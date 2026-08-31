# SPDX-License-Identifier: MIT
"""tools_probe - probe info, build, flash, erase, and chip detection tools."""

import os

from . import core
from . import chips

mcp = core.mcp


@mcp.tool
def list_probes() -> str:
    """연결된 ST-Link probe 목록(SN, 펌웨어)을 조회합니다.
    List connected ST-Link probes (serial number, firmware).

    사용 예 / Use for: "연결된 probe 알려줘", "어떤 보드 연결됐어?",
    "what device/board/probe is connected?".
    """
    return core.run_cli(["-l"])


@mcp.tool
def probe_details() -> str:
    """연결된 STM32 칩 상세(보드명/전압/Device ID/Flash/CPU)를 읽습니다.
    Read connected-chip details (board / voltage / Device ID / Flash / CPU).

    사용 예 / Use for: "어떤 칩이야?", "보드 사양 알려줘",
    "what chip is this", "board specs".
    HOTPLUG 모드라 실행 중인 펌웨어를 건드리지 않습니다.
    HOTPLUG: running firmware is left untouched.
    """
    return core.run_cli(["-c", "port=SWD", "mode=HOTPLUG"])


@mcp.tool
def build(config: str = "", clean: bool = False) -> str:
    """프로젝트를 빌드해 펌웨어 이미지를 생성합니다 (GCC make 또는 IAR EWARM 자동 선택).
    Build the project to produce the firmware image (GCC make or IAR EWARM,
    auto-selected by toolchain).

    사용 예 / Use for: "빌드해줘", "컴파일해줘", "build", "compile",
    "IAR로 빌드해줘", "Release 빌드", "전체 다시 빌드".

    툴체인은 자동 감지됩니다: 빌드 폴더에 Makefile 이 있으면 GCC, 근처에
    IAR 프로젝트(.ewp)가 있으면 IAR. set_toolchain 으로 강제할 수 있습니다.
    The toolchain is auto-detected: a Makefile in the build dir -> GCC; an IAR
    project (.ewp) nearby -> IAR. Force it with set_toolchain.

    GCC : make -j4 [clean] all          (.elf 생성 / produces .elf)
    IAR : iarbuild <proj.ewp> [-make|-build] <config>   (.out 생성 / produces .out)

    Args:
        config: IAR 빌드 구성 이름(예: "Debug"/"Release"), 비우면 "Debug".
                GCC 에서는 무시됩니다. IAR configuration name; ignored for GCC.
        clean:  True 면 전체 다시 빌드 (GCC: make clean all, IAR: -build).
                True forces a full rebuild instead of an incremental one.
    """
    toolchain = core.get_toolchain()

    if toolchain == "iar":
        ewp = core.find_iar_project()
        if not ewp:
            return ("IAR 프로젝트(.ewp)를 찾지 못했습니다.\n"
                    "Could not find an IAR project (.ewp).\n"
                    "set_iar_project 로 지정하거나, STM32_IAR_PROJECT 환경변수를 쓰거나,\n"
                    "프로젝트 폴더에서 실행하세요 / point to it with set_iar_project, set "
                    "STM32_IAR_PROJECT, or run from the project folder.")
        cfg = config or "Debug"
        action = "-build" if clean else "-make"
        out = core.run_iarbuild(ewp, cfg, action=action, timeout=900)
        img = core.find_elf()
        produced = f"\n\nProduced image: {img}" if img else "\n\nWarning: no .out/.elf found"
        return f"[IAR iarbuild {action} {cfg}]  project: {ewp}\n\n{out}{produced}"

    # --- CMake (Ninja 또는 Makefiles 생성기 모두) / generator-agnostic ---
    if toolchain == "cmake":
        bdir = core.get_build_dir()
        if not bdir:
            return core.no_build_dir_msg()
        if not os.path.isdir(bdir):
            return f"Error: build folder not found: {bdir}\n\n" + core.no_build_dir_msg()
        args = ["cmake", "--build", bdir]
        if clean:
            args.append("--clean-first")
        if config:
            args += ["--config", config]
        out = core.run(args, timeout=600, env=core.gnu_tools_env())
        elf = core.find_elf()
        return out + (f"\n\nProduced ELF: {elf}" if elf else "\n\nWarning: no .elf found")

    # --- GCC / Makefile ---
    bdir = core.get_build_dir()
    if not bdir:
        return core.no_build_dir_msg()
    if not os.path.isdir(bdir):
        return f"Error: build folder not found: {bdir}\n\n" + core.no_build_dir_msg()
    targets = ["clean", "all"] if clean else ["all"]
    out = core.run(["make", "-j4", *targets], cwd=bdir, timeout=600, env=core.gnu_tools_env())
    elf = core.find_elf()
    return out + (f"\n\nProduced ELF: {elf}" if elf else "\n\nWarning: no .elf found")


@mcp.tool
def flash(elf_path: str = "", run_after: bool = True, full_erase: bool = False) -> str:
    """빌드된 .elf 를 보드에 플래시합니다.
    Flash the built .elf to the board.

    사용 예 / Use for: "플래시해줘", "보드에 구워줘", "업로드해줘",
    "flash the board", "upload firmware".
    run_after=False 면 플래시 후 멈춘 채로 둡니다(디버깅 직전에 유용).
    run_after=False leaves the target halted after flashing (handy before debugging).

    Erase 동작 / Erase behavior:
      - 기본: 쓰는 영역만 자동 erase 후 기록(빠름, 보통 충분).
        Default: only the written region is auto-erased (fast; usually enough).
      - full_erase=True: 쓰기 전에 칩 전체 erase(-e all).
        "전체 지우고 플래시", "칩 초기화 후 굽기", "full erase" 요청 시 사용.
        full_erase=True erases the whole chip first; clears leftover data (slower).

    참고/Note: .elf 는 배치 주소를 포함하므로 시작 주소를 지정하지 않습니다
    (주소 지정은 .bin 일 때만 필요). An .elf embeds load addresses, so no start
    address is given; addresses are only needed for raw .bin files.

    Args:
        elf_path: 플래시할 .elf (비우면 자동 탐색) / .elf to flash.
        run_after: True 면 플래시 후 리셋·실행 / reset and run after flashing.
        full_erase: True 면 쓰기 전 전체 칩 erase / full chip erase before writing.
    """
    elf = elf_path or core.find_elf()
    if not elf or not os.path.exists(elf):
        if not core.get_build_dir():
            return core.no_build_dir_msg()
        return f"Error: .elf not found ({elf!r}). Build first, or check the build dir."
    args = ["-c", "port=SWD"]
    if full_erase:
        args += ["-e", "all"]
    args += ["-w", elf, "-v"]
    if run_after:
        args.append("-rst")
    out = core.run_cli(args, timeout=240)
    mode = "full-erase then flash" if full_erase else "flash (auto-erase)"
    run_s = " + reset/run" if run_after else " (halted)"
    return f"[{mode}{run_s}]  ELF: {elf}\n\n{out}"


@mcp.tool
def erase_chip() -> str:
    """칩 전체 Flash를 완전히 지웁니다(쓰기 없이 erase만).
    Fully erase the chip's Flash (erase only, no write).

    사용 예 / Use for: "전체 지워줘", "칩 초기화해줘", "Flash 전부 지워",
    "erase everything", "erase all Flash".
    펌웨어를 쓰지 않고 비우기만 합니다(이후 flash 로 기록).
    Empties Flash without writing firmware (follow with flash to program).
    쓰면서 지우려면 flash(full_erase=True) 를 사용하세요.
    To erase while writing, use flash(full_erase=True).
    """
    return core.run_cli(["-c", "port=SWD", "-e", "all"], timeout=180)


@mcp.tool
def detect_chip() -> str:
    """연결된 STM32 칩을 감지하고, 매칭되는 OpenOCD target cfg 를 알려줍니다.
    Detect the connected STM32 chip and report the matching OpenOCD target cfg.

    사용 예 / Use for: "무슨 칩이야?", "어떤 cfg 써야 해?", "칩 감지해줘",
    "what chip is this", "which cfg should I use", "detect the chip".
    디버그 세션을 시작하지 않고 감지만 합니다(start_debug 전 확인용).
    Detection only; does not start a debug session (handy before start_debug).
    """
    dev = chips.detect_device_name()
    if not dev:
        return "Could not detect a chip. Check the board connection / ST-Link contention."
    mapped = chips.map_chip(dev)
    if not mapped:
        return (f"Device name: {dev}\n-> No matching cfg. "
                "Specify manually with start_debug(chip='stm32xxx').")
    cfg, core_name, tz = mapped
    scripts = core.PATHS.get("scripts") or ""
    cfg_full = os.path.join(scripts, "target", cfg + ".cfg")
    exists = "present" if os.path.exists(cfg_full) else "NOT in this OpenOCD"
    return (f"Device name : {dev}\n"
            f"target cfg  : {cfg}.cfg ({exists})\n"
            f"core        : {core_name}\n"
            f"TrustZone   : {'yes' if tz else 'no'}")
