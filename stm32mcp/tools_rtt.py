# SPDX-License-Identifier: MIT
"""tools_rtt - expose SEGGER RTT through the running OpenOCD debug session.

RTT needs the debug probe, and the probe already belongs to this session's
OpenOCD, so RTT is switched on HERE (OpenOCD 'rtt' commands sent as GDB
'monitor ...') and OpenOCD serves the channel on a TCP port. Reading,
searching and waiting on the log is the job of the stm32-log MCP server
(open_rtt(port=...)), which works the same with any probe.
"""

import re
import time

from . import core

mcp = core.mcp


def _monitor(cmd, timeout=10):
    """Run 'monitor <cmd>'; if the core is running, halt for it and resume after."""
    out = core.gdb_cmd(f"monitor {cmd}", timeout=timeout)
    if "running" in out.lower() and "[error]" in out:
        core.gdb_cmd("-exec-interrupt", timeout=5)
        time.sleep(0.3)
        out = core.gdb_cmd(f"monitor {cmd}", timeout=timeout)
        core.gdb_cmd("-exec-continue", timeout=5)
    return out


def _symbol_address(name):
    out = core.gdb_cmd(f"-data-evaluate-expression &{name}", timeout=5)
    m = re.search(r"0x[0-9a-fA-F]+", out) if "[error]" not in out else None
    return int(m.group(0), 16) if m else None


@mcp.tool
def rtt_server_start(address: str = "", size: str = "", channel: int = 0, port: int = 19021) -> str:
    """디버그 세션 중 SEGGER RTT 를 켜고 채널을 TCP 포트로 내보냅니다 (OpenOCD rtt).
    Switch on SEGGER RTT in the running debug session and serve one channel on a TCP port.

    사용 예 / Use for: "RTT 켜줘", "RTT 로그 보게 해줘", "start RTT", "enable RTT output".
    로그를 읽고 기다리는 것은 stm32-log 서버의 open_rtt(port) / read_log / wait_for 로 합니다.
    Read/search/wait on the log with the stm32-log server: open_rtt(port) -> read_log / wait_for.

    Args:
        address: RTT 제어 블록 주소 또는 검색 시작 주소. 비우면 ELF 의 _SEGGER_RTT 심볼.
            Control block address (or start of a RAM range to search); empty = ELF symbol _SEGGER_RTT.
        size: 검색 범위 크기 (address 를 직접 줄 때). Search range size when address is given
            (e.g. "0x20000"); default 0x1000 around the symbol.
        channel: 내보낼 up 채널 번호 (보통 0 = Terminal). RTT up-channel to serve.
        port: TCP 포트. TCP port for the channel (one server per channel).
    Requires an active session (start_debug). The firmware must have initialised the
    control block (SEGGER_RTT_Init / first SEGGER_RTT_Write) before it can be found:
    if it is not found, run past init (e.g. cont, or a breakpoint after main) and call again.
    """
    if core.get_gdb() is None:
        return "Error: no debug session. Call start_debug first."
    if address:
        try:
            addr = int(address, 0)
        except ValueError:
            return f"Error: bad address {address!r}"
        src = "given"
    else:
        addr = _symbol_address("_SEGGER_RTT")
        if addr is None:
            return ("Error: symbol _SEGGER_RTT not found in the loaded ELF. Add SEGGER_RTT.c to the "
                    "firmware, or pass address= (and size=) to search a RAM range.")
        src = "ELF symbol _SEGGER_RTT"
    try:
        span = int(size, 0) if size else 0x1000
    except ValueError:
        return f"Error: bad size {size!r}"

    log_mark = len(core.openocd_log_tail(10 ** 7))
    steps = [_monitor(f'rtt setup {addr:#x} {span:#x} "SEGGER RTT"'),
             _monitor("rtt start")]
    # OpenOCD reports the search result in its own log, not over GDB.
    time.sleep(0.5)
    oc_new = core.openocd_log_tail(10 ** 7)[log_mark:]
    m = re.search(r"[Cc]ontrol block found at (0x[0-9a-fA-F]+)", oc_new)
    if not m:
        _monitor("rtt stop")
        return ("RTT control block NOT found "
                f"(searched {addr:#x}..{addr + span:#x}, {src}).\n"
                "The firmware has probably not initialised it yet: let it run past RTT init "
                "(cont, or break after main) and call rtt_server_start again.\n\n"
                + "\n".join(steps) + "\n" + oc_new[-600:])
    steps.append(_monitor(f"rtt server start {port} {channel}"))
    chans = _monitor("rtt channellist")
    bad = [s for s in steps if "[error]" in s]
    if bad:
        return "Error while starting the RTT server:\n" + "\n".join(bad)
    return (f"RTT on: control block at {m.group(1)} ({src}); up-channel {channel} -> tcp://127.0.0.1:{port}\n"
            f"{chans}\n\n"
            f"Next: stm32-log open_rtt(port={port}) to collect it, then read_log / wait_for. "
            "Text sent with stm32-log send() goes to down-channel "
            f"{channel}. The target must be running (cont) for new output to appear.")


@mcp.tool
def rtt_server_stop(port: int = 19021) -> str:
    """RTT TCP 서버와 RTT 폴링을 끕니다. Stop the RTT TCP server on `port` and RTT polling.

    사용 예 / Use for: "RTT 꺼줘", "stop RTT".
    """
    if core.get_gdb() is None:
        return "No debug session (RTT stops with the session anyway)."
    return "\n".join([_monitor(f"rtt server stop {port}"), _monitor("rtt stop")])
