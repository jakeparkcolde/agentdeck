# Antigravity(agy) 한도 / Antigravity quota

## 한국어

agentdeck 은 agy 를 쓰는 컴퓨터에 SSH 로 접속해 공식 CLI 의 읽기 전용 명령으로 한도를 가져와요.

```
agy -p /usage --output-format json
```

**필요 조건**
- `config.json` 에 `"agy": {"enabled": true, "host": "<ssh 대상>"}` (예: `~/.ssh/config` 의 호스트 이름)
- 원격 컴퓨터: Python 3, **agy CLI 1.1.11 이상**, agy 에 미리 로그인
- 이 맥에서 비밀번호 없이 `ssh <호스트>` 가 되어야 해요(키 인증). Tailscale SSH 를 쓰면 처음 한 번 브라우저 확인이 뜰 수 있으니, 터미널에서 `ssh <호스트> true` 로 먼저 통과해 두세요.

**수집기 설치 (권장, 한 번만)**

```
.venv/bin/python remote_agy.py --install     # 지우기: --uninstall
```

agy 컴퓨터의 로그인 세션에 작은 자동 실행 작업(launchd) 하나를 두고, 5분마다 위 명령을 실행해 `~/.cache/agentdeck/agy_usage.json` 에 저장해요. 덱은 SSH 로 이 파일만 읽어요.
- **왜 필요한가:** agy 는 SSH 로 실행되면 키체인 대신 별도 파일에 로그인을 둬요. 같은 컴퓨터에서 agy 를 오래 띄워 두면 매시간 토큰을 갱신하는데, 그 직후 SSH 쪽 로그인이 풀리는 일이 잦았어요(실측: 갱신 몇 초 뒤 풀림). 수집기는 그 컴퓨터의 평소 로그인을 같이 써서 따로 로그인할 일이 없어요.
- 설치하려면 그 컴퓨터에 화면 로그인(데스크톱 세션)이 있어야 해요.
- 수집 결과가 15분 넘게 안 바뀌면(수집기를 지웠거나 멈춤) 아래의 직접 조회로 돌아가요.

**동작**
- 수집기가 없으면 짧은 Python 스크립트를 SSH 로 넘겨 실행해요 (원격에 설치하는 것 없음).
- 먼저 `agy --version` 으로 1.1.11 이상인지 확인해요. 옛 버전은 `/usage` 를 프롬프트로 처리해 한도를 쓸 수 있어서 실행하지 않아요.
- 토큰 파일을 읽거나 비공개 API 를 부르지 않아요.
- **로그인이 풀리면** AGY 칸이 노란 테두리의 "로그인"으로 바뀌어요. 수집기를 쓰면 그 컴퓨터에서 agy 에 로그인하면 돼요. 수집기 없이 쓸 때는 그 칸을 **길게 누르면** 맥에 터미널 창이 열리고 원격 agy 가 떠요. 나오는 주소로 직접 로그인하고 agy 를 종료하면, 다음 갱신 때 한도가 돌아와요. agy 는 SSH 접속용 로그인을 따로 보관해서, 그 컴퓨터 앞에서는 로그인돼 있어도 덱에서는 풀려 보일 수 있어요. 비밀번호와 토큰은 대시보드가 다루지 않아요.
- CLI 가 없거나 시간이 초과되면 한도를 "없음"으로 표시해요. 모르는 시간 창이나 이상한 항목은 그것만 건너뛰어요.
- 대시보드는 5시간·주간 창만 그려요. 모델 그룹이 여럿이면 창마다 **가장 빠듯한(남은 게 적은) 그룹** 값을 보여줘요.

## English

agentdeck connects over SSH to the computer running agy and reads quota with the official read-only CLI command above.

**Requirements**
- `config.json`: `"agy": {"enabled": true, "host": "<ssh target>"}`
- Remote computer: Python 3, **agy CLI 1.1.11+**, already signed in
- Passwordless `ssh <host>` from this Mac (key auth). With Tailscale SSH, run `ssh <host> true` once in a terminal to pass any browser check first.

**Collector (recommended, once)**

```
.venv/bin/python remote_agy.py --install     # remove: --uninstall
```

Adds one small launchd job to the agy computer's login session. Every 5 minutes it runs the command above and saves `~/.cache/agentdeck/agy_usage.json`; the deck only reads that file over SSH.
- **Why:** over SSH, agy keeps its login in a separate file instead of the keychain. When long-running agy sessions on that computer refresh their token every hour, the SSH login was often signed out seconds later (measured). The collector shares that computer's normal login, so there is nothing extra to sign in to.
- Installing needs a logged-in desktop session on that computer.
- If the cached result is older than 15 minutes (collector removed or stuck), the deck falls back to the direct mode below.

**Behaviour**
- Without the collector, a short Python script is sent over SSH (nothing installed remotely).
- `agy --version` is checked first; older CLIs may treat `/usage` as a prompt, so they are skipped.
- No credential files or private endpoints are used.
- **If the login expires**, the AGY tiles turn into a yellow-bordered "Log in". With the collector, sign in to agy on that computer. Without it, **Hold** one and a Terminal window opens on your Mac running the remote agy; sign in with the URL it shows, quit agy, and the quota returns on the next refresh. agy keeps a separate login for SSH sessions, so it can look signed in at that computer while the deck sees it signed out. The dashboard never handles your password or tokens.
- Missing CLI or timeouts show the quota as unavailable. Unknown windows or malformed entries are skipped individually.
- Only the 5-hour and weekly windows are drawn; with several model groups, each window shows the **tightest** (least remaining) group.
