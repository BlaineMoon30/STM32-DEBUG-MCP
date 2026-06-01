# STM32 Debug MCP Server (한국어)

VSCode + Claude Code에서 STM32 보드를 빌드·플래시·디버깅하는 로컬 MCP 서버입니다.
CubeIDE의 Makefile/CMake 프로젝트를 대상으로 하며, "런타임 중 HardFault가 났는데
직접 디버깅해줘" 같은 자연어 요청에 Claude가 도구를 골라 실행합니다.

대상: VSCode 기반 개발 · STM32CubeIDE Makefile/CMake 프로젝트 · STM32 일부 제품군

Supported OS : Windows

---

## Step 1. 설치

1. **STM32CubeIDE** 설치 — OpenOCD, arm-none-eabi-gdb 제공
2. **STM32CubeCLT** 설치 — STM32_Programmer_CLI, SVD 파일 제공
3. **Python 3.11+** 설치 (Microsoft Store 스텁 말고 python.org 정식본)
   - 설치 시 "Add python.exe to PATH" 체크. 실행은 `py` 사용 권장
4. 패키지 설치:
   ```
   py -m pip install fastmcp pygdbmi
   ```

---

## Step 2. 서버 파일 배치

`stm32_probe_mcp.py` 를 폴더에 저장 (예: `D:/STM32_MCP/`).

파일 상단에서 **이 한 줄만** 본인 프로젝트에 맞게 수정:

```python
BUILD_DIR = r"D:/.../STM32CubeIDE/Debug"   # .elf 가 생성되는 폴더
```

> 나머지 경로(OpenOCD/GDB/CLI/SVD)는 자동 탐색되므로 수정 불필요.

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

---

## Step 5. 사용

VSCode에서 Claude Code 세션을 **새로 시작**(등록 후 반영). `/mcp` 로 도구 확인 후:

```
check_setup 실행해줘            # 경로 자동 탐색 점검 (전부 ✅면 OK)
연결된 probe 알려줘
무슨 칩이야?                    # 칩 자동 감지
프로젝트 빌드해줘               # Makefile/CMake 빌드
보드에 플래시해줘
런타임에 HardFault가 났는데 직접 디버깅해줘
```

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

## 자주 막히는 곳

| 증상 | 해결 |
|------|------|
| `py` 실행 시 `Python`만 출력 | 가짜 Python(스토어 스텁). python.org 정식본 설치 후 `py` 사용 |
| 등록했는데 도구 안 보임 | `--scope user` 로 재등록 + **새 세션** 시작 |
| `✗ Failed to connect` | 서버 직접 실행해 에러 확인 / `fastmcp` 설치 / `py` 사용 |
| 플래시·디버그 실패 | CubeIDE·CubeProgrammer GUI 닫기 (ST-Link 점유 충돌) |
| 경로 자동탐색 실패 | `check_setup` 으로 ❌ 항목 확인 → 환경변수 지정 (아래) |

자동 탐색이 빗나가면 등록 시 환경변수로 지정 (서버 이름 뒤에 `-e`):
```powershell
claude mcp add --scope user stm32-probe ^
  -e STM32_CUBEIDE_ROOT=D:/Tools/ST/STM32CubeIDE_x ^
  -e STM32_SVD_DIR=D:/Tools/ST/STM32CubeCLT_x/STMicroelectronics_CMSIS_SVD ^
  -- cmd /c py D:/STM32_MCP/stm32_probe_mcp.py
```

---

## 다른 PC로 이식

1. CubeIDE + CubeCLT + 정식 Python 설치
2. `py -m pip install fastmcp pygdbmi`
3. 서버 파일 복사 → `BUILD_DIR` 만 수정
4. Step 4 등록 명령 실행
5. 경로는 자동 탐색 → 막히면 `check_setup` 확인

> 수정할 코드는 `BUILD_DIR` 한 줄. 나머지는 자동 탐색됩니다.
