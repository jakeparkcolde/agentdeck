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

**동작**
- 원격에는 아무것도 설치하지 않아요. 짧은 Python 스크립트를 SSH 로 넘겨 실행해요.
- 먼저 `agy --version` 으로 1.1.11 이상인지 확인해요. 옛 버전은 `/usage` 를 프롬프트로 처리해 한도를 쓸 수 있어서 실행하지 않아요.
- 토큰 파일을 읽거나 비공개 API 를 부르지 않아요.
- CLI 가 없거나, 로그인이 안 됐거나, 시간이 초과되면 한도를 "없음"으로 표시해요. 모르는 시간 창이나 이상한 항목은 그것만 건너뛰어요.
- 대시보드는 5시간·주간 창만 그려요. 모델 그룹이 여럿이면 같은 창 중 마지막 그룹 값을 보여줘요.

## English

agentdeck connects over SSH to the computer running agy and reads quota with the official read-only CLI command above.

**Requirements**
- `config.json`: `"agy": {"enabled": true, "host": "<ssh target>"}`
- Remote computer: Python 3, **agy CLI 1.1.11+**, already signed in
- Passwordless `ssh <host>` from this Mac (key auth). With Tailscale SSH, run `ssh <host> true` once in a terminal to pass any browser check first.

**Behaviour**
- Nothing is installed remotely; a short Python script is sent over SSH.
- `agy --version` is checked first; older CLIs may treat `/usage` as a prompt, so they are skipped.
- No credential files or private endpoints are used.
- Missing CLI, no login or timeouts show the quota as unavailable. Unknown windows or malformed entries are skipped individually.
- Only the 5-hour and weekly windows are drawn; with several model groups, the last group's value for each window is shown.
