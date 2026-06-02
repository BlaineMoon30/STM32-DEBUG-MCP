# SPDX-License-Identifier: MIT
"""tools_hotplug - read state WITHOUT a debug session (CubeProgrammer HOTPLUG).

HotPlug attaches to a running target via CubeProgrammer CLI (mode=HOTPLUG)
without OpenOCD/GDB and ideally without halting/resetting the firmware.

Caveats (observed in the wild):
  - Reading multiple regions in one call has been reported to return swapped
    results, so we read ONE region per CLI invocation.
  - On some setups HotPlug may still appear to halt/reset; verify on your board.
  - Core registers (R0-R15/PC/SP) are generally NOT reliably readable via
    HotPlug (they live in the CPU, not memory) - use a debug session for those.
"""

import os
import re

from . import core
from . import chips
from . import svd as svdmod

mcp = core.mcp


def _hotplug_read_words(address, count):
    """Read `count` 32-bit words from `address` via CubeProgrammer HotPlug.

    Returns (values_list, raw_text). values_list is [] on failure.
    """
    out = core.run_cli(
        ["-c", "port=SWD", "mode=HOTPLUG", "-q",
         "-r32", hex(address), hex(count * 4)],
        timeout=60,
    )
    values = []
    for line in out.splitlines():
        if ":" not in line:
            continue
        left, _, right = line.partition(":")
        if not left.strip().lower().startswith("0x"):
            continue
        for tok in right.split():
            if re.fullmatch(r"[0-9A-Fa-f]{1,8}", tok):
                try:
                    values.append(int(tok, 16))
                except ValueError:
                    pass
    return values, out


@mcp.tool
def hotplug_read_memory(address: str, count: int = 4) -> str:
    """디버그 세션 없이 HotPlug로 메모리를 읽습니다 (펌웨어를 멈추지 않음, CubeProgrammer만 사용).
    Read memory via HotPlug without a debug session (no halt; CubeProgrammer only).

    사용 예 / Use for: "세션 없이 0x20000000 읽어줘", "운영 중 보드 메모리 봐줘",
    "read memory without a debug session", "hotplug read".
    이미 현장에서 돌고 있는 보드에 가볍게 붙어 값만 떠볼 때 적합합니다.
    Good for peeking at a board that is already running, without starting a session.

    Args:
        address: 시작 주소 (예: "0x20000000", "0x40013000"). hex string.
        count: 읽을 워드(4B) 개수 / number of 32-bit words.
    """
    try:
        addr = int(address, 0)
    except ValueError:
        return f"오류/Error: invalid address: {address!r}"
    count = max(1, min(count, 64))
    values, raw = _hotplug_read_words(addr, count)
    if not values:
        return f"읽기 실패 / read failed (raw):\n{raw[:1500]}"
    lines = [f"=== HotPlug read @ {hex(addr)} ({count} words) ==="]
    for i, v in enumerate(values):
        lines.append(f"  {hex(addr + i * 4)} = 0x{v:08X}")
    return "\n".join(lines)


@mcp.tool
def hotplug_read_peripheral(name: str, svd: str = "") -> str:
    """디버그 세션 없이 HotPlug로 페리페럴 레지스터를 읽고 SVD로 비트 해석합니다.
    Read a peripheral via HotPlug (no debug session) and decode it with the SVD.

    사용 예 / Use for: "세션 없이 SPI1 상태 봐줘", "운영 중 보드 GPIOA 레지스터 해석",
    "decode SPI1 without a debug session", "hotplug peripheral".
    펌웨어를 멈추지 않고 운영 중인 보드의 페리페럴 상태를 진단할 때 적합합니다.
    Diagnose a running board's peripheral state without halting it.

    주의/Note: 코어 레지스터(R0-R15 등)는 HotPlug로 안정적으로 못 읽습니다
    (그건 디버그 세션의 read_registers 사용).
    Core registers are not reliably readable via HotPlug; use read_registers in a session.

    Args:
        name: 페리페럴 이름 (예: "SPI1", "GPIOA", "USART2", "SCB").
        svd: SVD 품번 강제 지정(예: "STM32H563"), 비우면 칩에서 자동 선택.
    """
    if svd:
        svd_dir = svdmod.find_svd_dir()
        svd_path = os.path.join(svd_dir, svd if svd.endswith(".svd") else svd + ".svd")
        if not os.path.exists(svd_path):
            return f"오류/Error: SVD file not found: {svd_path}"
    else:
        dev = chips.get_detected_device() or chips.detect_device_name()
        if not dev:
            return ("오류/Error: chip not detected for SVD selection. "
                    "Specify the svd argument (e.g. svd='STM32H563').")
        svd_path, err = svdmod.pick_svd_file(dev)
        if err:
            return f"오류/Error: {err}"

    parsed = svdmod.parse_peripheral(svd_path, name)
    if isinstance(parsed, tuple) and isinstance(parsed[1], str):
        return f"오류/Error: {parsed[1]}"
    if parsed is None:
        return f"오류/Error: peripheral '{name}' not found in the SVD."
    base, regs = parsed
    if not regs:
        return f"'{name}' has no register definitions (SVD: {os.path.basename(svd_path)})."

    lines = [f"=== {name} @ {hex(base)}  [HotPlug, no session]  "
             f"(SVD: {os.path.basename(svd_path)}) ==="]
    MAX_REGS = 24
    shown = 0
    for reg_name, off, _size, fields in regs:
        if shown >= MAX_REGS:
            lines.append(f"... ({len(regs) - shown} more registers omitted)")
            break
        addr = base + off
        vals, _raw = _hotplug_read_words(addr, 1)  # one region per call
        if not vals:
            lines.append(f"\n{reg_name} @ {hex(addr)} : (read failed)")
            shown += 1
            continue
        val = vals[0]
        lines.append(f"\n{reg_name} @ {hex(addr)} = 0x{val:08X}")
        for fn, fv, bits in svdmod.decode_register(val, fields):
            mark = " <--" if fv != 0 else ""
            lines.append(f"    {fn:<12} = {fv}   ({bits}){mark}")
        shown += 1

    lines.append("\n(<-- marks non-zero fields - inspect these first)")
    lines.append("(HotPlug: read without halting; verify non-intrusiveness on your board)")
    return "\n".join(lines)[:6000]
