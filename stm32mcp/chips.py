# SPDX-License-Identifier: MIT
"""chips - STM32 family detection and family -> OpenOCD target cfg mapping."""

from . import core

# Last detected Device name (used by the SVD module to pick a file).
_detected_device = ""


def get_detected_device():
    return _detected_device


# ====================================================================
# STM32 family -> OpenOCD target cfg mapping
# (based on the cfg files actually present under st_scripts/target/)
# ====================================================================
# Order matters: put more specific prefixes first (e.g. STM32L4+ vs L4).
_CHIP_MAP = [
    # (Device-name prefix, target cfg, core, TrustZone)
    ("STM32C0",  "stm32c0x",      "Cortex-M0+", False),
    ("STM32C5",  "stm32c5x",      "Cortex-M33", True),
    ("STM32F0",  "stm32f0x",      "Cortex-M0",  False),
    ("STM32F1",  "stm32f1x",      "Cortex-M3",  False),
    ("STM32F2",  "stm32f2x",      "Cortex-M3",  False),
    ("STM32F3",  "stm32f3x",      "Cortex-M4",  False),
    ("STM32F4",  "stm32f4x",      "Cortex-M4",  False),
    ("STM32F7",  "stm32f7x",      "Cortex-M7",  False),
    ("STM32G0",  "stm32g0x",      "Cortex-M0+", False),
    ("STM32G4",  "stm32g4x",      "Cortex-M4",  False),
    ("STM32H5",  "stm32h5x",      "Cortex-M33", True),
    ("STM32H7",  "stm32h7x",      "Cortex-M7",  False),
    ("STM32L0",  "stm32l0x",      "Cortex-M0+", False),
    ("STM32L1",  "stm32l1x",      "Cortex-M3",  False),
    ("STM32L5",  "stm32l5x",      "Cortex-M33", True),
    ("STM32N6",  "stm32n6x",      "Cortex-M55", True),
    ("STM32U0",  "stm32u0x",      "Cortex-M0+", False),
    ("STM32U3",  "stm32u3x",      "Cortex-M33", True),
    ("STM32U5",  "stm32u5x",      "Cortex-M33", True),
    ("STM32V8",  "stm32v8x",      "Cortex-M85", True),
    ("STM32WBA", "stm32wbax",     "Cortex-M33", True),
    ("STM32WB0", "stm32wb0x",     "Cortex-M0+", False),
    ("STM32WB",  "stm32wbx",      "Cortex-M4",  False),
    ("STM32WL3", "stm32wl3x",     "Cortex-M0+", False),
    ("STM32WL",  "stm32wlx",      "Cortex-M4",  False),
]

# L4 and L4+ share the "STM32L4" prefix; L4+ part numbers look like STM32L4[PQRS]xx.
_L4_PLUS_LETTERS = ("L4P", "L4Q", "L4R", "L4S")


def map_chip(device_name):
    """Map a Device name (e.g. 'STM32H563', 'STM32L4R5') to (cfg, core, trustzone)."""
    if not device_name:
        return None
    dn = device_name.upper().replace(" ", "")
    # CubeProgrammer reports some STM32N6 parts with a malformed Device name
    # that drops the 'M' (e.g. "ST32N657" instead of "STM32N657"). Repair the
    # prefix so the matching below still works and the chip auto-detects.
    if dn.startswith("ST32") and not dn.startswith("STM32"):
        dn = "STM" + dn[2:]
    if dn.startswith("STM32L4"):
        marker = dn[5:8]
        if marker in _L4_PLUS_LETTERS:
            return ("stm32l4plusx", "Cortex-M4", False)
        return ("stm32l4x", "Cortex-M4", False)
    for prefix, cfg, core_name, tz in _CHIP_MAP:
        if dn.startswith(prefix):
            return (cfg, core_name, tz)
    return None


def detect_device_name():
    """Read the Device name by connecting via CubeProgrammer (HOTPLUG)."""
    global _detected_device
    out = core.run_cli(["-c", "port=SWD", "mode=HOTPLUG"], timeout=60)
    for line in out.splitlines():
        if "Device name" in line and ":" in line:
            _detected_device = line.split(":", 1)[1].strip()
            return _detected_device
    return ""
