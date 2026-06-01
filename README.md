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

## Step 2. Place the server file

Save `stm32_probe_mcp.py` in a folder (e.g. `D:/STM32_MCP/`).

Edit **only this one line** at the top to match your project:

```python
BUILD_DIR = r"D:/.../STM32CubeIDE/Debug"   # folder where the .elf is built
```

> All other paths (OpenOCD/GDB/CLI/SVD) are auto-detected — no edits needed.

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
run check_setup                  # verify auto-detected paths (all ✅ = good)
list connected probes
what chip is this?               # auto-detect chip
build the project                # Makefile/CMake build
flash the board
a HardFault happened at runtime, debug it for me
```

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

If auto-detection misses, pass env vars at registration (`-e` after the server name):
```powershell
claude mcp add --scope user stm32-probe ^
  -e STM32_CUBEIDE_ROOT=D:/Tools/ST/STM32CubeIDE_x ^
  -e STM32_SVD_DIR=D:/Tools/ST/STM32CubeCLT_x/STMicroelectronics_CMSIS_SVD ^
  -- cmd /c py D:/STM32_MCP/stm32_probe_mcp.py
```

---

## Moving to another PC

1. Install CubeIDE + CubeCLT + real Python
2. `py -m pip install fastmcp pygdbmi`
3. Copy the server file → edit `BUILD_DIR` only
4. Run the Step 4 register command
5. Paths auto-detect → if stuck, run `check_setup`

> The only code to edit is the `BUILD_DIR` line. Everything else is auto-detected.
