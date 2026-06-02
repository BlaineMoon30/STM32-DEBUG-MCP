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
STM32 Debug MCP Server - entry point.

Builds, flashes, and debugs STM32 boards from Claude Code via OpenOCD + GDB +
STM32CubeProgrammer, with SVD-based register decoding and HotPlug inspection.

The implementation is split into the `stm32mcp` package:
    core         - shared MCP instance, paths, build-dir, GDB helpers, state
    chips        - chip detection + family -> OpenOCD target cfg mapping
    svd          - SVD parsing and register/bit-field decoding
    tools_setup  - check_setup, set_build_dir, show_build_dir
    tools_probe  - list_probes, probe_details, build, flash, erase_chip, detect_chip
    tools_debug  - start_debug, stop_debug, breakpoints, run/cont/halt/step, registers
    tools_watch  - watch_read, watch_sample, set_watchpoint, list/delete breakpoints
    tools_svd    - read_peripheral, list_peripherals
    tools_hotplug- hotplug_read_memory, hotplug_read_peripheral

Importing the tools_* modules registers their @mcp.tool functions onto the
shared FastMCP instance in core. This file just imports them all and runs it.

Install:
    py -m pip install fastmcp pygdbmi

Environment variables (optional overrides):
    STM32_BUILD_DIR     : folder where the .elf is built
    STM32_CUBEIDE_ROOT  : STM32CubeIDE install root
    STM32_SVD_DIR       : folder containing the .svd files
    STM32_PROGRAMMER_CLI / OPENOCD_BIN / OPENOCD_SCRIPTS / GDB_BIN : explicit paths
"""

import os
import sys

# Make sure the package next to this script is importable regardless of the
# current working directory (Claude Code runs the server from the project
# folder, which may differ from where this file lives).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stm32mcp import core

# Importing each tools_* module registers its @mcp.tool functions on core.mcp.
from stm32mcp import tools_setup    # noqa: E402,F401
from stm32mcp import tools_probe    # noqa: E402,F401
from stm32mcp import tools_debug    # noqa: E402,F401
from stm32mcp import tools_watch    # noqa: E402,F401
from stm32mcp import tools_svd      # noqa: E402,F401
from stm32mcp import tools_hotplug  # noqa: E402,F401


def main():
    core.mcp.run()


if __name__ == "__main__":
    main()
