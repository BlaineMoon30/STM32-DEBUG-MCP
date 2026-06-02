# SPDX-License-Identifier: MIT
"""tools_svd - read peripherals and decode their registers using SVD (debug session)."""

import os

from . import core
from . import chips
from . import svd as svdmod

mcp = core.mcp


def _resolve_svd_path(svd):
    """Resolve an SVD path from an explicit part name or the detected chip.

    Returns (path, None) or (None, error_message).
    """
    if svd:
        svd_dir = svdmod.find_svd_dir()
        path = os.path.join(svd_dir, svd if svd.endswith(".svd") else svd + ".svd")
        if not os.path.exists(path):
            return None, f"specified SVD file not found: {path}"
        return path, None
    dev = chips.get_detected_device() or chips.detect_device_name()
    if not dev:
        return None, "chip not detected for SVD selection. Specify the svd argument."
    return svdmod.pick_svd_file(dev)


@mcp.tool
def read_peripheral(name: str, svd: str = "") -> str:
    """페리페럴의 레지스터를 SVD로 디코딩해 '비트 의미'까지 보여줍니다.
    Read a peripheral's registers and decode them into their bit-field meanings.

    사용 예 / Use for: "SPI1 레지스터 해석해줘", "SPI1 상태 비트 보여줘",
    "decode the SPI1 registers", "show SPI1 status bits".
    예: read_peripheral("SPI1") -> CR1/CR2/SR 등을 CPOL/CPHA/OVR/BSY 로 분해.
    디버그 세션이 필요합니다(멈춘 상태 권장).
    Requires a debug session (preferably while halted).

    폴트/페리페럴 힌트 / Fault & peripheral hints:
      - HardFault: read_peripheral("SCB") 로 CFSR/HFSR, BFAR/MMFAR
        (폴트 원인과 폴트 주소)를 디코딩.
        For a HardFault, "SCB" decodes CFSR/HFSR and BFAR/MMFAR.
      - SPI: SR.OVR=1 이면 수신을 늦게 읽어 데이터 유실; CR1.CPOL/CPHA 불일치 시 깨짐.
        SPI: SR.OVR=1 = RX read too late; CR1.CPOL/CPHA mismatch corrupts data.

    Args:
        name: 페리페럴 이름 (예: "SCB", "SPI1", "USART2", "GPIOA", "TIM2").
        svd: SVD 품번 강제 지정(예: "STM32H563"), 비우면 칩에서 자동 선택.
    """
    if core.get_gdb() is None:
        return "Error: no debug session. Call start_debug first."

    svd_path, err = _resolve_svd_path(svd)
    if err:
        return f"Error: {err}"

    parsed = svdmod.parse_peripheral(svd_path, name)
    if isinstance(parsed, tuple) and isinstance(parsed[1], str):
        return f"Error: {parsed[1]}"
    if parsed is None:
        return f"Error: peripheral '{name}' not found in the SVD."

    base, regs = parsed
    if not regs:
        return f"'{name}' has no register definitions (SVD: {os.path.basename(svd_path)})."

    lines = [f"=== {name} @ {hex(base)}  (SVD: {os.path.basename(svd_path)}) ==="]
    MAX_REGS = 24
    shown = 0
    for reg_name, off, _size, fields in regs:
        if shown >= MAX_REGS:
            lines.append(f"... ({len(regs) - shown} more registers omitted)")
            break
        addr = base + off
        val = core.read_word(addr)
        if val is None:
            lines.append(f"\n{reg_name} @ {hex(addr)} : (read failed)")
            shown += 1
            continue
        lines.append(f"\n{reg_name} @ {hex(addr)} = 0x{val:08X}")
        for fn, fv, bits in svdmod.decode_register(val, fields):
            mark = " <--" if fv != 0 else ""
            lines.append(f"    {fn:<12} = {fv}   ({bits}){mark}")
        shown += 1

    lines.append("\n(<-- marks non-zero fields - inspect these first)")
    return "\n".join(lines)[:6000]


@mcp.tool
def list_peripherals(svd: str = "") -> str:
    """현재 칩 SVD에 정의된 페리페럴 이름 목록을 보여줍니다.
    List the peripheral names defined in the current chip's SVD.

    사용 예 / Use for: "어떤 페리페럴 있어?", "페리페럴 목록 보여줘",
    "what peripherals are there", "list peripherals".
    read_peripheral 에 넣을 정확한 이름을 확인할 때 유용합니다.
    Helpful to find the exact name to pass to read_peripheral.
    """
    import xml.etree.ElementTree as ET

    svd_path, err = _resolve_svd_path(svd)
    if err:
        return f"Error: {err}"

    names = []
    try:
        for _ev, elem in ET.iterparse(svd_path, events=("end",)):
            if elem.tag == "peripheral":
                nm = elem.find("name")
                if nm is not None and nm.text:
                    names.append(nm.text)
                elem.clear()
    except ET.ParseError as e:
        return f"SVD parse error: {e}"
    names = sorted(set(names))
    return f"[{os.path.basename(svd_path)}] {len(names)} peripherals:\n" + ", ".join(names)
