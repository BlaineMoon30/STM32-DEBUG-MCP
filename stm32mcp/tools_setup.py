# SPDX-License-Identifier: MIT
"""tools_setup - diagnostics and build-directory configuration tools."""

import os

from . import core

mcp = core.mcp


@mcp.tool
def check_setup() -> str:
    """자동 탐색된 도구 경로들을 보여줍니다 (문제 진단용).
    Show the auto-detected tool paths (for troubleshooting).

    사용 예 / Use for: "설정 확인해줘", "경로 제대로 잡혔어?",
    "check the setup", "are the paths correct?".
    디버깅이 안 될 때 가장 먼저 호출해 어떤 경로가 비었는지 확인하세요.
    Call this first when debugging fails, to see which path is missing.
    """
    lines = ["=== Auto-detected paths ==="]
    for k, label in [("cli", "CubeProgrammer CLI"), ("openocd", "OpenOCD"),
                     ("gdb", "arm-none-eabi-gdb"), ("scripts", "OpenOCD scripts")]:
        v = core.PATHS.get(k)
        ok = "OK" if (v and os.path.exists(v)) else "MISSING"
        lines.append(f"{label}: {ok}\n   {v}")
    bdir = core.get_build_dir()
    src = core.build_dir_source()
    if bdir:
        status = "OK" if os.path.isdir(bdir) else "MISSING"
        lines.append(f"\nBUILD_DIR [{src}]: {status}\n   {bdir}")
    else:
        lines.append("\nBUILD_DIR [NOT SET]: use set_build_dir, "
                     "or run from the project folder, or set STM32_BUILD_DIR")
    elf = core.find_elf()
    lines.append(f"ELF: {'OK ' + elf if elf else 'MISSING (no .elf / build dir not set)'}")
    lines.append(f"pygdbmi: {'installed' if core.GdbController else 'MISSING - py -m pip install pygdbmi'}")
    return "\n".join(lines)


@mcp.tool
def set_build_dir(path: str = "") -> str:
    """빌드 폴더(.elf 위치)를 이 세션 동안 지정/변경합니다.
    Set or change the build folder (.elf location) for this session.

    사용 예 / Use for: "빌드 폴더 D:/proj/Debug 로 바꿔줘", "이 프로젝트로 빌드 경로 지정",
    "set the build dir to ...", "use this Debug folder".
    빈 값으로 호출하면 자동 탐색/환경변수 기준으로 되돌립니다(override 해제).
    Call with empty path to clear the override (revert to auto/env detection).

    Args:
        path: Debug/Release 폴더 경로 (.elf 가 있는 곳). 비우면 override 해제.
    """
    if not path:
        core.set_build_dir_override(None)
        return f"빌드 폴더 override 해제 / cleared. Now resolves to:\n   {core.get_build_dir()}"
    norm = path.replace("\\", "/")
    if not os.path.isdir(norm):
        core.set_build_dir_override(norm)
        return (f"경고/Warning: folder does not exist yet: {norm}\n"
                "그래도 설정은 적용했습니다 (빌드 후 생길 수도 있음).\n"
                "Set anyway (it may be created by a build).")
    core.set_build_dir_override(norm)
    elf = core.find_elf()
    elf_s = f"\n   ELF: {elf}" if elf else "\n   (.elf not found yet - build first)"
    return f"빌드 폴더 설정됨 / build dir set:\n   {norm}{elf_s}"


@mcp.tool
def show_build_dir() -> str:
    """현재 사용 중인 빌드 폴더와 그 출처를 보여줍니다.
    Show the active build folder and how it was resolved.

    사용 예 / Use for: "지금 빌드 폴더 어디야?", "어느 .elf 쓰고 있어?",
    "what's the current build dir?".
    """
    bdir = core.get_build_dir()
    if not bdir:
        return "Build dir : (not set)\n\n" + core.no_build_dir_msg()
    elf = core.find_elf()
    elf_s = elf if elf else "(no .elf found - build first)"
    return (f"Build dir : {bdir}\n"
            f"Source    : {core.build_dir_source()}\n"
            f"ELF       : {elf_s}")
