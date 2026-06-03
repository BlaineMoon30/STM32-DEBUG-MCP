# STM32 Debug MCP Server (한국어)

VSCode + Claude Code(또는 VSCode + Codex)에서 STM32 보드를 빌드·플래시·디버깅하는
로컬 MCP 서버입니다. CubeIDE의 Makefile/CMake 프로젝트를 대상으로 하며, "런타임 중
HardFault가 났는데 직접 디버깅해줘" 같은 자연어 요청에 AI 에이전트가 도구를 골라 실행합니다.

대상: VSCode 기반 개발(Claude Code 또는 Codex) · STM32CubeIDE Makefile/CMake 프로젝트 · STM32 일부 제품군

Supported OS : Windows

---

## Step 1. 설치

1. **STM32CubeIDE** 설치 — OpenOCD, arm-none-eabi-gdb 제공
   - https://www.st.com/en/development-tools/stm32cubeide.html
2. **STM32CubeCLT** 설치 — STM32_Programmer_CLI, SVD 파일 제공
   - https://www.st.com/en/development-tools/stm32cubeclt.html
3. **Python 3.11+** 설치 (Microsoft Store 스텁 말고 python.org 정식본) — 권장 3.14.5
   - https://www.python.org/downloads/
   - 설치 시 "Add python.exe to PATH" 체크. 실행은 `py` 사용 권장
4. 패키지 설치:
   ```
   py -m pip install fastmcp pygdbmi
   ```

---

## Step 2. 서버 파일 배치

아래 **두 가지**를 같은 폴더에 나란히 복사하세요 (예: `D:/STM32_MCP/`):

```
D:/STM32_MCP/
├─ stm32_probe_mcp.py     # 진입점 — 이 파일을 실행/등록
└─ stm32mcp/              # 실제 도구가 든 패키지 (.py 옆에 같이 둘 것)
   ├─ core.py  chips.py  svd.py
   └─ tools_setup.py  tools_probe.py  tools_debug.py
      tools_watch.py  tools_svd.py  tools_hotplug.py
```

진입점이 자기 폴더를 `sys.path` 에 추가하므로 Claude Code가 어느 디렉터리에서
실행하든 `stm32mcp/` 를 찾습니다 — 두 파일을 항상 같이 두기만 하면 됩니다.

**코드 수정 불필요** — `.elf` 가 생성되는 빌드 폴더는 이제 실행 시점에 자동으로
다음 우선순위로 결정됩니다:

1. **런타임 지정** — Claude에게 그냥 말하세요: *"빌드 폴더를 D:/myproj/Debug 로 지정해줘"*
   (`set_build_dir` 도구 호출, 해당 세션 동안 적용)
2. **`STM32_BUILD_DIR` 환경변수** — 등록 시 지정 (Step 4 / 자주 막히는 곳 참고)
3. **자동 탐색** — 현재 작업 폴더에서 `.elf` 가 든 폴더를 찾음
   (`Debug/` → `Release/` → 가장 얕은 경로 순으로 우선)
4. 못 찾으면 도구가 지정해달라고 안내합니다 — **하드코딩 경로 없음**.

> 나머지 경로(OpenOCD/GDB/CLI/SVD)도 자동 탐색되므로 수정 불필요.
> 언제든 *"지금 빌드 폴더 어디야?"* 로 현재 경로를 확인하세요 (`show_build_dir`).

---

## Step 3. 동작 확인

```powershell
py D:/STM32_MCP/stm32_probe_mcp.py
```

FastMCP 배너가 뜨면 정상 → `Ctrl+C` 로 종료.

---

## Step 4. Claude Code에 등록

```powershell
claude mcp add --scope user stm32-probe -- cmd /c py D:/STM32_MCP/stm32_probe_mcp.py
```

- `--scope user` : 모든 폴더에서 사용 가능
- `cmd /c py` : Windows 필수 (그냥 `python`은 실패)

연결 확인:
```powershell
claude mcp list      # "stm32-probe ... ✓ Connected" 확인
```

### Codex에 등록

같은 서버를 Codex CLI에서도 등록할 수 있습니다. 터미널에서 입력:
```powershell
codex mcp add stm32-probe -- cmd /c py D:\STM32_MCP\stm32_probe_mcp.py
```

또는 VSCode의 Codex 플러그인에서 아래 명령을 입력해 실행:
```
codex mcp add stm32-probe -- cmd /c py D:\STM32_MCP\stm32_probe_mcp.py
```

---

## Step 5. 사용

VSCode에서 Claude Code 세션을 **새로 시작**(등록 후 반영). `/mcp` 로 도구 확인 후:

```
check_setup 실행해줘            # 경로 자동 탐색 + 빌드 폴더 출처 점검
지금 빌드 폴더 어디야?          # 현재 빌드 폴더 확인 (show_build_dir)
빌드 폴더를 D:/proj/Debug 로 지정해줘   # 내 프로젝트로 지정 (set_build_dir)
연결된 probe 알려줘
무슨 칩이야?                    # 칩 자동 감지
프로젝트 빌드해줘               # Makefile/CMake 빌드
보드에 플래시해줘
런타임에 HardFault가 났는데 직접 디버깅해줘
세션 없이 0x20000000 메모리 읽어줘        # HotPlug, 멈추지 않고
세션 없이 운영 중 보드 SPI1 해석해줘      # HotPlug 페리페럴
```

### HotPlug: 디버그 세션 없이 운영 중인 보드 확인
`hotplug_read_memory` 와 `hotplug_read_peripheral` 는 CubeProgrammer(`mode=HOTPLUG`)로
붙어서 OpenOCD/GDB **없이**, 이상적으로는 펌웨어를 **멈추지 않고** 메모리를 읽거나
페리페럴을 해석합니다 — 이미 현장에서 돌고 있는 보드를 가볍게 들여다볼 때 유용합니다.

> 주의: 코어 레지스터(R0–R15/PC/SP)는 HotPlug로 **안정적으로 못 읽습니다**
> (디버그 세션의 `read_registers` 사용). 일부 환경에선 HotPlug가 그래도 halt/reset
> 될 수 있으니 본인 보드에서 비침습 여부를 확인하세요.

### 예제: "런타임 중 HardFault, 직접 디버깅해줘"
이 한마디로 Claude가 아래를 자동 수행합니다.
1. `build` → `flash(run_after=False)` — 빌드 후 굽고 멈춘 채로
2. `start_debug` — 칩 자동 감지 + 세션 시작
3. `set_breakpoint HardFault_Handler` → `run`
4. HardFault 진입 시 `read_registers` — 스택된 PC/LR, 폴트 시점 파악
5. `read_peripheral("SCB")` — CFSR/HFSR 등 폴트 상태 레지스터를 비트 의미로 해석
   (예: `CFSR.IMPRECISERR`, `BFAR/MMFAR` 폴트 주소) → 원인 위치 추적
6. `where` / 소스 대조로 어떤 코드·접근이 폴트를 냈는지 진단
7. `stop_debug` — 마무리

> HardFault는 이미 정지 상태라 레지스터·스택을 그대로 분석하기 좋습니다.
> 핵심 단서: SCB의 CFSR(폴트 원인), HFSR, 그리고 BFAR/MMFAR(폴트가 난 주소).

---

## 알려진 이슈

### STM32N6 (예: STM32N6-DK): `reset` 하지 말고 펌웨어 다시 실행

N6은 RAM 부팅(내부 유저 플래시 없음)이라 `reset` 으로는 RAM에 올린 ELF가 **다시
실행되지 않고**, 코어가 HardFault로 빠질 수도 있습니다. N6 보드에서 펌웨어를 다시
실행하려면 **리셋하지 말고** 아래처럼 하세요:

1. **리셋하지 말고**, 디버그 세션을 **새로** 연 뒤 코어가 **halted** 상태인지 확인.
2. halted 상태에서 `load_image` 로 RAM에 ELF를 다시 로드.
3. **ELF entry point** 로 `set_pc`.
4. `cont` 로 실행.
5. 실행 확인은 잠깐 `halt` 해서 **HardFault가 아닌지** 본 뒤 다시 `cont`.

> 요약: 새 세션 → halted → `load_image`(RAM) → `set_pc`(entry point) → `cont`,
> 확인은 잠깐 `halt` → 점검 → `cont`. N6에서는 `reset` 을 피하세요.

---

## 자주 막히는 곳

| 증상 | 해결 |
|------|------|
| `py` 실행 시 `Python`만 출력 | 가짜 Python(스토어 스텁). python.org 정식본 설치 후 `py` 사용 |
| 등록했는데 도구 안 보임 | `--scope user` 로 재등록 + **새 세션** 시작 |
| `✗ Failed to connect` | 서버 직접 실행해 에러 확인 / `fastmcp` 설치 / `py` 사용 |
| 플래시·디버그 실패 | CubeIDE·CubeProgrammer GUI 닫기 (ST-Link 점유 충돌) |
| 경로 자동탐색 실패 | `check_setup` 으로 ❌ 항목 확인 → 환경변수 지정 (아래) |
| "빌드 폴더를 찾지 못했습니다" | *"빌드 폴더를 .../Debug 로 지정해줘"*, 프로젝트 폴더에서 실행, 또는 `STM32_BUILD_DIR` 설정 |

자동 탐색이 빗나가면 등록 시 환경변수로 지정 (서버 이름 뒤에 `-e`):
```powershell
claude mcp add --scope user stm32-probe ^
  -e STM32_CUBEIDE_ROOT=D:/Tools/ST/STM32CubeIDE_x ^
  -e STM32_SVD_DIR=D:/Tools/ST/STM32CubeCLT_x/STMicroelectronics_CMSIS_SVD ^
  -e STM32_BUILD_DIR=D:/myproj/STM32CubeIDE/Debug ^
  -- cmd /c py D:/STM32_MCP/stm32_probe_mcp.py
```

> `STM32_BUILD_DIR` 은 선택사항 — 생략하면 자동 탐색이 `.elf` 를 찾거나,
> 실행 중 Claude에게 빌드 폴더를 말해주면 됩니다(`set_build_dir`).

---

## 다른 PC로 이식

1. CubeIDE + CubeCLT + 정식 Python 설치
2. `py -m pip install fastmcp pygdbmi`
3. `stm32_probe_mcp.py` **와** `stm32mcp/` 폴더를 함께 복사 (코드 수정 불필요)
4. Step 4 등록 명령 실행
5. 경로는 자동 탐색 → 막히면 `check_setup` 확인. 프로젝트 지정은
   *"빌드 폴더를 .../Debug 로 지정해줘"*, `STM32_BUILD_DIR`, 또는 프로젝트 폴더에서 실행.

> 이제 수정할 코드가 없습니다 — 빌드 폴더와 모든 도구 경로가 자동으로 결정됩니다.
