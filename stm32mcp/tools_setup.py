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

    lines.append("\n=== Toolchain ===")
    tc = core.get_toolchain()
    lines.append(f"Active toolchain [{core.toolchain_source()}]: {tc.upper()}")
    iarbuild = core.PATHS.get("iarbuild")
    iar_ok = "OK" if (iarbuild and os.path.exists(iarbuild)) else "MISSING (IAR not installed?)"
    lines.append(f"IAR iarbuild: {iar_ok}\n   {iarbuild}")
    if tc == "iar":
        ewp = core.find_iar_project()
        lines.append(f"IAR project (.ewp): {ewp if ewp else 'MISSING - use set_iar_project'}")

    lines.append("")
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


@mcp.tool
def set_toolchain(name: str = "") -> str:
    """빌드 툴체인을 GCC 또는 IAR 로 강제 지정합니다 (비우면 자동 감지로 되돌림).
    Force the build toolchain to GCC or IAR for this session (empty = auto-detect).

    사용 예 / Use for: "IAR로 빌드하게 해줘", "GCC로 바꿔줘", "툴체인 IAR",
    "use IAR", "switch to GCC", "set toolchain".
    기본은 자동 감지(빌드 폴더에 Makefile -> GCC, 근처 .ewp -> IAR)이며,
    감지가 틀릴 때만 이 도구로 고정하세요.
    Auto-detect is the default (Makefile -> GCC, nearby .ewp -> IAR); use this
    only to override when detection is wrong.

    Args:
        name: "gcc" 또는 "iar" / "gcc" or "iar". 비우면 override 해제(자동).
    """
    n = (name or "").strip().lower()
    if n and n not in ("gcc", "iar", "cmake"):
        return f"Error: unknown toolchain {name!r}. Use 'gcc', 'iar', or 'cmake' (or empty for auto)."
    core.set_toolchain_override(n)
    if not n:
        return (f"툴체인 override 해제 / cleared. Now auto-resolves to: "
                f"{core.get_toolchain().upper()} [{core.toolchain_source()}]")
    extra = ""
    if n == "iar":
        iarbuild = core.PATHS.get("iarbuild")
        if not iarbuild or not os.path.exists(iarbuild):
            extra = "\n   Warning: iarbuild.exe not found - set STM32_IAR_ROOT/STM32_IARBUILD."
        ewp = core.find_iar_project()
        extra += f"\n   IAR project: {ewp}" if ewp else "\n   IAR project: (not found - use set_iar_project)"
    return f"툴체인 설정됨 / toolchain set: {n.upper()}{extra}"


@mcp.tool
def set_iar_project(path: str = "") -> str:
    """IAR 프로젝트 파일(.ewp) 경로를 이 세션 동안 지정합니다 (비우면 해제).
    Set the IAR project file (.ewp) for this session (empty clears the override).

    사용 예 / Use for: "IAR 프로젝트를 D:/proj/App.ewp 로 지정", "set the IAR project".
    iarbuild 는 .eww 워크스페이스가 아니라 .ewp 프로젝트 파일을 빌드합니다.
    iarbuild builds the .ewp project file (not the .eww workspace).

    Args:
        path: .ewp 파일 경로. 비우면 자동 탐색/환경변수로 되돌립니다.
    """
    if not path:
        core.set_iar_project_override(None)
        found = core.find_iar_project()
        return ("IAR 프로젝트 override 해제 / cleared. Now resolves to: "
                + (found or "(none found - set STM32_IAR_PROJECT or run from the project folder)"))
    norm = path.replace("\\", "/")
    if not norm.lower().endswith(".ewp"):
        return f"Error: expected a .ewp project file, got {path!r}."
    core.set_iar_project_override(norm)
    if not os.path.isfile(norm):
        return (f"경고/Warning: file does not exist yet: {norm}\n"
                "그래도 설정은 적용했습니다 / set anyway.")
    return (f"IAR 프로젝트 설정됨 / IAR project set:\n   {norm}\n"
            "툴체인이 자동으로 IAR 로 감지됩니다 (필요시 set_toolchain('iar')).")
