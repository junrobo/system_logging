# sysinfo.py

PC나 보드의 하드웨어/OS 정보를 한 번에 수집해서, 보기 좋은 단일 HTML 리포트로 만들어 주는 도구입니다.
파일은 `sysinfo.py` 하나이며, 수집기(Linux `sh`, Windows PowerShell)가 파일 안에 내장되어 있습니다.

- 읽기 전용입니다. 대상 시스템에 아무것도 설치하거나 수정하지 않습니다.
- 대상에 파일을 복사하지 않습니다. 수집 스크립트를 stdin으로 흘려 보내 실행합니다.
- 외부 의존성이 없습니다. Python 3 표준 라이브러리만 씁니다.
- HTML은 외부 리소스 없이 하나로 열리고, 라이트/다크 테마를 따라갑니다.

## 요구 사항

| 위치 | 필요한 것 |
|---|---|
| 실행하는 PC | Python 3.8+, (원격 조사 시) `ssh` 클라이언트 |
| Linux 대상 | POSIX `sh` (`/proc`, `/sys`가 있는 Linux) |
| Windows 대상 | PowerShell 5.1 이상 또는 `pwsh` |

## 사용법

```bash
# 이 PC를 조사 -> sysinfo.html
./sysinfo.py

# Linux 보드를 SSH로 조사
./sysinfo.py -p 2200 vela@10.42.0.123 -o r9_sysinfo.html

# Windows PC를 SSH로 조사
./sysinfo.py user@win-pc -o win.html

# HTML 대신 텍스트로 출력
./sysinfo.py --text [host]

# 대상 OS 자동 감지를 건너뛰기
./sysinfo.py --os windows user@win-pc
```

Windows에서 직접 실행할 때는 `python sysinfo.py`를 씁니다.

| 옵션 | 설명 |
|---|---|
| `host` | `[user@]host`. 생략하면 이 PC를 조사합니다. |
| `-p`, `--port` | SSH 포트 |
| `-o`, `--out` | 출력 HTML 경로 (기본 `sysinfo.html`) |
| `--text` | HTML 없이 원본 텍스트를 출력 |
| `--os {auto,linux,windows}` | 대상 OS (기본 `auto`) |

## 지원 환경

| 조사 대상 | 상태 |
|---|---|
| Linux (x86_64, riscv64 등) | 지원. 이 PC(Ubuntu 22.04)와 RISC-V 보드(Ubuntu 24.04)에서 확인했습니다. |
| Windows | 지원하도록 작성했지만 **실제 Windows에서는 아직 실행해 보지 못했습니다.** 모의 환경으로만 검증했습니다. |
| macOS | 수집기가 Linux용 `sh`라서 정보가 대부분 비어 나옵니다. 전용 수집기는 없습니다. |

실행하는 PC의 OS는 상관없습니다. Windows나 Mac에서 실행해도 SSH로 Linux 보드를 조사할 수 있습니다.

## 리포트 내용

상단 요약 카드(호스트, 보드, 아키텍처, CPU, 코어, RAM, OS, 커널, 부하, 가동 시간)와 아래 섹션으로 구성됩니다.

System / Board·Firmware / CPU / Memory / Storage / Network / USB / PCI / GPU·Display·Accelerators / Thermal·Power / Load·Top processes / Software

맨 아래의 접이식 영역에 원본 출력이 들어 있습니다.

### Linux에서 읽는 곳

`/proc`, `/sys`, device-tree(`/proc/device-tree`), DMI(`/sys/class/dmi/id`)를 직접 읽습니다.
`lscpu`, `lsblk`, `ip`, `lsusb`, `lspci` 같은 도구는 있으면 쓰고, 없으면 sysfs로 대체합니다.
그래서 `lsusb`와 `lspci`가 없는 임베디드 보드에서도 동작합니다.

### Windows에서 읽는 곳

PowerShell의 `Get-CimInstance`(`Win32_*`), `Get-PhysicalDisk`, `Get-NetAdapter`, `Get-PnpDevice` 등을 씁니다.

## 원격 조사 준비

### SSH 키 인증

스크립트는 `BatchMode=yes`로 접속합니다. 비밀번호를 물어보지 않고, 키 인증이 안 되어 있으면 실패합니다.
먼저 일반 `ssh`로 키 접속이 되는지 확인하세요.

### Windows 대상에서 OpenSSH Server 켜기

관리자 권한 PowerShell에서 실행합니다.

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

관리자 계정으로 접속할 때는 공개 키를 `C:\ProgramData\ssh\administrators_authorized_keys`에 넣어야 합니다.
일반 계정은 `%USERPROFILE%\.ssh\authorized_keys`를 씁니다.

### OS 자동 감지

원격 대상에서 `uname -s`를 실행해 봅니다. 명령이 없으면(cmd.exe나 PowerShell 기본 셸) Windows로 보고,
MSYS/Cygwin/MinGW 셸이 응답해도 Windows로 처리합니다. 접속 자체가 실패하면 SSH 오류를 그대로 보여 줍니다.

## 동작 방식

1. 대상 OS를 정합니다(자동 감지 또는 `--os`).
2. 내장된 수집 스크립트를 stdin으로 실행합니다. Windows는 base64로 인코딩한 한 줄로 보냅니다.
   cmd.exe의 명령줄 길이 제한과 여러 줄 입력 파싱 문제를 피하기 위해서입니다.
3. 수집기는 `=== 섹션 ===` 아래에 키-값 줄이나 표를 출력합니다. 하위 제목은 `-- 제목 --`입니다.
4. 변환기가 섹션을 나눠서, 키-값은 표로, 나머지는 고정폭 블록으로 렌더링합니다.
   하위 제목이 `[table]`로 끝나면 키-값으로 해석하지 않고 그대로 보여 줍니다.

## 한계

- 온도와 팬 같은 센서 값은 이름만 보여 주는 경우가 많습니다. Windows에서는 CPU 온도에 관리자 권한이 필요하고 기기에 따라 안 나옵니다.
- 일부 Linux 커널은 캐시 정보를 sysfs에 노출하지 않아서 캐시 항목이 비어 있을 수 있습니다.
- 권한이 없으면 `dmesg`, 일부 장치 상세, 프로세스 정보 등이 빠집니다. 도구는 root를 요구하지 않습니다.
- 리포트는 실행 시점의 스냅샷입니다.
- JSON 출력이나 정기 수집 기능은 없습니다.

## 수집 항목 추가하기

- Linux: 파일 안의 `SH` 문자열에 `sec "이름"`으로 섹션을 추가하고 `kv 키 값`으로 출력합니다.
- Windows: `PS` 문자열에 `Sec '이름'`을 추가하고 `KV '키' 값`을 씁니다. 키는 공백 없는 한 단어로 써야 키-값 표로 인식됩니다.
- 표 형태의 출력은 `Sub '제목 [table]'` 아래에 두세요.
- 요약 카드에 넣을 값은 `facts()` 함수에서 키 이름으로 읽어 옵니다.

## 파일

| 파일 | 설명 |
|---|---|
| `sysinfo.py` | 도구 본체 (수집기 + HTML 변환기) |
| `r9_sysinfo.html`, `local_sysinfo.html` | 생성한 리포트 예시 |
