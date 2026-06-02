# SPDX-License-Identifier: MIT
"""svd - parse CMSIS SVD files and decode register values into bit-fields.

Uses only the Python standard library (xml.etree); no cmsis-svd dependency.
"""

import glob
import os
import xml.etree.ElementTree as ET

from . import core

_svd_dir_cache = None     # resolved SVD folder (detected once)
_svd_periph_cache = {}    # {(svd_file, periph): (base, regs)}


def find_svd_dir():
    """Locate the folder that contains the .svd files."""
    global _svd_dir_cache
    if _svd_dir_cache is not None:
        return _svd_dir_cache
    for cand in core._SVD_DIR_CANDIDATES:
        if not cand or not os.path.isdir(cand):
            continue
        if glob.glob(os.path.join(cand, "STM32*.svd")):
            _svd_dir_cache = cand
            return cand
        hits = glob.glob(os.path.join(cand, "**", "*CMSIS_SVD"), recursive=True)
        for h in hits:
            if os.path.isdir(h) and glob.glob(os.path.join(h, "STM32*.svd")):
                _svd_dir_cache = h
                return h
    _svd_dir_cache = ""
    return ""


def pick_svd_file(device_name):
    """Pick the SVD file path that best matches a Device name.

    Returns (path, None) on success, or (None, error_message) on failure.
    """
    svd_dir = find_svd_dir()
    if not svd_dir:
        return None, "SVD folder not found. Set the STM32_SVD_DIR environment variable."

    files = [os.path.basename(p) for p in glob.glob(os.path.join(svd_dir, "STM32*.svd"))]
    if not files:
        return None, f"No SVD files in folder: {svd_dir}"

    dn = device_name.upper().replace(" ", "")
    token = dn.split("/")[0]

    def base_of(s):
        s = s.upper()
        return s[:-4] if s.endswith(".SVD") else s

    best, best_score = None, -1
    for f in files:
        fb = base_of(f)
        score = 0
        for a, b in zip(token, fb.replace("X", "")):
            if a == b:
                score += 1
            else:
                break
        if token.startswith(fb.replace("X", "")[:len(token)]):
            score += 1
        if "_CM7" in fb:
            score += 0.3
        if score > best_score:
            best, best_score = f, score

    if best is None or best_score <= 0:
        return None, (f"No SVD matched '{device_name}'.\n"
                      f"Folder: {svd_dir}\n"
                      "You can specify one directly: read_peripheral(..., svd='STM32xxxx').")
    return os.path.join(svd_dir, best), None


def parse_peripheral(svd_path, periph_name):
    """Extract a peripheral's register/field definitions from an SVD file.

    Returns (base_address, regs) on success, or (None, error_message) on failure,
    where regs = [(reg_name, offset, size, [(field, bit_offset, bit_width), ...]), ...].
    """
    key = (svd_path, periph_name.upper())
    if key in _svd_periph_cache:
        return _svd_periph_cache[key]

    target = periph_name.upper()
    found = None
    derived_from = None
    try:
        for _ev, elem in ET.iterparse(svd_path, events=("end",)):
            if elem.tag != "peripheral":
                continue
            nm = elem.find("name")
            if nm is None or not nm.text:
                elem.clear()
                continue
            if nm.text.upper() != target:
                elem.clear()
                continue
            base_el = elem.find("baseAddress")
            base = int(base_el.text, 0) if base_el is not None else 0
            regs = []
            for reg in elem.iter("register"):
                rn = reg.find("name")
                off = reg.find("addressOffset")
                if rn is None or off is None:
                    continue
                sz_el = reg.find("size")
                size = int(sz_el.text, 0) if sz_el is not None else 32
                fields = []
                for f in reg.iter("field"):
                    fn = f.find("name")
                    bo = f.find("bitOffset")
                    bw = f.find("bitWidth")
                    if bo is None or bw is None:
                        br = f.find("bitRange")
                        if fn is not None and br is not None and br.text:
                            hi, lo = br.text.strip("[]").split(":")
                            bo_v, bw_v = int(lo), int(hi) - int(lo) + 1
                        else:
                            continue
                    else:
                        bo_v, bw_v = int(bo.text), int(bw.text)
                    if fn is not None:
                        fields.append((fn.text, bo_v, bw_v))
                regs.append((rn.text, int(off.text, 0), size, fields))
            if not regs:
                derived_from = elem.get("derivedFrom")
            found = (base, regs)
            elem.clear()
            break
    except ET.ParseError as e:
        return None, f"SVD parse error: {e}"

    if found is None:
        return None, f"Peripheral '{periph_name}' not found in the SVD."

    base, regs = found
    if not regs and derived_from:
        src = parse_peripheral(svd_path, derived_from)
        if isinstance(src, tuple) and not isinstance(src[1], str):
            regs = src[1]

    result = (base, regs)
    _svd_periph_cache[key] = result
    return result


def decode_register(value, fields):
    """Split a register value into its fields. Returns [(field, value, bit_label), ...]."""
    out = []
    for fn, bo, bw in fields:
        mask = (1 << bw) - 1
        fv = (value >> bo) & mask
        bits = f"bit{bo}" if bw == 1 else f"bit{bo}-{bo + bw - 1}"
        out.append((fn, fv, bits))
    return out
