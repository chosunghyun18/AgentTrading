# 배포 가이드 — 남의 서버에 AgentTrading 관리 화면 올리기

이 문서는 **운영자**용이다. 지금 배포되는 것은 관리 화면(Streamlit)뿐이고, 에이전트 런타임(shadow·paper·live)은 다음 단계(W2)에서 같은 이미지에 붙는다. 설계 근거는 프로젝트 볼트의 `design/platform-architecture.md` 다.

## 무엇이 배포되고 무엇이 아닌가

| 배포됨 | 배포되지 않음(각자 준비) |
|---|---|
| 코드(`src/`), 의존성 잠금(`uv.lock`), 테마(`.streamlit/config.toml`) | 시장 데이터(`data/`) — BitMEX 공개 체결 테이프 등은 README 의 수집 절차로 직접 받는다 |
| | 거래소 API 키(`.env`) — ui 컨테이너에는 기본적으로 주지 않는다 |
| | OIDC 비밀값(`.streamlit/secrets.toml`) — 외부 공개 시에만 |

- 코드에 들어 있는 전략 설명·근거 수치의 원본 데이터는 **개인 비공개 연구 조건**으로 받은 것이다. 데이터·파생 산출물은 재배포하지 않는다.
- 거래소 이용 가능 여부·세무는 운영자 거주지 기준으로 각자 확인한다(예: Bybit 는 한국 거주자에게 FIU 미신고 해외 사업자다).
- 이 소프트웨어는 투자 조언이 아니며, 실거래 손실은 운영자 책임이다.

## 1. 로컬에서 그대로 쓰기 (가장 쉬움)

```bash
uv sync --group dev            # Python 3.12 를 받아 .venv 를 만든다 (.python-version)
uv run python -m src.ui.serve  # http://127.0.0.1:8501 — 인증 없음, 내 컴퓨터에서만
```

기존 방식(`pip install -r requirements.txt` + `scripts/ui.sh`)도 그대로 된다. 원격 서버에 두고 혼자 보려면 **SSH 터널**이 가장 간단하다: `ssh -L 8501:127.0.0.1:8501 user@server` 뒤 로컬 브라우저로 연다. 이 경우 서버 쪽은 여전히 127.0.0.1 bind 라 인증이 필요 없다.

## 2. Docker 로 띄우기

```bash
docker compose up -d --build
curl -fsS http://127.0.0.1:8501/_stcore/health   # ok
```

기본값은 host 의 **127.0.0.1 에만** 공개한다. 컨테이너 안에서는 0.0.0.0 bind 라 런처(`src.ui.serve`)가 인증 설정을 요구하고, 기본 `AT_AUTH_REQUIRED=1` 이라 OIDC 설정(3절)이 없으면 화면에 "인증 설정이 없다" 안내만 나온다. 혼자 SSH 터널로 볼 생각이면 `AT_AUTH_EXTERNAL=1` 로 선언한다(아래 표).

데이터는 named volume `data` 에 둔다. 산출물을 넣으려면 `docker compose cp ./data/out ui:/app/data/` 처럼 복사하거나, 볼륨을 bind mount 로 바꾼다(SQLite WAL 은 bind mount 에서 검증하지 않았다).

## 3. 외부에 열기 — 인증 + 인가 필수

1. **TLS 리버스 프록시**(Caddy·nginx 등) 뒤에 둔다. 컨테이너 포트를 인터넷에 직접 열지 않는다.
2. **OIDC 로그인**: `.streamlit/secrets.example.toml` 을 `.streamlit/secrets.toml` 로 복사해 채운다(Google 등 OIDC 제공자에서 클라이언트를 만들고 `redirect_uri = https://<도메인>/oauth2callback` 을 등록). Authlib 은 이미지에 들어 있다(`--extra auth`).
3. **허용 이메일**: `AT_AUTH_ALLOWED_EMAILS=me@example.com,ops@example.com`. 로그인만으로는 아무도 들어오지 못한다 — 목록이 비어 있으면 기동 자체를 거부한다.
4. compose 로 `AT_PUBLISH=0.0.0.0 AT_AUTH_REQUIRED=1 AT_AUTH_ALLOWED_EMAILS=... docker compose up -d`.

프록시가 이미 인증을 하고 있다면(oauth2-proxy·Cloudflare Access 등) `AT_AUTH_EXTERNAL=1` 로 선언하고 OIDC 설정을 생략할 수 있다. 이때 접근 통제는 전적으로 프록시 책임이다.

## 4. 환경변수

모든 값의 목록·기본값·설명은 `src/shared/settings.py` 의 `VARS` 가 기준이다(환경변수 → `.env` → 기본값 순서로 읽는다).

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `AT_BIND_ADDRESS` / `AT_PORT` | `127.0.0.1` / `8501` | 런처(`src.ui.serve`) bind. loopback 이 아니면 아래 인증 설정 중 하나가 필요하다 |
| `AT_AUTH_REQUIRED` | `0` | `1` 이면 OIDC 로그인 + 허용 이메일 |
| `AT_AUTH_ALLOWED_EMAILS` | (빈값) | 허용 이메일, 쉼표 구분 |
| `AT_AUTH_EXTERNAL` | `0` | `1` 이면 앞단이 인증한다고 선언(외부 bind 허용) |
| `AT_OUT_DIR` / `AT_DATA_DIR` | `data/out/backtest` / `data/raw/normalized/bitmex` | 백테스트 산출물 · 정규화 데이터 |
| `AT_AGENT_DIR` / `AT_RISK_FILE` | `data/agent` / `config/risk.yaml` | 에이전트 상태·감사 로그 · 계정 리스크 한도 |
| `AT_SYMBOL` / `AT_TRADE_SYMBOL` | `XBTUSD` / `BTCUSDT` | 백테스트 심볼(인버스 테이프) · 실행 심볼(Bybit linear) |
| `AT_VAULT_DIR` | (빈값) | 설계 문서 볼트 폴더(선택). 비면 "사람 할 일·Phase 진행" 카드만 비어 있다 |
| `BYBIT_*`, `AT_LIVE_ENABLED` | — | 거래소 키·LIVE 잠금(`.env.example`). ui 컨테이너에는 주지 않는다 |

compose 의 `AT_PUBLISH`(기본 `127.0.0.1`)는 host 쪽 공개 주소다.

## 5. 검증

```bash
uv run --frozen pytest -q                       # 전체 테스트(실데이터·볼트 없는 환경에서는 일부 skip)
uv run python -m src.ui.serve --address 0.0.0.0 # 인증 설정 없이 외부 bind → 종료코드 2 (fail-closed 확인)
```

CI(`.github/workflows/ci.yml`)가 push 마다 같은 잠금으로 pytest 와 비밀키 스캔(gitleaks)을 돌린다.
