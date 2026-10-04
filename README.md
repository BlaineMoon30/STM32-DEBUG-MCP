# STM32 Debug MCP Server (English)

A local MCP server to build, flash, and debug STM32 boards from VSCode + Claude Code
(or VSCode + Codex). Targets STM32CubeIDE Makefile/CMake projects. With natural-language
requests like "a HardFault happened at runtime, debug it for me," the AI agent picks and
runs the right tools.

Scope: VSCode-based workflow (Claude Code or Codex) · STM32CubeIDE Makefile/CMake projects · selected STM32 families

Supported OS : Windows

---

## Step 1. Install

1. **STM32CubeIDE** — provides OpenOCD and arm-none-eabi-gdb
   - https://www.st.com/en/development-tools/stm32cubeide.html
2. **STM32CubeCLT** — provides STM32_Programmer_CLI and SVD files
   - https://www.st.com/en/development-tools/stm32cubeclt.html
3. **Python 3.11+** from python.org (NOT the Microsoft Store stub) — recommended 3.14.5
   - https://www.python.org/downloads/
   - Check "Add python.exe to PATH" during install. Use `py` to run.
4. Create the server's own virtual environment (run in the repo folder):
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install.ps1
   ```
   This creates `.venv\`, installs `requirements.txt` into it, and registers the server
   (Step 4). The server never uses packages from the system Python, so upgrading or
   reinstalling Python / pip packages elsewhere cannot break it.

---

## Step 2. Place the server files

Copy **both** of these into the same folder (e.g. `D:/STM32_MCP/`), side by side:

```
D:/STM32_MCP/
├─ .venv/                 # created by install.ps1 (never copy it between PCs)
├─ stm32_probe_mcp.py     # entry point — run/register this
├─ install.ps1  register_mcp.py
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
D:/STM32_MCP/.venv/Scripts/python.exe D:/STM32_MCP/stm32_probe_mcp.py
```

A FastMCP banner means it works → press `Ctrl+C` to stop.

---

## Step 4. Register with Claude Code

`install.ps1` already does this. To (re)register by hand, e.g. after moving the folder:
```powershell
.venv\Scripts\python.exe register_mcp.py                        # writes ~/.claude.json (user scope)
.venv\Scripts\python.exe register_mcp.py --build-dir D:/myproj/Debug
```

Or with the Claude CLI:
```powershell
claude mcp add --scope user stm32-probe -- D:/STM32_MCP/.venv/Scripts/python.exe D:/STM32_MCP/stm32_probe_mcp.py
```

- `--scope user` : available in every folder
- Always point at the `.venv` python, not `py` / `python`: the system Python may not have
  (or may later lose or upgrade) `fastmcp` and `pygdbmi`.

Verify connection:
```powershell
claude mcp list      # expect "stm32-probe ... ✓ Connected"
```

### Or register with Codex

Same server, Codex CLI. Run it in the terminal:
```powershell
codex mcp add stm32-probe -- D:\STM32_MCP\.venv\Scripts\python.exe D:\STM32_MCP\stm32_probe_mcp.py
```

Or, in the VSCode Codex plugin, enter the same command:
```
codex mcp add stm32-probe -- D:\STM32_MCP\.venv\Scripts\python.exe D:\STM32_MCP\stm32_probe_mcp.py
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

### Toolchain: GCC (Makefile/CMake), STM32CubeIDE or IAR EWARM

`build` supports these toolchains and auto-selects between them:

- **GCC** — a `Makefile` in the build dir → `make -j4 all` → produces a `.elf`.
- **IAR EWARM** — an IAR project (`.ewp`) nearby → `iarbuild.exe <proj.ewp> -make <config>`
  → produces a `.out` (also ELF/DWARF). `iarbuild.exe` is auto-detected (e.g.
  `C:/iar/ewarm-9.60.4/common/bin/iarbuild.exe`).
- **STM32CubeIDE (headless)** — a build dir inside a CubeIDE project (`.cproject` in it or its
  parent) with no Makefile yet → `stm32cubeidec.exe ... headlessbuild -import <project> -build <name>/<config>`
  in a private workspace (`.cubeide_ws/`) → produces a `.elf`. Also chosen instead of IAR when
  `iarbuild.exe` is missing (ST examples ship EWARM, MDK-ARM and STM32CubeIDE side by side).
  `stm32cubeidec.exe` is auto-detected under `C:/ST/STM32CubeIDE_*` (or `STM32_CUBEIDEC`).
  Two-stage examples (Boot + Appli) are two projects: `set_build_dir(".../STM32CubeIDE/Appli/Debug")`
  picks one; `set_toolchain("cubeide")` forces it even when a generated makefile exists.

Flashing and the whole OpenOCD + GDB debug stack work on the IAR `.out` unchanged
(it is a standard ELF with DWARF symbols), so `flash`, `start_debug`, breakpoints,
registers, etc. all behave the same regardless of toolchain.

```
what toolchain am I using?            # check_setup shows it
use IAR                               # set_toolchain("iar")
set the IAR project to D:/proj/App.ewp   # set_iar_project
build                                 # iarbuild -make Debug
build the Release config              # build(config="Release")
rebuild everything                    # build(clean=True)  -> iarbuild -build
flash the board                       # flashes the produced .out
```

Detection is automatic, so usually you only need `build`. Force it with
`set_toolchain("gcc"|"iar")` (or the `STM32_TOOLCHAIN` env var) when auto-detect
guesses wrong. If the `.ewp` isn't found, point at it with `set_iar_project` or the
`STM32_IAR_PROJECT` env var. IAR install paths can be overridden with `STM32_IAR_ROOT`
/ `STM32_IARBUILD`.

### HotPlug: inspect a running board without a debug session
`hotplug_read_memory` and `hotplug_read_peripheral` attach via CubeProgrammer
(`mode=HOTPLUG`) to read memory / decode peripherals **without** OpenOCD/GDB and
ideally without halting the firmware — handy for a board already running in the field.

> Caveats: core registers (R0–R15/PC/SP) are **not** reliably readable via HotPlug
> (use `read_registers` inside a debug session). On some setups HotPlug may still
> halt/reset — verify non-intrusiveness on your board.

### RTT log during a debug session
`rtt_server_start` switches on SEGGER RTT inside the running OpenOCD session (the control
block address comes from the ELF's `_SEGGER_RTT` symbol) and serves an up-channel on a
TCP port; `rtt_server_stop` turns it off. Collecting, searching and waiting on the log is
done by the [stm32-log](https://github.com/BlaineMoon30/STM32-LOG-MCP) server:

```
start_debug → rtt_server_start(port=19021) → (stm32-log) open_rtt(19021) → cont
            → (stm32-log) read_log / wait_for("BOOT OK") / send("cmd")
```

The firmware must link `SEGGER_RTT.c` and have initialised it; if the block is not found
yet, `cont` past RTT init and call `rtt_server_start` again. What RTT is and how it works
is explained, with diagrams, in the stm32-log README.

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

## Known issues

### STM32N6 (e.g. STM32N6-DK): re-run firmware **without** `reset`

The N6 boots from RAM (no internal user flash), so a `reset` will **not** re-run the
ELF you loaded into RAM — it can even drop the core into a HardFault. To re-run the
firmware on an N6 board, **do not reset**. Instead:

1. **Don't reset.** Start a **new** debug session and make sure the core is **halted**.
2. While halted, `load_image` to reload the ELF back into RAM.
3. `set_pc` to the **ELF entry point**.
4. `cont` to run.
5. To confirm it's actually running, briefly `halt`, check it's **not** a HardFault,
   then `cont` again.

> In short: new session → halted → `load_image` (RAM) → `set_pc` (entry point) →
> `cont`, and verify with a quick `halt` → check → `cont`. Avoid `reset` on the N6.

---

## Common issues

| Symptom | Fix |
|---------|-----|
| `py` prints only `Python` | Fake Python (Store stub). Install python.org build, use `py` |
| Tools not visible after add | Re-register with `--scope user` + start a **new** session |
| `✗ Failed to connect` | Run the server with `.venv\Scripts\python.exe` to see the error / rerun `install.ps1` |
| Flash/debug fails | Close CubeIDE & CubeProgrammer GUI (ST-Link contention) |
| Auto-detect failed | Run `check_setup`, find ❌ items, set env vars (below) |
| "Could not determine the build folder" | Say *"set the build dir to .../Debug"*, run from the project folder, or set `STM32_BUILD_DIR` |

If auto-detection misses, pass env vars at registration (`-e` after the server name):
```powershell
claude mcp add --scope user stm32-probe ^
  -e STM32_CUBEIDE_ROOT=D:/Tools/ST/STM32CubeIDE_x ^
  -e STM32_SVD_DIR=D:/Tools/ST/STM32CubeCLT_x/STMicroelectronics_CMSIS_SVD ^
  -e STM32_BUILD_DIR=D:/myproj/STM32CubeIDE/Debug ^
  -- D:/STM32_MCP/.venv/Scripts/python.exe D:/STM32_MCP/stm32_probe_mcp.py
```

or: `.venv\Scripts\python.exe register_mcp.py -e STM32_CUBEIDE_ROOT=... -e STM32_SVD_DIR=...`

> `STM32_BUILD_DIR` is optional — skip it and either let auto-detect find the `.elf`
> or tell Claude the build folder at runtime (`set_build_dir`).

---

## Moving to another PC

1. Install CubeIDE + CubeCLT + real Python
2. `git clone` the repo (or copy the folder **without** `.venv\`, a venv is not portable)
3. Run `install.ps1` (creates `.venv`, installs packages, registers the server)
4. Paths auto-detect → if stuck, run `check_setup`. Point at your project with
   *"set the build dir to .../Debug"*, `STM32_BUILD_DIR`, or by running from the project folder.

> No code to edit anymore — the build folder and all tool paths are resolved automatically.
