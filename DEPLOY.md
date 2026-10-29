# 배포 가이드 — 남의 서버에 AgentTrading 관리 화면 올리기

이 문서는 **운영자**용이다. 지금 배포되는 것은 관리 화면(Streamlit)뿐이고, 에이전트 런타임(shadow·paper·live)은 다음 단계(W2)에서 같은 이미지에 붙는다. 설계 근거는 프로젝트 볼트의 `design/platform-architecture.md` 다.

## 무엇이 배포되고 무엇이 아닌가

| 배포됨 | 배포되지 않음(각자 준비) |
|---|---|
| 코드(`src/`), 의존성 잠금(`uv.lock`), 테마(`.streamlit/config.toml`) | 시장 데이터(`data/`) — BitMEX 공개 체결 테이프 등은 README 의 수집 절차로 직접 받는다 |
| | 거래소 API 키(`.env`) — ui 컨테이너에는 기본적으로 주지 않는다 |
| | OIDC 비밀값(`.streamlit/secrets.toml`) — 외부 공개 시에만 |

- 코드에 들어 있는 전략 설명·근거 수치의 원본 데이터는 **개인 비공개 연구 조건**으로 받은 것이다. 데이터·파생 산출물은 재배포하지 않는다.
- 거래소 이용 가능 여부·세무는 운영자 거주지 기준으로 각자 확인한다(예: 프로젝트 조사 문서 기준 Bybit 는 한국 거주자에게 FIU 미신고 해외 사업자다 — 2차 자료, 직접 확인 필요).
- 이 소프트웨어는 투자 조언이 아니며, 실거래 손실은 운영자 책임이다.

## 1. 로컬에서 그대로 쓰기 (가장 쉬움)

```bash
uv sync --group dev            # Python 3.12 를 받아 .venv 를 만든다 (.python-version)
uv run python -m src.ui.serve  # http://127.0.0.1:8501 — 인증 없음, 내 컴퓨터에서만
```

`pip install -r requirements.txt` 경로는 아직 의존성 **상한이 없어** 새 환경에서 pandas 3 이 깔릴 수 있다(판정 경로 테스트 37개가 깨진다). `requirements.txt` 를 `uv export` 로 재생성하기 전까지는 `uv` 경로를 쓴다.

원격 서버에 두고 혼자 보려면 **SSH 터널**이 가장 간단하다: 서버에서 위처럼 127.0.0.1 로 띄우고 `ssh -L 8501:127.0.0.1:8501 user@server` 뒤 로컬 브라우저로 연다. 서버 쪽은 여전히 loopback bind 라 인증이 필요 없다.

## 2. Docker 로 띄우기

```bash
docker compose up -d --build
curl -fsS http://127.0.0.1:8501/_stcore/health   # ok
```

기본값은 host 의 **127.0.0.1 에만** 공개한다. 컨테이너 안에서는 0.0.0.0 bind 라 런처(`src.ui.serve`)가 인증 설정을 요구하고, 기본 `AT_AUTH_REQUIRED=1` 이라 OIDC(3절)와 허용 이메일을 채우기 전에는 화면에 **"인증 설정이 필요하다"** 안내만 나오고 아무도 들어오지 못한다(컨테이너는 떠 있고 health 는 `ok`).

동작하는 조합 두 가지:

| 쓰임 | 설정 | 결과 |
|---|---|---|
| 혼자, SSH 터널로만 접근 | `AT_AUTH_REQUIRED=0 AT_AUTH_EXTERNAL=1 docker compose up -d` (publish 는 기본 127.0.0.1 유지) | 인증 없음. 접근 통제는 터널·호스트 방화벽 책임 |
| 여럿, 또는 인터넷 공개 | 3절대로 `secrets.toml` + `AT_AUTH_ALLOWED_EMAILS=...` (기본 `AT_AUTH_REQUIRED=1`) | 로그인 + 허용 이메일만 본문 |

`AT_AUTH_REQUIRED=1` 과 `AT_AUTH_EXTERNAL=1` 을 함께 켜면 **REQUIRED 가 우선**한다(OIDC 설정이 필요). 인증 없이 외부 bind 를 시도하면 런처가 종료코드 2 로 멈추고 `restart: unless-stopped` 때문에 재시작을 반복한다 — `docker compose logs ui` 에 사유가 찍힌다.

데이터는 named volume `data` 에 둔다. 산출물을 넣으려면 `docker compose cp ./data/out ui:/app/data/` 처럼 복사하거나, 볼륨을 bind mount 로 바꾼다(SQLite WAL 은 bind mount 에서 검증하지 않았다). Docker 이미지 빌드·기동은 이 문서를 쓴 장비에서 데몬이 꺼져 있어 **아직 실측하지 않았다**(`docker compose config` 문법 검증만).

## 3. 외부에 열기 — 인증 + 인가 필수

1. **TLS 리버스 프록시**(Caddy·nginx 등) 뒤에 둔다. 프록시가 같은 호스트에 있으면 compose 의 publish 는 기본 `127.0.0.1` 그대로 두고 프록시가 `127.0.0.1:8501` 로 넘기면 된다(`AT_PUBLISH=0.0.0.0` 은 프록시가 다른 호스트에 있을 때만, 그때도 방화벽으로 프록시 IP 만 허용). 프록시는 **WebSocket upgrade 를 통과**시키고 **Host 헤더를 보존**해야 한다(Streamlit 이 same-origin 을 Host 로 판정). Host 를 바꿔야 하면 `.streamlit/config.toml` 의 `server.corsAllowedOrigins` 에 공개 origin 을 적는다.
2. **OIDC 로그인**: `.streamlit/secrets.example.toml` 을 `.streamlit/secrets.toml` 로 복사해 채운다. OIDC 제공자(Google 등)에서 클라이언트를 만들고 `redirect_uri = https://<도메인>/oauth2callback` 을 등록한다. `cookie_secret` 은 **직접 만든 값**(`python -c "import secrets; print(secrets.token_hex(32))"`)이어야 한다 — 비어 있거나 32자 미만이면 앱이 "설정 필요"로 막는다(예시 값을 그대로 쓰면 로그인 쿠키를 누구나 위조할 수 있다). 파일은 컨테이너 사용자(uid 10001)가 읽을 수 있어야 한다. 의존성 `streamlit[auth]`(Authlib + httpx)는 이미지에 들어 있다.
3. **허용 이메일**: `AT_AUTH_ALLOWED_EMAILS=me@example.com,ops@example.com`. 로그인만으로는 아무도 들어오지 못한다 — 목록이 비어 있으면 모두 "설정 필요" 화면이다.
4. `AT_AUTH_ALLOWED_EMAILS=... docker compose up -d` (기본 `AT_AUTH_REQUIRED=1`).

프록시가 이미 인증을 하고 있다면(oauth2-proxy·Cloudflare Access 등) `AT_AUTH_REQUIRED=0 AT_AUTH_EXTERNAL=1` 로 선언하고 OIDC 설정을 생략할 수 있다. 이때 접근 통제는 전적으로 프록시 책임이다. 실제 OIDC 제공자 로그인은 아직 실측하지 않았다.

## 4. 환경변수

모든 값의 목록·기본값·설명은 `src/shared/settings.py` 의 `VARS` 가 기준이다. 환경변수 → `.env` → 기본값 순서로 읽는다. 단, 표에 "환경변수만"으로 표시한 4개는 아직 `src/agent/settings.py` 가 환경변수만 읽는다(`.env` 값은 콘솔이 `load_env()` 를 부른 뒤에만 반영).

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `AT_BIND_ADDRESS` / `AT_PORT` | `127.0.0.1` / `8501` | 런처(`src.ui.serve`) bind 주소·포트. loopback 이 아니면 아래 인증 설정 중 하나가 필요하다. compose 에서 `AT_PORT` 는 **host 쪽 공개 포트**이고 컨테이너 안은 8501 고정 |
| `AT_AUTH_REQUIRED` | `0` | `1` 이면 OIDC 로그인 + 허용 이메일. 값은 1/0·true/false·yes/no·on/off 만(그 밖은 설정 오류로 멈춤) |
| `AT_AUTH_ALLOWED_EMAILS` | (빈값) | 허용 이메일, 쉼표 구분, 대소문자 무시. 비어 있으면 아무도 들어오지 못한다 |
| `AT_AUTH_EXTERNAL` | `0` | `1` 이면 앞단이 인증한다고 선언(외부 bind 허용). `AT_AUTH_REQUIRED=1` 이 함께 켜져 있으면 REQUIRED 가 우선 |
| `AT_OUT_DIR` / `AT_DATA_DIR` | `data/out/backtest` / `data/raw/normalized/bitmex` | 백테스트 산출물 · 정규화 데이터 |
| `AT_SYMBOL` | `XBTUSD` | 백테스트 심볼(BitMEX 인버스 테이프) |
| `AT_VAULT_DIR` | (빈값) | 설계 문서 볼트 폴더(선택). 비면 "사람 할 일·Phase 진행" 카드만 비어 있다 |
| `AT_TRADE_SYMBOL` | `BTCUSDT` | 실행 심볼(Bybit linear) — **환경변수만** |
| `AT_AGENT_DIR` / `AT_RISK_FILE` | `data/agent` / `config/risk.yaml` | 에이전트 상태·감사 로그 · 계정 리스크 한도 — **환경변수만**(이미지 기본값은 `/app/data/...`) |
| `AT_STORE_PATH` | `data/agent/platform.sqlite` | 플랫폼 저장소(SQLite, WAL) — 에이전트·run·자본곡선·이벤트·정지 명령. `data/agent/` 와 같이 git 제외 |
| `AT_LIVE_ENABLED` | `0` | LIVE 잠금 해제 — 정확히 `1` 일 때만, **환경변수만** |
| `BYBIT_*` | — | 거래소 키(`.env.example`). ui 컨테이너에는 주지 않는다 |

compose 전용: `AT_PUBLISH`(기본 `127.0.0.1`) = host 쪽 공개 주소.

## 5. 검증

```bash
uv run --locked pytest -q                       # 전체 테스트(실데이터·볼트 없는 환경에서는 일부 skip)
uv run python -m src.ui.serve --address 0.0.0.0 # 인증 설정 없이 외부 bind → 종료코드 2 (fail-closed 확인)
```

CI(`.github/workflows/ci.yml`)가 push 마다 같은 잠금으로 ubuntu(배포 대상)·macOS(판정 산출물 바이트 골든 포함) 두 환경의 pytest 와 비밀키 스캔(gitleaks)을 돌린다.
