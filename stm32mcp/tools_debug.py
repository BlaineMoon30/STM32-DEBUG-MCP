# SPDX-License-Identifier: MIT
"""tools_debug - debug session (OpenOCD + GDB), execution control, registers."""

import os
import subprocess
import time

from . import core
from . import chips

mcp = core.mcp


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
        elf_path: 디버그할 .elf (비우면 자동 탐색) / .elf to debug.
        chip: 계열 강제 지정(예: "stm32f4x"), 비우면 자동 감지 / force a family.
    """
    if core.GdbController is None:
        return "Error: pygdbmi not installed. Run 'py -m pip install pygdbmi' and restart."
    if core.get_openocd() is not None:
        return "A debug session is already running. Call stop_debug first."

    openocd = core.PATHS.get("openocd")
    scripts = core.PATHS.get("scripts")
    gdb = core.PATHS.get("gdb")
    miss = [n for n, v in [("OpenOCD", openocd), ("scripts", scripts), ("gdb", gdb)]
            if not v or not os.path.exists(v)]
    if miss:
        return f"Error: unresolved paths -> {', '.join(miss)}. Run check_setup."

    elf = elf_path or core.find_elf()
    if not elf or not os.path.exists(elf):
        if not core.get_build_dir():
            return core.no_build_dir_msg()
        return f"Error: .elf not found ({elf!r}). Build first, or check the build dir."

    # -- Decide chip -> target cfg --
    if chip:
        target_cfg = chip if chip.endswith(".cfg") else f"{chip}.cfg"
        info_line = f"chip (manual): {target_cfg}"
    else:
        dev = chips.detect_device_name()
        mapped = chips.map_chip(dev)
        if not mapped:
            return (
                f"Error: chip auto-detection failed (Device name: {dev!r}).\n"
                "Check the board connection, or specify chip directly "
                "(e.g. chip='stm32f4x')."
            )
        cfg_base, core_name, tz = mapped
        cfg_full = os.path.join(scripts, "target", cfg_base + ".cfg")
        if not os.path.exists(cfg_full):
            return (f"Error: {cfg_base}.cfg not found under OpenOCD scripts/target.\n"
                    f"   ({cfg_full})\nCheck whether this CubeIDE/OpenOCD supports the chip.")
        target_cfg = cfg_base + ".cfg"
        info_line = f"auto-detected: {dev}  ->  {target_cfg}  ({core_name}{', TrustZone' if tz else ''})"

    core.set_active_cfg(target_cfg)

    # -- Launch OpenOCD (interface unified to stlink-dap + dapdirect_swd) --
    cmd = [
        openocd, "-s", scripts,
        "-f", "interface/stlink-dap.cfg",
        "-c", "transport select dapdirect_swd",
        "-f", f"target/{target_cfg}",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:  # noqa: BLE001
        return f"Error: failed to launch OpenOCD - {e}"
    core.set_openocd(proc)
    time.sleep(2.0)
    if proc.poll() is not None:
        err = proc.stderr.read().decode(errors="ignore")
        core.set_openocd(None)
        return (
            f"Error: OpenOCD exited immediately (cfg: {target_cfg}).\n"
            "Usually a cfg name/path issue or ST-Link contention.\n"
            "(Make sure CubeIDE / CubeProgrammer GUI are closed.)\n\n" + err[:1800]
        )

    try:
        ctrl = core.GdbController(command=[gdb, "--interpreter=mi3"])
    except Exception as e:  # noqa: BLE001
        stop_debug()
        return f"Error: failed to launch GDB - {e}"
    core.set_gdb(ctrl)

    # Enable async mode so the target can be interrupted while running.
    core.gdb_cmd("-gdb-set mi-async on")
    core.gdb_cmd("-gdb-set non-stop off")

    elf_norm = os.path.abspath(elf).replace("\\", "/")
    sym = core.gdb_cmd(f'-file-exec-and-symbols "{elf_norm}"')
    tgt = core.gdb_cmd(f"-target-select extended-remote localhost:{core.GDB_PORT}")

    warn = ""
    if "No such file" in sym or "error" in sym.lower():
        warn = ("\n\nWarning: symbol load may have failed. Check the ELF path:\n"
                f"   {elf_norm}\n   (verify the file exists and the path is correct)")

    return (f"Debug session started.\n{info_line}\nELF: {elf_norm}\n"
            f"gdbserver: localhost:{core.GDB_PORT}\n\n" + "\n".join([sym, tgt]) + warn)


@mcp.tool
def stop_debug() -> str:
    """디버그 세션(GDB + OpenOCD)을 종료합니다.
    Stop the debug session (GDB + OpenOCD).

    사용 예 / Use for: "디버그 종료해줘", "세션 닫아줘", "stop debugging".
    """
    msg = []
    gdb = core.get_gdb()
    if gdb is not None:
        try:
            gdb.exit()
        except Exception:  # noqa: BLE001, S110
            pass
        core.set_gdb(None)
        msg.append("GDB stopped")
    proc = core.get_openocd()
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001, S110
            pass
        core.set_openocd(None)
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
    return core.gdb_cmd(f"-break-insert {location}")


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
    out = core.gdb_cmd("-exec-interrupt", timeout=10)
    time.sleep(0.3)
    loc = core.gdb_cmd("-stack-info-frame", timeout=10)
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
        return core.gdb_cmd("monitor reset halt")
    return core.gdb_cmd("monitor reset run")


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
    core.gdb_cmd("monitor reset halt", timeout=10)
    out = core.gdb_cmd("-exec-continue", timeout=10)
    time.sleep(0.5)
    loc = core.gdb_cmd("-stack-info-frame", timeout=5)
    return f"[run]\n{out}\n---\ncurrent state:\n{loc}"


@mcp.tool
def cont() -> str:
    """실행을 계속합니다(다음 브레이크포인트까지). 실행 중 멈추려면 halt 사용.
    Continue execution (until the next breakpoint). Use halt to stop while running.

    사용 예 / Use for: "계속해줘", "이어서 실행", "continue", "resume".
    """
    out = core.gdb_cmd("-exec-continue", timeout=10)
    time.sleep(0.5)
    loc = core.gdb_cmd("-stack-info-frame", timeout=5)
    return f"[continue]\n{out}\n---\ncurrent state:\n{loc}"


@mcp.tool
def step() -> str:
    """소스 한 줄을 실행합니다(함수 호출 시 안으로 진입).
    Step one source line (stepping into function calls).

    사용 예 / Use for: "한 줄 실행", "스텝 인", "step", "step into".
    """
    return core.gdb_cmd("-exec-step")


@mcp.tool
def step_over() -> str:
    """소스 한 줄을 실행합니다(함수 호출은 건너뜀).
    Step one source line (stepping over function calls).

    사용 예 / Use for: "한 줄 넘어가기", "스텝 오버", "step over".
    """
    return core.gdb_cmd("-exec-next")


@mcp.tool
def where() -> str:
    """현재 멈춘 위치(파일:라인, 함수, 스택 프레임)를 보여줍니다.
    Show the current stop location (file:line, function, stack frames).

    사용 예 / Use for: "지금 어디서 멈췄어?", "콜스택 보여줘", "where am I".
    """
    return core.gdb_cmd("-stack-info-frame") + "\n---\n" + core.gdb_cmd("-stack-list-frames")


@mcp.tool
def read_registers() -> str:
    """코어 레지스터(r0-r15, xPSR 등)를 덤프합니다. 멈춘 상태에서 호출하세요.
    Dump the core registers (r0-r15, xPSR, etc.). Call while halted.

    사용 예 / Use for: "레지스터 보여줘", "show registers".
    """
    return f"[names]\n{core.gdb_cmd('-data-list-register-names')}\n\n" \
           f"[values]\n{core.gdb_cmd('-data-list-register-values x')}"


@mcp.tool
def read_memory(address: str, count: int = 16) -> str:
    """메모리를 그대로 읽습니다 (페리페럴 레지스터 확인 등).
    Read raw memory (e.g. to inspect peripheral registers).

    사용 예 / Use for: "0x40013000 메모리 읽어줘", "read memory at ...".
    address 예: "0x40013000"(SPI1 베이스) 또는 "&myvar". count = 워드(4B) 개수.
    address example: "0x40013000" (SPI1 base) or "&myvar". count = words (4B).
    """
    return core.gdb_cmd(f"-data-read-memory {address} x 4 1 {count}")
