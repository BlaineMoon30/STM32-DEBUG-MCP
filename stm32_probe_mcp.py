# SPDX-License-Identifier: MIT
#
# STM32 Debug MCP Server
# Copyright (c) 2026 STM32 Debug MCP contributors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
STM32 Debug MCP Server - probe + build + flash + debug + SVD register decode
============================================================================
Tools:
  [diag]   check_setup, detect_chip
  [probe]  list_probes, probe_details
  [build]  build
  [flash]  flash, erase_chip
  [debug]  start_debug, stop_debug, set_breakpoint,
           run, cont, halt, reset, step, step_over, where,
           read_registers, read_memory
  [svd]    read_peripheral, list_peripherals  (decode register values into bit-fields)

Highlights:
  - Auto-detects OpenOCD / st_scripts / GDB / CubeProgrammer / SVD paths from the
    STM32CubeIDE and CubeCLT install folders (works across versions and PCs).
  - Detects the connected chip and selects the matching OpenOCD target cfg
    (covers a selection of STM32 families).
  - SVD parsing uses only the Python standard library (xml); no cmsis-svd needed.

Install:
    py -m pip install fastmcp pygdbmi

Environment variables (optional overrides):
    STM32_BUILD_DIR     : folder where the .elf is built (usually the only one to set)
    STM32_CUBEIDE_ROOT  : STM32CubeIDE install root
    STM32_SVD_DIR       : folder containing the .svd files
    STM32_PROGRAMMER_CLI / OPENOCD_BIN / OPENOCD_SCRIPTS / GDB_BIN : explicit paths

NOTE: Close any other program using the ST-Link (CubeIDE / CubeProgrammer GUI)
      before flashing or debugging, to avoid probe contention.
"""

import glob
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

from fastmcp import FastMCP

try:
    from pygdbmi.gdbcontroller import GdbController
except ImportError:
    GdbController = None

mcp = FastMCP("STM32 Probe Info")

# ====================================================================
# Path configuration (mostly auto-detected; override via env vars below)
# ====================================================================

# Candidate CubeIDE install roots to search for the bundled tools.
_CUBEIDE_CANDIDATES = [
    os.environ.get("STM32_CUBEIDE_ROOT", ""),
    r"C:/ST/STM32CubeIDE_2.1.1",
    r"C:/ST",
    r"C:/Program Files/STMicroelectronics",
    r"C:/Program Files (x86)/STMicroelectronics",
]

# Candidate folders that hold the .svd files (CMSIS_SVD). Override via STM32_SVD_DIR.
_SVD_DIR_CANDIDATES = [
    os.environ.get("STM32_SVD_DIR", ""),
    r"C:/ST/STM32CubeCLT_1.21.0/STMicroelectronics_CMSIS_SVD",
    r"C:/ST",  # fall back to scanning for a *_CMSIS_SVD folder underneath
]

# Build folder where the .elf is produced.
BUILD_DIR = os.environ.get(
    "STM32_BUILD_DIR",
    r"D:/__xxxProjPathxxx___/STM32CubeIDE/Debug",
)

GDB_PORT = 3333


def _glob_first(roots, pattern):
    """Return the first file matching `pattern` under any of `roots` (recursive)."""
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        hits = glob.glob(os.path.join(root, "**", pattern), recursive=True)
        if hits:
            # If several versioned folders exist, prefer the newest (last when sorted).
            hits.sort()
            return hits[-1]
    return None


def _resolve_paths():
    """Resolve OpenOCD / GDB / CubeProgrammer / scripts paths: env var, then auto-detect."""
    roots = [r for r in _CUBEIDE_CANDIDATES if r]

    cli = os.environ.get("STM32_PROGRAMMER_CLI") or shutil.which("STM32_Programmer_CLI") \
        or _glob_first(roots, "STM32_Programmer_CLI.exe")

    openocd = os.environ.get("OPENOCD_BIN") or _glob_first(roots, "openocd.exe")

    gdb = os.environ.get("GDB_BIN") or shutil.which("arm-none-eabi-gdb") \
        or _glob_first(roots, "arm-none-eabi-gdb.exe")

    # Scripts root: locate stm32h5x.cfg, then take its grandparent (parent of target/).
    scripts = os.environ.get("OPENOCD_SCRIPTS")
    if not scripts:
        h5cfg = _glob_first(roots, "stm32h5x.cfg")
        if h5cfg:
            # .../st_scripts/target/stm32h5x.cfg  ->  .../st_scripts
            scripts = os.path.dirname(os.path.dirname(h5cfg))

    return {
        "cli": cli,
        "openocd": openocd,
        "gdb": gdb,
        "scripts": scripts,
    }


PATHS = _resolve_paths()

# Global handles
_openocd = None
_gdb = None
_active_cfg = None      # last target cfg used (auto-detection cache)
_detected_device = ""   # last detected Device name (used to pick an SVD file)


# ====================================================================
# STM32 family -> OpenOCD target cfg mapping
# (based on the cfg files actually present under st_scripts/target/)
# ====================================================================
# Order matters: put more specific prefixes first (e.g. STM32L4+ vs L4).
# Matching uses the first prefix that the Device name starts with.
_CHIP_MAP = [
    # (Device-name prefix, target cfg, core, TrustZone)
    ("STM32C0",  "stm32c0x",      "Cortex-M0+", False),
    ("STM32C5",  "stm32c5x",      "Cortex-M33", True),
    ("STM32F0",  "stm32f0x",      "Cortex-M0",  False),
    ("STM32F1",  "stm32f1x",      "Cortex-M3",  False),
    ("STM32F2",  "stm32f2x",      "Cortex-M3",  False),
    ("STM32F3",  "stm32f3x",      "Cortex-M4",  False),
    ("STM32F4",  "stm32f4x",      "Cortex-M4",  False),
    ("STM32F7",  "stm32f7x",      "Cortex-M7",  False),
    ("STM32G0",  "stm32g0x",      "Cortex-M0+", False),
    ("STM32G4",  "stm32g4x",      "Cortex-M4",  False),
    ("STM32H5",  "stm32h5x",      "Cortex-M33", True),
    ("STM32H7",  "stm32h7x",      "Cortex-M7",  False),
    ("STM32L0",  "stm32l0x",      "Cortex-M0+", False),
    ("STM32L1",  "stm32l1x",      "Cortex-M3",  False),
    ("STM32L5",  "stm32l5x",      "Cortex-M33", True),
    ("STM32N6",  "stm32n6x",      "Cortex-M55", True),
    ("STM32U0",  "stm32u0x",      "Cortex-M0+", False),
    ("STM32U3",  "stm32u3x",      "Cortex-M33", True),
    ("STM32U5",  "stm32u5x",      "Cortex-M33", True),
    ("STM32V8",  "stm32v8x",      "Cortex-M85", True),
    # WB / WL families (also bundled; supported if present)
    ("STM32WBA", "stm32wbax",     "Cortex-M33", True),
    ("STM32WB0", "stm32wb0x",     "Cortex-M0+", False),
    ("STM32WB",  "stm32wbx",      "Cortex-M4",  False),
    ("STM32WL3", "stm32wl3x",     "Cortex-M0+", False),
    ("STM32WL",  "stm32wlx",      "Cortex-M4",  False),
]

# L4 and L4+ share the "STM32L4" prefix, so handle them separately.
# L4+ (high-density: P/Q/R/S sub-families) part numbers look like STM32L4[PQRS]xx.
_L4_PLUS_LETTERS = ("L4P", "L4Q", "L4R", "L4S")


def _map_chip(device_name: str):
    """Map a Device name (e.g. 'STM32H563', 'STM32L4R5') to (cfg, core, trustzone)."""
    if not device_name:
        return None
    dn = device_name.upper().replace(" ", "")

    # Distinguish L4 vs L4+ first.
    if dn.startswith("STM32L4"):
        marker = dn[5:8]  # e.g. 'L4R'
        if marker in _L4_PLUS_LETTERS:
            return ("stm32l4plusx", "Cortex-M4", False)
        return ("stm32l4x", "Cortex-M4", False)

    for prefix, cfg, core, tz in _CHIP_MAP:
        if dn.startswith(prefix):
            return (cfg, core, tz)
    return None


def _detect_device_name() -> str:
    """Read the Device name by connecting via CubeProgrammer (for auto-detection)."""
    global _detected_device
    out = _run_cli(["-c", "port=SWD", "mode=HOTPLUG"], timeout=60)
    # Example output: "Device name : STM32H563/H573" or "Device name : STM32H563ZITx"
    for line in out.splitlines():
        if "Device name" in line and ":" in line:
            _detected_device = line.split(":", 1)[1].strip()
            return _detected_device
    return ""


# ====================================================================
# SVD - decode peripheral register values into their bit-field meanings
# ====================================================================
_svd_dir_cache = None     # resolved SVD folder (detected once)
_svd_periph_cache = {}    # {(svd_file, periph): (base, [(reg, off, size, [(field, bo, bw)])])}


def _find_svd_dir():
    """Locate the folder that contains the .svd files."""
    global _svd_dir_cache
    if _svd_dir_cache is not None:
        return _svd_dir_cache
    for cand in _SVD_DIR_CANDIDATES:
        if not cand or not os.path.isdir(cand):
            continue
        # If STM32*.svd files sit directly in this folder, use it.
        if glob.glob(os.path.join(cand, "STM32*.svd")):
            _svd_dir_cache = cand
            return cand
        # Otherwise scan for a *_CMSIS_SVD subfolder.
        hits = glob.glob(os.path.join(cand, "**", "*CMSIS_SVD"), recursive=True)
        for h in hits:
            if os.path.isdir(h) and glob.glob(os.path.join(h, "STM32*.svd")):
                _svd_dir_cache = h
                return h
    _svd_dir_cache = ""
    return ""


def _pick_svd_file(device_name: str):
    """Pick the SVD file path that best matches a Device name.

    Returns (path, None) on success, or (None, error_message) on failure.
    SVD file names are part-number based but may contain wildcards (STM32F0x1)
    or core suffixes (_CM7), so candidates are scored and the best match is chosen.
    """
    svd_dir = _find_svd_dir()
    if not svd_dir:
        return None, "SVD folder not found. Set the STM32_SVD_DIR environment variable."

    files = [os.path.basename(p) for p in glob.glob(os.path.join(svd_dir, "STM32*.svd"))]
    if not files:
        return None, f"No SVD files in folder: {svd_dir}"

    # Extract the primary part token from the Device name: 'STM32H563/H573' -> 'STM32H563'.
    dn = device_name.upper().replace(" ", "")
    token = dn.split("/")[0]

    def base_of(s):
        s = s.upper()
        if s.endswith(".SVD"):
            s = s[:-4]
        return s

    # Score = how long the file name matches the token as a prefix.
    best, best_score = None, -1
    for f in files:
        fb = base_of(f)
        score = 0
        # Compare char by char; treat 'X' (wildcard) loosely by stripping it.
        for a, b in zip(token, fb.replace("X", "")):
            if a == b:
                score += 1
            else:
                break
        # Bonus for an exact prefix match.
        if token.startswith(fb.replace("X", "")[:len(token)]):
            score += 1
        # For dual-core parts (_CM7 etc.), slightly prefer the application core (CM7).
        if "_CM7" in fb:
            score += 0.3
        if score > best_score:
            best, best_score = f, score

    if best is None or best_score <= 0:
        return None, (f"No SVD matched '{device_name}'.\n"
                      f"Folder: {svd_dir}\n"
                      "You can specify one directly: read_peripheral(..., svd='STM32xxxx').")
    return os.path.join(svd_dir, best), None


def _parse_peripheral(svd_path, periph_name):
    """Extract a peripheral's register/field definitions from an SVD file.

    Uses iterparse to handle large SVD files (tens of MB) efficiently.
    Returns (base_address, regs) on success, or (None, error_message) on failure,
    where regs = [(reg_name, offset, size, [(field_name, bit_offset, bit_width), ...]), ...].
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
                    # Also support the "[hi:lo]" bitRange form.
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
            # Some peripherals inherit registers via derivedFrom (e.g. SPI2 from SPI1).
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
    # Resolve derivedFrom: if empty, inherit the source peripheral's registers
    # (e.g. SPI2 derives from SPI1) while keeping this peripheral's base address.
    if not regs and derived_from:
        src = _parse_peripheral(svd_path, derived_from)
        if isinstance(src, tuple) and not isinstance(src[1], str):
            regs = src[1]

    result = (base, regs)
    _svd_periph_cache[key] = result
    return result


def _decode_register(value: int, fields):
    """Split a register value into its fields. Returns [(field, value, bit_label), ...]."""
    out = []
    for fn, bo, bw in fields:
        mask = (1 << bw) - 1
        fv = (value >> bo) & mask
        bits = f"bit{bo}" if bw == 1 else f"bit{bo}-{bo + bw - 1}"
        out.append((fn, fv, bits))
    return out


# ====================================================================
# Generic process / CLI helpers
# ====================================================================
def _run(cmd, cwd=None, timeout=180):
    """Run a command and return combined stdout + stderr (never raises)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return f"Error: command timed out ({timeout}s)"
    except FileNotFoundError:
        return f"Error: executable not found: {cmd[0]}"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def _run_cli(args, timeout=120):
    """Run STM32_Programmer_CLI with the given arguments."""
    cli = PATHS.get("cli")
    if not cli or not os.path.exists(cli):
        return "Error: STM32_Programmer_CLI not found. Set STM32_PROGRAMMER_CLI."
    return _run([cli, *args], timeout=timeout)


def _find_elf():
    """Find the first .elf in BUILD_DIR, returned as a normalized forward-slash path."""
    if not os.path.isdir(BUILD_DIR):
        return None
    for f in os.listdir(BUILD_DIR):
        if f.lower().endswith(".elf"):
            # Normalize to an absolute forward-slash path to avoid GDB path-parsing
            # issues from mixed Windows separators (/ and \).
            full = os.path.abspath(os.path.join(BUILD_DIR, f))
            return full.replace("\\", "/")
    return None


def _gdb_cmd(command, timeout=20):
    """Send a GDB/MI command and return the relevant response lines as text."""
    if _gdb is None:
        return "Error: no debug session. Call start_debug first."
    try:
        try:
            resp = _gdb.write(command, timeout_sec=timeout, raise_error_on_timeout=False)
        except TypeError:
            # Older pygdbmi: raise_error_on_timeout argument not supported.
            resp = _gdb.write(command, timeout_sec=timeout)
    except Exception as e:  # noqa: BLE001
        # Treat timeouts as non-fatal: a running target may not respond.
        return f"(no response / target running - {type(e).__name__})"
    lines = []
    for m in resp:
        t = m.get("type")
        payload = m.get("payload")
        if t in ("result", "notify", "console") and payload:
            lines.append(f"[{t}] {payload}")
    return "\n".join(lines)[:3000] or "(no response)"


def _read_word(address: int):
    """Read a single 32-bit word from target memory as an int (None on failure)."""
    resp = _gdb_cmd(f"-data-read-memory {hex(address)} x 4 1 1", timeout=10)
    # Extract a "0x........" value from the response.
    m = re.search(r'data=\["?(0x[0-9a-fA-F]+)', resp)
    if not m:
        m = re.search(r'(0x[0-9a-fA-F]{1,8})', resp)
    if m:
        try:
            return int(m.group(1), 0)
        except ValueError:
            return None
    return None


# ====================================================================
# Diagnostics - verify paths before debugging
# ====================================================================
@mcp.tool
def check_setup() -> str:
    """자동 탐색된 도구 경로들을 보여줍니다 (문제 진단용).
    Show the auto-detected tool paths (for troubleshooting).

    사용 예 / Use for: "설정 확인해줘", "경로 제대로 잡혔어?",
    "check the setup", "are the paths correct?".
    디버깅이 안 될 때 가장 먼저 호출해 어떤 경로가 비었는지 확인하세요.
    Call this first when debugging fails, to see which path is missing.
    """
    lines = ["=== Auto-detected paths ==="]
    for k, label in [("cli", "CubeProgrammer CLI"), ("openocd", "OpenOCD"),
                     ("gdb", "arm-none-eabi-gdb"), ("scripts", "OpenOCD scripts")]:
        v = PATHS.get(k)
        ok = "OK" if (v and os.path.exists(v)) else "MISSING"
        lines.append(f"{label}: {ok}\n   {v}")
    lines.append(f"\nBUILD_DIR: {'OK' if os.path.isdir(BUILD_DIR) else 'MISSING'}\n   {BUILD_DIR}")
    elf = _find_elf()
    lines.append(f"ELF: {'OK ' + elf if elf else 'MISSING (no .elf in BUILD_DIR)'}")
    lines.append(f"pygdbmi: {'installed' if GdbController else 'MISSING - py -m pip install pygdbmi'}")
    return "\n".join(lines)


# ====================================================================
# probe / build / flash
# ====================================================================
@mcp.tool
def list_probes() -> str:
    """연결된 ST-Link probe 목록(SN, 펌웨어)을 조회합니다.
    List connected ST-Link probes (serial number, firmware).

    사용 예 / Use for: "연결된 probe 알려줘", "어떤 보드 연결됐어?",
    "what device/board/probe is connected?".
    """
    return _run_cli(["-l"])


@mcp.tool
def probe_details() -> str:
    """연결된 STM32 칩 상세(보드명/전압/Device ID/Flash/CPU)를 읽습니다.
    Read connected-chip details (board / voltage / Device ID / Flash / CPU).

    사용 예 / Use for: "어떤 칩이야?", "보드 사양 알려줘",
    "what chip is this", "board specs".
    HOTPLUG 모드라 실행 중인 펌웨어를 건드리지 않습니다.
    HOTPLUG: running firmware is left untouched.
    """
    return _run_cli(["-c", "port=SWD", "mode=HOTPLUG"])


@mcp.tool
def build() -> str:
    """CubeIDE 프로젝트를 빌드(make)해서 .elf 를 생성합니다.
    Build the CubeIDE project (make) to produce the .elf.

    사용 예 / Use for: "빌드해줘", "컴파일해줘", "build", "compile".
    """
    if not os.path.isdir(BUILD_DIR):
        return f"Error: build folder not found: {BUILD_DIR}"
    out = _run(["make", "-j4", "all"], cwd=BUILD_DIR, timeout=600)
    elf = _find_elf()
    return out + (f"\n\nProduced ELF: {elf}" if elf else "\n\nWarning: no .elf found")


@mcp.tool
def flash(elf_path: str = "", run_after: bool = True, full_erase: bool = False) -> str:
    """빌드된 .elf 를 보드에 플래시합니다.
    Flash the built .elf to the board.

    사용 예 / Use for: "플래시해줘", "보드에 구워줘", "업로드해줘",
    "flash the board", "upload firmware".
    run_after=False 면 플래시 후 멈춘 채로 둡니다(디버깅 직전에 유용).
    run_after=False leaves the target halted after flashing (handy before debugging).

    Erase 동작 / Erase behavior:
      - 기본: 쓰는 영역만 자동 erase 후 기록(빠름, 보통 충분).
        Default: only the written region is auto-erased (fast; usually enough).
      - full_erase=True: 쓰기 전에 칩 전체 erase(-e all).
        "전체 지우고 플래시", "칩 초기화 후 굽기", "full erase" 요청 시 사용.
        이전 펌웨어가 남긴 데이터까지 지웁니다(느림).
        full_erase=True erases the whole chip first; clears leftover data (slower).

    참고/Note: .elf 는 배치 주소를 포함하므로 시작 주소를 지정하지 않습니다
    (주소 지정은 .bin 일 때만 필요). An .elf embeds load addresses, so no start
    address is given; addresses are only needed for raw .bin files.

    Args:
        elf_path: 플래시할 .elf (비우면 BUILD_DIR 자동 탐색) / .elf to flash.
        run_after: True 면 플래시 후 리셋·실행 / reset and run after flashing.
        full_erase: True 면 쓰기 전 전체 칩 erase / full chip erase before writing.
    """
    elf = elf_path or _find_elf()
    if not elf or not os.path.exists(elf):
        return f"Error: .elf not found ({elf!r})"
    args = ["-c", "port=SWD"]
    if full_erase:
        args += ["-e", "all"]          # full chip erase (on request)
    args += ["-w", elf, "-v"]          # write + verify (includes auto sector erase)
    if run_after:
        args.append("-rst")
    out = _run_cli(args, timeout=240)
    mode = "full-erase then flash" if full_erase else "flash (auto-erase)"
    run_s = " + reset/run" if run_after else " (halted)"
    return f"[{mode}{run_s}]  ELF: {elf}\n\n{out}"


@mcp.tool
def erase_chip() -> str:
    """칩 전체 Flash를 완전히 지웁니다(쓰기 없이 erase만).
    Fully erase the chip's Flash (erase only, no write).

    사용 예 / Use for: "전체 지워줘", "칩 초기화해줘", "Flash 전부 지워",
    "erase everything", "erase all Flash".
    펌웨어를 쓰지 않고 비우기만 합니다(이후 flash 로 기록).
    Empties Flash without writing firmware (follow with flash to program).
    쓰면서 지우려면 flash(full_erase=True) 를 사용하세요.
    To erase while writing, use flash(full_erase=True).
    """
    return _run_cli(["-c", "port=SWD", "-e", "all"], timeout=180)


# ====================================================================
# Chip detection
# ====================================================================
@mcp.tool
def detect_chip() -> str:
    """연결된 STM32 칩을 감지하고, 매칭되는 OpenOCD target cfg 를 알려줍니다.
    Detect the connected STM32 chip and report the matching OpenOCD target cfg.

    사용 예 / Use for: "무슨 칩이야?", "어떤 cfg 써야 해?", "칩 감지해줘",
    "what chip is this", "which cfg should I use", "detect the chip".
    디버그 세션을 시작하지 않고 감지만 합니다(start_debug 전 확인용).
    Detection only; does not start a debug session (handy before start_debug).
    """
    dev = _detect_device_name()
    if not dev:
        return "Could not detect a chip. Check the board connection / ST-Link contention."
    mapped = _map_chip(dev)
    if not mapped:
        return (f"Device name: {dev}\n-> No matching cfg. "
                "Specify manually with start_debug(chip='stm32xxx').")
    cfg, core, tz = mapped
    scripts = PATHS.get("scripts") or ""
    cfg_full = os.path.join(scripts, "target", cfg + ".cfg")
    exists = "present" if os.path.exists(cfg_full) else "NOT in this OpenOCD"
    return (f"Device name : {dev}\n"
            f"target cfg  : {cfg}.cfg ({exists})\n"
            f"core        : {core}\n"
            f"TrustZone   : {'yes' if tz else 'no'}")


# ====================================================================
# Debugging (OpenOCD + GDB)
# ====================================================================
@mcp.tool
def start_debug(elf_path: str = "", chip: str = "") -> str:
    """디버그 세션 시작: 칩 자동 감지 -> OpenOCD(gdbserver:3333) -> GDB 연결 -> 심볼 로드.
    Start a debug session: detect chip -> OpenOCD (gdbserver:3333) -> GDB -> load symbols.

    사용 예 / Use for: "디버그 세션 시작해줘", "디버깅 시작",
    "start debugging", "open a debug session".
    연결된 STM32 칩을 자동 감지해 알맞은 target cfg 를 고릅니다
    (지원: C0/C5, F0-F7, G0/G4, H5/H7, L0-L5, N6, U0/U3/U5, V8, WB/WL).
    Auto-detects the chip and picks the matching target cfg.

    Args:
        elf_path: 디버그할 .elf (비우면 BUILD_DIR 자동 탐색) / .elf to debug.
        chip: 계열 강제 지정(예: "stm32f4x"), 비우면 자동 감지 / force a family.
    """
    global _openocd, _gdb, _active_cfg
    if GdbController is None:
        return "Error: pygdbmi not installed. Run 'py -m pip install pygdbmi' and restart."
    if _openocd is not None:
        return "A debug session is already running. Call stop_debug first."

    openocd = PATHS.get("openocd")
    scripts = PATHS.get("scripts")
    gdb = PATHS.get("gdb")
    miss = [n for n, v in [("OpenOCD", openocd), ("scripts", scripts), ("gdb", gdb)]
            if not v or not os.path.exists(v)]
    if miss:
        return f"Error: unresolved paths -> {', '.join(miss)}. Run check_setup."

    elf = elf_path or _find_elf()
    if not elf or not os.path.exists(elf):
        return f"Error: .elf not found ({elf!r})"

    # -- Decide chip -> target cfg --
    info_line = ""
    if chip:
        target_cfg = chip if chip.endswith(".cfg") else f"{chip}.cfg"
        info_line = f"chip (manual): {target_cfg}"
    else:
        dev = _detect_device_name()
        mapped = _map_chip(dev)
        if not mapped:
            return (
                f"Error: chip auto-detection failed (Device name: {dev!r}).\n"
                "Check the board connection, or specify chip directly "
                "(e.g. chip='stm32f4x')."
            )
        cfg_base, core, tz = mapped
        # Verify the cfg file actually exists.
        cfg_full = os.path.join(scripts, "target", cfg_base + ".cfg")
        if not os.path.exists(cfg_full):
            return (f"Error: {cfg_base}.cfg not found under OpenOCD scripts/target.\n"
                    f"   ({cfg_full})\nCheck whether this CubeIDE/OpenOCD supports the chip.")
        target_cfg = cfg_base + ".cfg"
        info_line = f"auto-detected: {dev}  ->  {target_cfg}  ({core}{', TrustZone' if tz else ''})"

    _active_cfg = target_cfg

    # -- Launch OpenOCD (interface unified to stlink-dap + dapdirect_swd) --
    cmd = [
        openocd, "-s", scripts,
        "-f", "interface/stlink-dap.cfg",
        "-c", "transport select dapdirect_swd",
        "-f", f"target/{target_cfg}",
    ]
    try:
        _openocd = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:  # noqa: BLE001
        _openocd = None
        return f"Error: failed to launch OpenOCD - {e}"
    time.sleep(2.0)
    if _openocd.poll() is not None:
        err = _openocd.stderr.read().decode(errors="ignore")
        _openocd = None
        return (
            f"Error: OpenOCD exited immediately (cfg: {target_cfg}).\n"
            "Usually a cfg name/path issue or ST-Link contention.\n"
            "(Make sure CubeIDE / CubeProgrammer GUI are closed.)\n\n" + err[:1800]
        )

    try:
        _gdb = GdbController(command=[gdb, "--interpreter=mi3"])
    except Exception as e:  # noqa: BLE001
        stop_debug()
        return f"Error: failed to launch GDB - {e}"

    # Enable async mode so the target can be interrupted (-exec-interrupt) while running.
    _gdb_cmd("-gdb-set mi-async on")
    _gdb_cmd("-gdb-set non-stop off")

    # Pass the ELF path to GDB/MI safely (forward slashes, quoted).
    elf_norm = os.path.abspath(elf).replace("\\", "/")
    sym = _gdb_cmd(f'-file-exec-and-symbols "{elf_norm}"')
    tgt = _gdb_cmd(f"-target-select extended-remote localhost:{GDB_PORT}")

    warn = ""
    if "No such file" in sym or "error" in sym.lower():
        warn = ("\n\nWarning: symbol load may have failed. Check the ELF path:\n"
                f"   {elf_norm}\n   (verify the file exists and the path is correct)")

    out = [sym, tgt]
    return (f"Debug session started.\n{info_line}\nELF: {elf_norm}\n"
            f"gdbserver: localhost:{GDB_PORT}\n\n" + "\n".join(out) + warn)


@mcp.tool
def stop_debug() -> str:
    """디버그 세션(GDB + OpenOCD)을 종료합니다.
    Stop the debug session (GDB + OpenOCD).

    사용 예 / Use for: "디버그 종료해줘", "세션 닫아줘", "stop debugging".
    """
    global _openocd, _gdb
    msg = []
    if _gdb is not None:
        try:
            _gdb.exit()
        except Exception:  # noqa: BLE001, S110
            pass
        _gdb = None
        msg.append("GDB stopped")
    if _openocd is not None:
        try:
            _openocd.terminate()
            _openocd.wait(timeout=5)
        except Exception:  # noqa: BLE001, S110
            pass
        _openocd = None
        msg.append("OpenOCD stopped")
    return ", ".join(msg) or "No session to stop."


@mcp.tool
def set_breakpoint(location: str) -> str:
    """브레이크포인트를 설정합니다. 예: "main.c:142" 또는 "HAL_SPI_IRQHandler".
    Set a breakpoint. Examples: "main.c:142" or "HAL_SPI_IRQHandler".

    사용 예 / Use for: "main에 브레이크포인트 걸어줘", "set a breakpoint at main".
    HardFault 디버깅이면 HardFault_Handler 에 거는 것이 유용합니다.
    For fault debugging, breaking at HardFault_Handler is useful.
    """
    return _gdb_cmd(f"-break-insert {location}")


@mcp.tool
def halt() -> str:
    """자유 실행 중인 타깃을 강제로 멈춥니다(브레이크포인트 없이).
    Force-halt a freely running target (no breakpoint needed).

    사용 예 / Use for: "멈춰줘", "지금 정지시켜", "실행 중인데 세워줘",
    "halt", "stop now", "freeze the target".
    멈춘 뒤 where / read_registers / read_memory 로 상태를 봅니다.
    After halting, inspect with where / read_registers / read_memory.
    SPI 등 타이밍 민감 코드는 halt가 동작을 교란할 수 있어, 정상 동작 관찰엔
    멈추지 말고 read_memory 라이브 읽기를 권합니다.
    For timing-sensitive code, prefer live memory reads over halting.
    """
    # In async mode (mi-async on), this is the proper way to stop a running target.
    # 'monitor halt' can block GDB while running, so -exec-interrupt is used.
    out = _gdb_cmd("-exec-interrupt", timeout=10)
    time.sleep(0.3)  # brief wait for the halt to take effect
    loc = _gdb_cmd("-stack-info-frame", timeout=10)
    return f"[halt]\n{out}\n---\n{loc}"


@mcp.tool
def reset(halt_after: bool = True) -> str:
    """타깃을 리셋합니다.
    Reset the target.

    사용 예 / Use for: "리셋해줘", "재시작해줘", "reset", "restart".
    halt_after=True(기본): 리셋 후 멈춘 상태 유지(브레이크포인트 걸기 좋음).
    halt_after=True (default): reset and stay halted.
    halt_after=False: 리셋 후 바로 실행 / reset and run immediately.
    """
    if halt_after:
        return _gdb_cmd("monitor reset halt")
    return _gdb_cmd("monitor reset run")


@mcp.tool
def run() -> str:
    """타깃을 리셋하고 실행합니다(설정된 브레이크포인트까지 진행).
    Reset the target and run (until a configured breakpoint is hit).

    사용 예 / Use for: "실행해줘", "리셋 후 실행", "처음부터 돌려줘",
    "run", "reset and run".
    리셋 벡터부터 시작하므로 main 등 시작부 브레이크포인트에 정상적으로 걸립니다.
    Starts from the reset vector, so startup breakpoints (e.g. main) hit.
    실행 도중 멈추려면 halt 를 사용하세요 / To stop mid-execution, use halt.
    """
    _gdb_cmd("monitor reset halt", timeout=10)
    # In async mode, continue returns immediately; it stops if a breakpoint is hit.
    out = _gdb_cmd("-exec-continue", timeout=10)
    time.sleep(0.5)
    # Check whether it stopped right away (breakpoint).
    loc = _gdb_cmd("-stack-info-frame", timeout=5)
    return f"[run]\n{out}\n---\ncurrent state:\n{loc}"


@mcp.tool
def cont() -> str:
    """실행을 계속합니다(다음 브레이크포인트까지). 실행 중 멈추려면 halt 사용.
    Continue execution (until the next breakpoint). Use halt to stop while running.

    사용 예 / Use for: "계속해줘", "이어서 실행", "continue", "resume".
    """
    out = _gdb_cmd("-exec-continue", timeout=10)
    time.sleep(0.5)
    loc = _gdb_cmd("-stack-info-frame", timeout=5)
    return f"[continue]\n{out}\n---\ncurrent state:\n{loc}"


@mcp.tool
def step() -> str:
    """소스 한 줄을 실행합니다(함수 호출 시 안으로 진입).
    Step one source line (stepping into function calls).

    사용 예 / Use for: "한 줄 실행", "스텝 인", "step", "step into".
    """
    return _gdb_cmd("-exec-step")


@mcp.tool
def step_over() -> str:
    """소스 한 줄을 실행합니다(함수 호출은 건너뜀).
    Step one source line (stepping over function calls).

    사용 예 / Use for: "한 줄 넘어가기", "스텝 오버", "step over".
    """
    return _gdb_cmd("-exec-next")


@mcp.tool
def where() -> str:
    """현재 멈춘 위치(파일:라인, 함수, 스택 프레임)를 보여줍니다.
    Show the current stop location (file:line, function, stack frames).

    사용 예 / Use for: "지금 어디서 멈췄어?", "콜스택 보여줘", "where am I".
    """
    return _gdb_cmd("-stack-info-frame") + "\n---\n" + _gdb_cmd("-stack-list-frames")


@mcp.tool
def read_registers() -> str:
    """코어 레지스터(r0-r15, xPSR 등)를 덤프합니다. 멈춘 상태에서 호출하세요.
    Dump the core registers (r0-r15, xPSR, etc.). Call while halted.

    사용 예 / Use for: "레지스터 보여줘", "show registers".
    """
    return f"[names]\n{_gdb_cmd('-data-list-register-names')}\n\n" \
           f"[values]\n{_gdb_cmd('-data-list-register-values x')}"


@mcp.tool
def read_memory(address: str, count: int = 16) -> str:
    """메모리를 그대로 읽습니다 (페리페럴 레지스터 확인 등).
    Read raw memory (e.g. to inspect peripheral registers).

    사용 예 / Use for: "0x40013000 메모리 읽어줘", "read memory at ...".
    address 예: "0x40013000"(SPI1 베이스) 또는 "&myvar". count = 워드(4B) 개수.
    address example: "0x40013000" (SPI1 base) or "&myvar". count = words (4B).
    """
    return _gdb_cmd(f"-data-read-memory {address} x 4 1 {count}")


# ====================================================================
# Live watch (non-intrusive) + watchpoints (data breakpoints)
# ====================================================================
def _eval_expr(expr: str):
    """Evaluate a C expression / variable via GDB/MI and return its value as text.

    Works on a running target too (SWD memory access is non-intrusive),
    as long as the expression resolves to a global/static address.
    """
    resp = _gdb_cmd(f'-data-evaluate-expression "{expr}"', timeout=8)
    # _gdb_cmd renders the payload as a Python dict repr, so 'value' may appear
    # as value="..." (MI text) or 'value': '...' (dict repr). Handle both.
    m = re.search(r'value="(.*?)"(?:[,}]|$)', resp)
    if m:
        return m.group(1).replace('\\"', '"')
    m = re.search(r"'value':\s*'(.*?)'(?:[,}]|$)", resp)
    if m:
        return m.group(1)
    m = re.search(r"'value':\s*\"(.*?)\"(?:[,}]|$)", resp)
    if m:
        return m.group(1)
    return resp  # fall back to raw response


@mcp.tool
def watch_read(expressions: list[str]) -> str:
    """변수/표현식들의 현재 값을 한 번 읽습니다 (CPU를 멈추지 않음).
    Read the current value of variables/expressions once (without halting the CPU).

    사용 예 / Use for: "sensor_value 지금 값 보여줘", "이 변수들 라이브로 읽어줘",
    "watch these variables", "read live values".
    전역/정적 변수는 실행 중에도 읽힙니다(SWD 비침습 접근).
    Globals/statics can be read even while running (non-intrusive SWD access).

    Args:
        expressions: 변수명/표현식 목록 (예: ["counter", "adc[0]", "g_state.mode"]).
                     C expressions or variable names.
    """
    if _gdb is None:
        return "오류/Error: no debug session. Call start_debug first."
    if not expressions:
        return "오류/Error: provide at least one expression."
    lines = ["=== live watch ==="]
    for ex in expressions:
        val = _eval_expr(ex)
        lines.append(f"  {ex} = {val}")
    return "\n".join(lines)


@mcp.tool
def watch_sample(expression: str, count: int = 10, interval_ms: int = 200) -> str:
    """한 변수/표현식을 여러 번 샘플링해 값의 변화(추이)를 보여줍니다 (멈추지 않음).
    Sample one expression repeatedly to show how its value changes over time (no halt).

    사용 예 / Use for: "counter 값 변하는지 지켜봐줘", "sensor 추이 보여줘",
    "sample this variable over time", "watch how X changes".
    실행 중인 타깃을 멈추지 않고 주기적으로 읽어 추세를 잡습니다.
    Periodically reads a running target to capture a trend.

    Args:
        expression: 관찰할 변수/표현식 / variable or expression to sample.
        count: 샘플 횟수 (기본 10, 최대 60) / number of samples.
        interval_ms: 샘플 간격 ms (기본 200) / sampling interval in ms.
    """
    if _gdb is None:
        return "오류/Error: no debug session. Call start_debug first."
    count = max(1, min(count, 60))     # cap to avoid long blocking
    interval = max(10, interval_ms) / 1000.0
    samples = []
    for i in range(count):
        val = _eval_expr(expression)
        samples.append(val)
        if i < count - 1:
            time.sleep(interval)
    lines = [f"=== sample: {expression}  ({count} samples, {interval_ms}ms) ==="]
    for i, v in enumerate(samples):
        lines.append(f"  [{i:>2}] {v}")
    # Quick change summary
    uniq = list(dict.fromkeys(samples))
    if len(uniq) == 1:
        lines.append(f"\n변화 없음 / unchanged: {uniq[0]}")
    else:
        lines.append(f"\n변화 감지 / changed: {samples[0]} -> {samples[-1]} "
                     f"({len(uniq)} distinct values)")
    return "\n".join(lines)


@mcp.tool
def set_watchpoint(expression: str, mode: str = "write") -> str:
    """워치포인트(데이터 브레이크포인트)를 겁니다 — 값이 바뀌면 자동으로 멈춥니다.
    Set a watchpoint (data breakpoint) — the target halts when the value changes/accesses.

    사용 예 / Use for: "이 변수 바뀌면 멈춰줘", "g_flag 망가지는 지점 잡아줘",
    "break when this variable changes", "watch for writes to X".
    값이 어디서·언제 변경되는지(메모리 오염, 예상 못한 덮어쓰기) 추적에 강력합니다.
    Great for finding where/when a value gets modified (memory corruption).
    이후 run/cont 로 실행하면 해당 접근 시점에 멈춥니다.
    After this, run/cont until the access occurs.

    주의/Note: ARM 하드웨어 워치포인트는 개수 제한(보통 4개)이 있습니다.
    ARM hardware watchpoints are limited in number (often 4).

    Args:
        expression: 감시할 변수/주소 (예: "g_state", "*(int*)0x20001000").
        mode: "write"(쓰기 시), "read"(읽기 시), "access"(둘 다). 기본 write.
    """
    if _gdb is None:
        return "오류/Error: no debug session. Call start_debug first."
    m = mode.lower()
    if m == "write":
        cmd = f"-break-watch {expression}"          # write watchpoint
    elif m == "read":
        cmd = f"-break-watch -r {expression}"       # read watchpoint
    elif m == "access":
        cmd = f"-break-watch -a {expression}"       # access (read or write)
    else:
        return "오류/Error: mode must be 'write', 'read', or 'access'."
    out = _gdb_cmd(cmd)
    return f"[watchpoint: {m} on {expression}]\n{out}\n\n" \
           f"run/cont 로 실행하면 해당 접근 시 멈춥니다 / run or cont until it triggers."


@mcp.tool
def list_watchpoints() -> str:
    """설정된 브레이크포인트/워치포인트 목록을 보여줍니다.
    List all configured breakpoints and watchpoints.

    사용 예 / Use for: "워치포인트 목록 보여줘", "뭐 걸려있어?",
    "list watchpoints", "show breakpoints".
    삭제하려면 번호로 delete_breakpoint 를 사용하세요.
    To remove one, use delete_breakpoint with its number.
    """
    return _gdb_cmd("-break-list")


@mcp.tool
def delete_breakpoint(number: int) -> str:
    """번호로 브레이크포인트/워치포인트를 삭제합니다.
    Delete a breakpoint/watchpoint by its number.

    사용 예 / Use for: "2번 워치포인트 지워줘", "delete breakpoint 2".
    번호는 list_watchpoints 로 확인 / find numbers via list_watchpoints.
    """
    return _gdb_cmd(f"-break-delete {number}")


# ====================================================================
# SVD register decoding
# ====================================================================
@mcp.tool
def read_peripheral(name: str, svd: str = "") -> str:
    """페리페럴의 레지스터를 SVD로 디코딩해 '비트 의미'까지 보여줍니다.
    Read a peripheral's registers and decode them into their bit-field meanings.

    사용 예 / Use for: "SPI1 레지스터 해석해줘", "SPI1 상태 비트 보여줘",
    "decode the SPI1 registers", "show SPI1 status bits".
    예: read_peripheral("SPI1") -> CR1/CR2/SR 등을 CPOL/CPHA/OVR/BSY 로 분해.
    디버그 세션이 필요합니다(멈춘 상태 권장).
    Requires a debug session (preferably while halted).

    폴트/페리페럴 힌트 / Fault & peripheral hints:
      - HardFault: read_peripheral("SCB") 로 CFSR/HFSR, BFAR/MMFAR
        (폴트 원인과 폴트 주소)를 디코딩.
        For a HardFault, "SCB" decodes CFSR/HFSR and BFAR/MMFAR.
      - SPI: SR.OVR=1 이면 수신을 늦게 읽어 데이터 유실; CR1.CPOL/CPHA 불일치 시 깨짐.
        SPI: SR.OVR=1 = RX read too late; CR1.CPOL/CPHA mismatch corrupts data.

    Args:
        name: 페리페럴 이름 (예: "SCB", "SPI1", "USART2", "GPIOA", "TIM2").
        svd: SVD 품번 강제 지정(예: "STM32H563"), 비우면 칩에서 자동 선택.
    """
    if _gdb is None:
        return "Error: no debug session. Call start_debug first."

    # Select the SVD file.
    if svd:
        svd_dir = _find_svd_dir()
        svd_path = os.path.join(svd_dir, svd if svd.endswith(".svd") else svd + ".svd")
        if not os.path.exists(svd_path):
            return f"Error: specified SVD file not found: {svd_path}"
    else:
        dev = _detected_device or _detect_device_name()
        if not dev:
            return "Error: chip not detected, cannot pick an SVD. Specify the svd argument."
        svd_path, err = _pick_svd_file(dev)
        if err:
            return f"Error: {err}"

    parsed = _parse_peripheral(svd_path, name)
    if isinstance(parsed, tuple) and isinstance(parsed[1], str):
        return f"Error: {parsed[1]}"
    if parsed is None:
        return f"Error: peripheral '{name}' not found in the SVD."

    base, regs = parsed
    if not regs:
        return f"'{name}' has no register definitions (SVD: {os.path.basename(svd_path)})."

    lines = [f"=== {name} @ {hex(base)}  (SVD: {os.path.basename(svd_path)}) ==="]
    # Cap how many registers we read at once.
    MAX_REGS = 24
    shown = 0
    for reg_name, off, size, fields in regs:
        if shown >= MAX_REGS:
            lines.append(f"... ({len(regs) - shown} more registers omitted)")
            break
        addr = base + off
        val = _read_word(addr)
        if val is None:
            lines.append(f"\n{reg_name} @ {hex(addr)} : (read failed)")
            shown += 1
            continue
        lines.append(f"\n{reg_name} @ {hex(addr)} = 0x{val:08X}")
        decoded = _decode_register(val, fields)
        # Show all fields; mark non-zero ones for quick scanning.
        for fn, fv, bits in decoded:
            mark = " <--" if fv != 0 else ""
            lines.append(f"    {fn:<12} = {fv}   ({bits}){mark}")
        shown += 1

    lines.append("\n(<-- marks non-zero fields - inspect these first)")
    return "\n".join(lines)[:6000]


@mcp.tool
def list_peripherals(svd: str = "") -> str:
    """현재 칩 SVD에 정의된 페리페럴 이름 목록을 보여줍니다.
    List the peripheral names defined in the current chip's SVD.

    사용 예 / Use for: "어떤 페리페럴 있어?", "페리페럴 목록 보여줘",
    "what peripherals are there", "list peripherals".
    read_peripheral 에 넣을 정확한 이름을 확인할 때 유용합니다.
    Helpful to find the exact name to pass to read_peripheral.
    """
    if svd:
        svd_dir = _find_svd_dir()
        svd_path = os.path.join(svd_dir, svd if svd.endswith(".svd") else svd + ".svd")
    else:
        dev = _detected_device or _detect_device_name()
        if not dev:
            return "Error: chip not detected. Specify the svd argument or run detect_chip first."
        svd_path, err = _pick_svd_file(dev)
        if err:
            return f"Error: {err}"
    if not os.path.exists(svd_path):
        return f"Error: SVD file not found: {svd_path}"

    names = []
    try:
        for _ev, elem in ET.iterparse(svd_path, events=("end",)):
            if elem.tag == "peripheral":
                nm = elem.find("name")
                if nm is not None and nm.text:
                    names.append(nm.text)
                elem.clear()
    except ET.ParseError as e:
        return f"SVD parse error: {e}"
    names = sorted(set(names))
    return f"[{os.path.basename(svd_path)}] {len(names)} peripherals:\n" + ", ".join(names)


if __name__ == "__main__":
    mcp.run()
