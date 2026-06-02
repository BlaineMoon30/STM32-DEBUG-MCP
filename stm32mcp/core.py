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


def _glob_first(roots, pattern):
    """Return the first file matching `pattern` under any of `roots` (recursive)."""
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        hits = glob.glob(os.path.join(root, "**", pattern), recursive=True)
        if hits:
            hits.sort()  # prefer the newest versioned folder (last when sorted)
            return hits[-1]
    return None


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

    return {"cli": cli, "openocd": openocd, "gdb": gdb, "scripts": scripts}


PATHS = _resolve_paths()

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


def find_elf():
    """Find the first .elf in the active build dir, as a normalized forward-slash path."""
    bdir = get_build_dir()
    if not bdir or not os.path.isdir(bdir):
        return None
    for f in os.listdir(bdir):
        if f.lower().endswith(".elf"):
            full = os.path.abspath(os.path.join(bdir, f))
            return full.replace("\\", "/")
    return None


# ====================================================================
# Generic process / CLI helpers
# ====================================================================
def run(cmd, cwd=None, timeout=180):
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
