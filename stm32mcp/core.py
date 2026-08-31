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
core - shared state and helpers for the STM32 Debug MCP Server.

Holds the single FastMCP instance, tool-path/build-dir resolution, generic
process/CLI/GDB helpers, and the live debug-session handles. All tool modules
import from here. Mutable session state (OpenOCD/GDB handles, build-dir
override) is kept in this module and accessed as core.<name> so reassignment
is visible everywhere.
"""

import glob
import os
import re
import shutil
import subprocess
import time

from fastmcp import FastMCP

try:
    from pygdbmi.gdbcontroller import GdbController
except ImportError:
    GdbController = None

# The single shared MCP server instance.
mcp = FastMCP("STM32 Probe Info")

GDB_PORT = 3333

# ====================================================================
# Tool path configuration (mostly auto-detected; override via env vars)
# ====================================================================
_CUBEIDE_CANDIDATES = [
    os.environ.get("STM32_CUBEIDE_ROOT", ""),
    r"C:/ST/STM32CubeIDE_2.1.1",
    r"C:/ST",
    r"C:/Program Files/STMicroelectronics",
    r"C:/Program Files (x86)/STMicroelectronics",
]

_SVD_DIR_CANDIDATES = [
    os.environ.get("STM32_SVD_DIR", ""),
    r"C:/ST/STM32CubeCLT_1.21.0/STMicroelectronics_CMSIS_SVD",
    r"C:/ST",  # fall back to scanning for a *_CMSIS_SVD folder underneath
]

# IAR Embedded Workbench for ARM (EWARM) install roots, newest first.
_IAR_CANDIDATES = [
    os.environ.get("STM32_IAR_ROOT", ""),
    r"C:/iar/ewarm-9.60.4",
    r"C:/iar",
    r"C:/Program Files/IAR Systems",
    r"C:/Program Files (x86)/IAR Systems",
]


def _version_key(path):
    """Natural/semantic sort key for a path.

    Splits digit runs out as integers so that, e.g., 'STM32CubeIDE_2.10.0'
    sorts AFTER 'STM32CubeIDE_2.2.0' (a plain string sort gets this backwards
    once a version component reaches two digits). Every element is a uniform
    (rank, int, str) tuple so a digit token is never compared against a text
    token (which would raise TypeError on misaligned paths).
    """
    s = path.replace("\\", "/").lower()
    key = []
    for p in re.split(r"(\d+)", s):
        if p.isdigit():
            key.append((0, int(p), ""))
        elif p:
            key.append((1, 0, p))
    return key


def _glob_first(roots, pattern):
    """Return the newest file matching `pattern` across all `roots` (recursive).

    'Newest' is decided by natural/semantic version order over the full path, so
    a freshly installed toolchain (e.g. STM32CubeIDE_2.10.x or STM32CubeCLT_1.22)
    is preferred over an older one. All roots are merged and de-duplicated, so a
    leftover pinned-version folder no longer shadows a newer install beside it.
    (Explicit per-tool env vars are applied by callers before this is reached.)
    """
    hits = []
    seen = set()
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for h in glob.glob(os.path.join(root, "**", pattern), recursive=True):
            key = os.path.normcase(os.path.abspath(h))
            if key not in seen:
                seen.add(key)
                hits.append(h)
    if not hits:
        return None
    hits.sort(key=_version_key)
    return hits[-1]


def _resolve_paths():
    """Resolve OpenOCD / GDB / CubeProgrammer / scripts paths: env var, then auto."""
    roots = [r for r in _CUBEIDE_CANDIDATES if r]

    cli = os.environ.get("STM32_PROGRAMMER_CLI") or shutil.which("STM32_Programmer_CLI") \
        or _glob_first(roots, "STM32_Programmer_CLI.exe")

    openocd = os.environ.get("OPENOCD_BIN") or _glob_first(roots, "openocd.exe")

    gdb = os.environ.get("GDB_BIN") or shutil.which("arm-none-eabi-gdb") \
        or _glob_first(roots, "arm-none-eabi-gdb.exe")

    scripts = os.environ.get("OPENOCD_SCRIPTS")
    if not scripts:
        h5cfg = _glob_first(roots, "stm32h5x.cfg")
        if h5cfg:
            # .../st_scripts/target/stm32h5x.cfg  ->  .../st_scripts
            scripts = os.path.dirname(os.path.dirname(h5cfg))

    # IAR EWARM command-line build tool (iarbuild.exe) and compiler (iccarm.exe).
    iar_roots = [r for r in _IAR_CANDIDATES if r]
    iarbuild = os.environ.get("STM32_IARBUILD") or shutil.which("iarbuild") \
        or _glob_first(iar_roots, "iarbuild.exe")
    iccarm = _glob_first(iar_roots, "iccarm.exe")

    # ST internal flash loaders (*.stldr). Newer target cfgs (e.g. stm32c5x)
    # program flash via an stldr instead of a built-in flash driver and abort at
    # startup unless INTERNAL_FLASH_LOADERS is set. They live in
    # <CubeProgrammer>/bin/FlashLoader/, keyed by DBGMCU DEV_ID (e.g. 0x44E.stldr).
    stldr_dir = os.environ.get("STM32_FLASHLOADER_DIR")
    if not stldr_dir and cli:
        cand = os.path.join(os.path.dirname(cli), "FlashLoader")
        if os.path.isdir(cand):
            stldr_dir = cand
    if not stldr_dir:
        hit = _glob_first(roots, os.path.join("FlashLoader", "*.stldr"))
        if hit:
            stldr_dir = os.path.dirname(hit)

    return {"cli": cli, "openocd": openocd, "gdb": gdb, "scripts": scripts,
            "iarbuild": iarbuild, "iccarm": iccarm, "stldr_dir": stldr_dir}


PATHS = _resolve_paths()


def find_internal_flash_loader(dev_id):
    """Resolve the ST internal flash loader (*.stldr) for a DBGMCU DEV_ID.

    dev_id is a hex string as printed by CubeProgrammer (e.g. '0x44E'). The
    loaders are named '<dev_id>.stldr' under the CubeProgrammer FlashLoader dir.
    Returns a forward-slash path, or None if no matching loader is found.
    """
    d = (dev_id or "").strip()
    if not d:
        return None
    if not d.lower().startswith("0x"):
        d = "0x" + d
    sd = PATHS.get("stldr_dir")
    if not sd or not os.path.isdir(sd):
        return None
    body = d[2:]
    # CubeProgrammer prints e.g. 0x44E; files are 0x44E.stldr. Try the exact
    # value plus upper/lower-case hex variants.
    for cand in (d, "0x" + body.upper(), "0x" + body.lower()):
        p = os.path.join(sd, cand + ".stldr")
        if os.path.isfile(p):
            return p.replace("\\", "/")
    # Fallback: a single <id>*.stldr (prefer the plain one over *_nonSecure).
    hits = []
    for cand in ("0x" + body.upper(), "0x" + body.lower(), d):
        hits += glob.glob(os.path.join(sd, cand + "*.stldr"))
    hits = sorted(set(hits), key=len)
    return hits[0].replace("\\", "/") if hits else None


# ====================================================================
# Build directory resolution
#   priority: set_build_dir override > STM32_BUILD_DIR env > auto-detect > None
# ====================================================================
_BUILD_DIR_DEFAULT = None      # no machine-specific default
_build_dir_override = None     # set at runtime by set_build_dir


def set_build_dir_override(path):
    """Set (or clear with None/'') the runtime build-dir override."""
    global _build_dir_override
    _build_dir_override = path or None


def _auto_find_build_dir():
    """Search the current working directory tree for a folder containing an .elf.

    Prefers Debug, then Release, then other folders; then shallower paths.
    Returns None if nothing is found.
    """
    cwd = os.getcwd()
    candidates = []
    for root, _dirs, files in os.walk(cwd):
        base = os.path.basename(root).lower()
        if base in (".git", "node_modules", ".vscode"):
            continue
        if any(f.lower().endswith(".elf") for f in files):
            depth = root.replace("\\", "/").count("/")
            if base == "debug":
                pref = 0
            elif base == "release":
                pref = 1
            else:
                pref = 2
            candidates.append((pref, depth, root))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2].replace("\\", "/")


def build_dir_source():
    """Return a label describing how the build dir is (or isn't) resolved."""
    if _build_dir_override:
        return "set_build_dir override"
    if os.environ.get("STM32_BUILD_DIR"):
        return "STM32_BUILD_DIR env"
    if _auto_find_build_dir():
        return "auto-detected from CWD"
    return "NOT SET"


def get_build_dir():
    """Return the active build directory, or None if it cannot be determined."""
    if _build_dir_override:
        return _build_dir_override
    env = os.environ.get("STM32_BUILD_DIR")
    if env:
        return env
    auto = _auto_find_build_dir()
    if auto:
        return auto
    return _BUILD_DIR_DEFAULT  # None


def no_build_dir_msg():
    """Guidance shown when the build directory cannot be determined."""
    return (
        "빌드 폴더(.elf 위치)를 찾지 못했습니다.\n"
        "Could not determine the build folder (.elf location).\n\n"
        "다음 중 하나로 지정하세요 / Set it in one of these ways:\n"
        "  1) 도구로 지정 / Tell me directly:\n"
        "       \"빌드 폴더를 D:/myproj/Debug 로 지정해줘\"  (calls set_build_dir)\n"
        "  2) 프로젝트 폴더에서 실행 / Run Claude Code from the project folder so the\n"
        "     .elf can be auto-detected (e.g. the CubeIDE Debug/ folder is under it).\n"
        "  3) 환경변수 / Env var at registration: STM32_BUILD_DIR=...\n\n"
        "현재 위치 / Current working dir: " + os.getcwd().replace("\\", "/")
    )


# ====================================================================
# Toolchain selection (GCC make-build vs IAR EWARM iarbuild)
#   priority: set_toolchain override > STM32_TOOLCHAIN env > auto-detect
# An IAR project (.ewp) nearby (and no Makefile) auto-selects the IAR path.
# ====================================================================
_toolchain_override = None     # "gcc" | "iar" | None
_iar_project_override = None   # explicit path to a .ewp (set_iar_project)


def set_toolchain_override(name):
    """Set ('gcc'/'iar') or clear (None/'') the runtime toolchain override."""
    global _toolchain_override
    name = (name or "").strip().lower()
    _toolchain_override = name if name in ("gcc", "iar", "cmake") else None


def set_iar_project_override(path):
    """Set (or clear with None/'') the runtime IAR project (.ewp) override."""
    global _iar_project_override
    _iar_project_override = path or None


def find_iar_project():
    """Locate the IAR project file (.ewp): override > env > auto-find under build dir / CWD.

    Returns a normalized forward-slash path, or None. Prefers the shallowest match.
    """
    if _iar_project_override:
        return _iar_project_override.replace("\\", "/")
    env = os.environ.get("STM32_IAR_PROJECT")
    if env:
        return env.replace("\\", "/")
    seen = []
    for root in (get_build_dir(), os.getcwd()):
        if not root or not os.path.isdir(root) or root in seen:
            continue
        seen.append(root)
        hits = glob.glob(os.path.join(root, "**", "*.ewp"), recursive=True)
        if hits:
            hits.sort(key=lambda p: (p.replace("\\", "/").count("/"), p))
            return hits[0].replace("\\", "/")
    return None


def get_toolchain():
    """Return the active build toolchain: 'gcc' or 'iar'."""
    if _toolchain_override in ("gcc", "iar", "cmake"):
        return _toolchain_override
    env = (os.environ.get("STM32_TOOLCHAIN") or "").strip().lower()
    if env in ("gcc", "iar", "cmake"):
        return env
    # Auto-detect: a CMake build dir (Ninja or Makefiles generator) means CMake;
    # a plain Makefile means GCC; otherwise an IAR project (.ewp) nearby means
    # IAR. Default to GCC.
    bdir = get_build_dir()
    if bdir and os.path.isfile(os.path.join(bdir, "CMakeCache.txt")):
        return "cmake"
    if bdir and os.path.isfile(os.path.join(bdir, "Makefile")):
        return "gcc"
    if find_iar_project():
        return "iar"
    return "gcc"


def toolchain_source():
    """Return a label describing how the toolchain was resolved."""
    if _toolchain_override in ("gcc", "iar", "cmake"):
        return "set_toolchain override"
    if (os.environ.get("STM32_TOOLCHAIN") or "").strip().lower() in ("gcc", "iar", "cmake"):
        return "STM32_TOOLCHAIN env"
    return "auto-detected"


def run_iarbuild(ewp, config, action="-make", timeout=900):
    """Build an IAR project from the command line via iarbuild.exe.

    action: '-make' (incremental), '-build' (rebuild all), or '-clean'.
    """
    iarbuild = PATHS.get("iarbuild")
    if not iarbuild or not os.path.exists(iarbuild):
        return ("Error: iarbuild.exe not found. Install IAR EWARM or set "
                "STM32_IAR_ROOT / STM32_IARBUILD.")
    if not ewp or not os.path.exists(ewp):
        return f"Error: IAR project (.ewp) not found: {ewp!r}"
    return run([iarbuild, ewp, action, config, "-log", "info"], timeout=timeout)


# ====================================================================
# Firmware image discovery (.elf for GCC, .out for IAR; both are ELF/DWARF)
# ====================================================================
def find_elf():
    """Find the firmware image for the active build, as a forward-slash path.

    GCC produces a top-level ``.elf`` in the build dir; IAR EWARM produces a
    ``.out`` (also ELF/DWARF) under ``<project>/<config>/Exe/``. Returns None
    if nothing is found.
    """
    bdir = get_build_dir()
    # Fast path: a top-level .elf (GCC) or .out (IAR) directly in the build dir.
    if bdir and os.path.isdir(bdir):
        for ext in (".elf", ".out"):
            for f in sorted(os.listdir(bdir)):
                if f.lower().endswith(ext):
                    return os.path.abspath(os.path.join(bdir, f)).replace("\\", "/")
    # IAR fallback: the image sits in <project>/<config>/Exe/<proj>.out, so
    # search the build dir and the IAR project dir, preferring an 'Exe' folder
    # and the newest file.
    roots = []
    if bdir and os.path.isdir(bdir):
        roots.append(bdir)
    ewp = find_iar_project()
    if ewp:
        pdir = os.path.dirname(ewp)
        if os.path.isdir(pdir) and pdir not in roots:
            roots.append(pdir)
    hits = []
    for root in roots:
        for r, _dirs, files in os.walk(root):
            base = os.path.basename(r).lower()
            if base in (".git", "node_modules", ".vscode"):
                continue
            for f in files:
                if f.lower().endswith((".elf", ".out")):
                    full = os.path.join(r, f)
                    in_exe = 0 if base == "exe" else 1
                    hits.append((in_exe, -os.path.getmtime(full), full))
    if hits:
        hits.sort()
        return os.path.abspath(hits[0][2]).replace("\\", "/")
    return None


# ====================================================================
# Generic process / CLI helpers
# ====================================================================
def gnu_tools_env():
    """Environment for make/cmake builds, with the GNU ARM toolchain bin dir
    prepended to PATH so `arm-none-eabi-gcc` resolves even when the MCP host
    process was launched without it (e.g. Claude Code on Windows).

    The dir comes from STM32_GNU_TOOLS if set, else from the folder of the
    auto-detected arm-none-eabi-gdb (CubeIDE plugin / CubeCLT), which ships
    gcc alongside gdb."""
    env = os.environ.copy()
    d = (os.environ.get("STM32_GNU_TOOLS") or "").strip()
    if not d and PATHS.get("gdb"):
        d = os.path.dirname(PATHS["gdb"])
    if d and os.path.isdir(d):
        env["PATH"] = d + os.pathsep + env.get("PATH", "")
    return env


def run(cmd, cwd=None, timeout=180, env=None):
    """Run a command and return combined stdout + stderr (never raises)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                           env=env)
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return f"Error: command timed out ({timeout}s)"
    except FileNotFoundError:
        return f"Error: executable not found: {cmd[0]}"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def run_cli(args, timeout=120):
    """Run STM32_Programmer_CLI with the given arguments."""
    cli = PATHS.get("cli")
    if not cli or not os.path.exists(cli):
        return "Error: STM32_Programmer_CLI not found. Set STM32_PROGRAMMER_CLI."
    return run([cli, *args], timeout=timeout)


# ====================================================================
# Debug session state (OpenOCD + GDB) and GDB helpers
# ====================================================================
_openocd = None   # subprocess.Popen for OpenOCD
_gdb = None       # pygdbmi GdbController
_active_cfg = None


def get_gdb():
    return _gdb


def get_openocd():
    return _openocd


def set_openocd(proc):
    global _openocd
    _openocd = proc


def set_gdb(ctrl):
    global _gdb
    _gdb = ctrl


def set_active_cfg(cfg):
    global _active_cfg
    _active_cfg = cfg


def gdb_cmd(command, timeout=20):
    """Send a GDB/MI command and return the relevant response lines as text."""
    if _gdb is None:
        return "Error: no debug session. Call start_debug first."
    try:
        try:
            resp = _gdb.write(command, timeout_sec=timeout, raise_error_on_timeout=False)
        except TypeError:
            resp = _gdb.write(command, timeout_sec=timeout)
    except Exception as e:  # noqa: BLE001
        return f"(no response / target running - {type(e).__name__})"
    lines = []
    for m in resp:
        t = m.get("type")
        payload = m.get("payload")
        if t in ("result", "notify", "console") and payload:
            lines.append(f"[{t}] {payload}")
    return "\n".join(lines)[:3000] or "(no response)"


def read_word(address):
    """Read a single 32-bit word from target memory as an int (None on failure)."""
    resp = gdb_cmd(f"-data-read-memory {hex(address)} x 4 1 1", timeout=10)
    m = re.search(r'data=\["?(0x[0-9a-fA-F]+)', resp)
    if not m:
        m = re.search(r'(0x[0-9a-fA-F]{1,8})', resp)
    if m:
        try:
            return int(m.group(1), 0)
        except ValueError:
            return None
    return None
