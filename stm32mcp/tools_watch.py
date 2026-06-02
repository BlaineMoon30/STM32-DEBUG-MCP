# SPDX-License-Identifier: MIT
"""tools_watch - live variable watching and watchpoints (data breakpoints)."""

import re
import time

from . import core

mcp = core.mcp


def _eval_expr(expr):
    """Evaluate a C expression / variable via GDB/MI and return its value as text.

    Works on a running target too (SWD memory access is non-intrusive),
    as long as the expression resolves to a global/static address.
    """
    resp = core.gdb_cmd(f'-data-evaluate-expression "{expr}"', timeout=8)
    m = re.search(r'value="(.*?)"(?:[,}]|$)', resp)
    if m:
        return m.group(1).replace('\\"', '"')
    m = re.search(r"'value':\s*'(.*?)'(?:[,}]|$)", resp)
    if m:
        return m.group(1)
    m = re.search(r"'value':\s*\"(.*?)\"(?:[,}]|$)", resp)
    if m:
        return m.group(1)
    return resp


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
    """
    if core.get_gdb() is None:
        return "오류/Error: no debug session. Call start_debug first."
    if not expressions:
        return "오류/Error: provide at least one expression."
    lines = ["=== live watch ==="]
    for ex in expressions:
        lines.append(f"  {ex} = {_eval_expr(ex)}")
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
    if core.get_gdb() is None:
        return "오류/Error: no debug session. Call start_debug first."
    count = max(1, min(count, 60))
    interval = max(10, interval_ms) / 1000.0
    samples = []
    for i in range(count):
        samples.append(_eval_expr(expression))
        if i < count - 1:
            time.sleep(interval)
    lines = [f"=== sample: {expression}  ({count} samples, {interval_ms}ms) ==="]
    for i, v in enumerate(samples):
        lines.append(f"  [{i:>2}] {v}")
    uniq = list(dict.fromkeys(samples))
    if len(uniq) == 1:
        lines.append(f"\n변화 없음 / unchanged: {uniq[0]}")
    else:
        lines.append(f"\n변화 감지 / changed: {samples[0]} -> {samples[-1]} "
                     f"({len(uniq)} distinct values)")
    return "\n".join(lines)


@mcp.tool
def set_watchpoint(expression: str, mode: str = "write") -> str:
    """워치포인트(데이터 브레이크포인트)를 겁니다 - 값이 바뀌면 자동으로 멈춥니다.
    Set a watchpoint (data breakpoint) - the target halts when the value changes/accesses.

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
    if core.get_gdb() is None:
        return "오류/Error: no debug session. Call start_debug first."
    m = mode.lower()
    if m == "write":
        cmd = f"-break-watch {expression}"
    elif m == "read":
        cmd = f"-break-watch -r {expression}"
    elif m == "access":
        cmd = f"-break-watch -a {expression}"
    else:
        return "오류/Error: mode must be 'write', 'read', or 'access'."
    out = core.gdb_cmd(cmd)
    return (f"[watchpoint: {m} on {expression}]\n{out}\n\n"
            "run/cont 로 실행하면 해당 접근 시 멈춥니다 / run or cont until it triggers.")


@mcp.tool
def list_watchpoints() -> str:
    """설정된 브레이크포인트/워치포인트 목록을 보여줍니다.
    List all configured breakpoints and watchpoints.

    사용 예 / Use for: "워치포인트 목록 보여줘", "뭐 걸려있어?",
    "list watchpoints", "show breakpoints".
    삭제하려면 번호로 delete_breakpoint 를 사용하세요.
    To remove one, use delete_breakpoint with its number.
    """
    return core.gdb_cmd("-break-list")


@mcp.tool
def delete_breakpoint(number: int) -> str:
    """번호로 브레이크포인트/워치포인트를 삭제합니다.
    Delete a breakpoint/watchpoint by its number.

    사용 예 / Use for: "2번 워치포인트 지워줘", "delete breakpoint 2".
    번호는 list_watchpoints 로 확인 / find numbers via list_watchpoints.
    """
    return core.gdb_cmd(f"-break-delete {number}")
