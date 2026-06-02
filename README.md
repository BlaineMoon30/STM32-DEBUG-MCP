# STM32 Debug MCP Server (English)

A local MCP server to build, flash, and debug STM32 boards from VSCode + Claude Code.
Targets STM32CubeIDE Makefile/CMake projects. With natural-language requests like
"a HardFault happened at runtime, debug it for me," Claude picks and runs the right tools.

Scope: VSCode-based workflow · STM32CubeIDE Makefile/CMake projects · selected STM32 families

Supported OS : Windows

---

## Step 1. Install

1. **STM32CubeIDE** — provides OpenOCD and arm-none-eabi-gdb
2. **STM32CubeCLT** — provides STM32_Programmer_CLI and SVD files
3. **Python 3.11+** from python.org (NOT the Microsoft Store stub)
   - Check "Add python.exe to PATH" during install. Use `py` to run.
4. Install packages:
   ```
   py -m pip install fastmcp pygdbmi
   ```

---

## Step 2. Place the server files

Copy **both** of these into the same folder (e.g. `D:/STM32_MCP/`), side by side:

```
D:/STM32_MCP/
├─ stm32_probe_mcp.py     # entry point — run/register this
└─ stm32mcp/              # package with the actual tools (keep next to the .py)
   ├─ core.py  chips.py  svd.py
   └─ tools_setup.py  tools_probe.py  tools_debug.py
      tools_watch.py  tools_svd.py  tools_hotplug.py
```

The entry point adds its own folder to `sys.path`, so it finds `stm32mcp/` no
matter which directory Claude Code runs it from — just keep the two together.

**No code editing needed** — the build folder (where the `.elf` is produced) is now
resolved automatically at runtime, in this order:

1. **Runtime override** — just tell Claude: *"set the build dir to D:/myproj/Debug"*
   (calls the `set_build_dir` tool, applies for the session)
2. **`STM32_BUILD_DIR` env var** — set at registration (see Step 4 / Common issues)
3. **Auto-detect** — searches the current working directory for a folder containing an
   `.elf` (prefers `Debug/`, then `Release/`, then the shallowest match)
4. If none are found, the tools ask you to set it — there is **no hard-coded path**.

> All other paths (OpenOCD/GDB/CLI/SVD) are auto-detected too — no edits needed.
> Ask *"what's the current build dir?"* anytime to see the active path (`show_build_dir`).

---

## Step 3. Verify it runs

```powershell
py D:/STM32_MCP/stm32_probe_mcp.py
```

A FastMCP banner means it works → press `Ctrl+C` to stop.

---

## Step 4. Register with Claude Code

```powershell
claude mcp add --scope user stm32-probe -- cmd /c py D:/STM32_MCP/stm32_probe_mcp.py
```

- `--scope user` : available in every folder
- `cmd /c py` : required on Windows (plain `python` fails)

Verify connection:
```powershell
claude mcp list      # expect "stm32-probe ... ✓ Connected"
```

---

## Step 5. Use it

Start a **new** Claude Code session in VSCode (registration applies to new sessions).
Check tools with `/mcp`, then:

```
run check_setup                  # verify auto-detected paths + build dir source
what's the current build dir?    # show active build folder (show_build_dir)
set the build dir to D:/proj/Debug   # point it at your project (set_build_dir)
list connected probes
what chip is this?               # auto-detect chip
build the project                # Makefile/CMake build
flash the board
a HardFault happened at runtime, debug it for me
read memory at 0x20000000 without a debug session   # HotPlug, no halt
decode SPI1 on the running board without a session   # HotPlug peripheral
```

### HotPlug: inspect a running board without a debug session
`hotplug_read_memory` and `hotplug_read_peripheral` attach via CubeProgrammer
(`mode=HOTPLUG`) to read memory / decode peripherals **without** OpenOCD/GDB and
ideally without halting the firmware — handy for a board already running in the field.

> Caveats: core registers (R0–R15/PC/SP) are **not** reliably readable via HotPlug
> (use `read_registers` inside a debug session). On some setups HotPlug may still
> halt/reset — verify non-intrusiveness on your board.

### Example: "a HardFault happened at runtime, debug it for me"
From that one request, Claude runs the following automatically:
1. `build` → `flash(run_after=False)` — build, flash, stay halted
2. `start_debug` — auto-detect chip + start session
3. `set_breakpoint HardFault_Handler` → `run`
4. On fault entry, `read_registers` — inspect stacked PC/LR, where it faulted
5. `read_peripheral("SCB")` — decode fault status regs (CFSR/HFSR, BFAR/MMFAR)
   into bit meanings (e.g. `CFSR.IMPRECISERR`, the faulting address) → locate the cause
6. `where` / cross-check source to identify the offending code or access
7. `stop_debug` — clean up

> A HardFault is already halted, so registers and the stack can be read as-is.
> Key clues: SCB CFSR (fault cause), HFSR, and BFAR/MMFAR (faulting address).

---

## Common issues

| Symptom | Fix |
|---------|-----|
| `py` prints only `Python` | Fake Python (Store stub). Install python.org build, use `py` |
| Tools not visible after add | Re-register with `--scope user` + start a **new** session |
| `✗ Failed to connect` | Run server directly to see the error / install `fastmcp` / use `py` |
| Flash/debug fails | Close CubeIDE & CubeProgrammer GUI (ST-Link contention) |
| Auto-detect failed | Run `check_setup`, find ❌ items, set env vars (below) |
| "Could not determine the build folder" | Say *"set the build dir to .../Debug"*, run from the project folder, or set `STM32_BUILD_DIR` |

If auto-detection misses, pass env vars at registration (`-e` after the server name):
```powershell
claude mcp add --scope user stm32-probe ^
  -e STM32_CUBEIDE_ROOT=D:/Tools/ST/STM32CubeIDE_x ^
  -e STM32_SVD_DIR=D:/Tools/ST/STM32CubeCLT_x/STMicroelectronics_CMSIS_SVD ^
  -e STM32_BUILD_DIR=D:/myproj/STM32CubeIDE/Debug ^
  -- cmd /c py D:/STM32_MCP/stm32_probe_mcp.py
```

> `STM32_BUILD_DIR` is optional — skip it and either let auto-detect find the `.elf`
> or tell Claude the build folder at runtime (`set_build_dir`).

---

## Moving to another PC

1. Install CubeIDE + CubeCLT + real Python
2. `py -m pip install fastmcp pygdbmi`
3. Copy `stm32_probe_mcp.py` **and** the `stm32mcp/` folder together (no code edits needed)
4. Run the Step 4 register command
5. Paths auto-detect → if stuck, run `check_setup`. Point at your project with
   *"set the build dir to .../Debug"*, `STM32_BUILD_DIR`, or by running from the project folder.

> No code to edit anymore — the build folder and all tool paths are resolved automatically.
