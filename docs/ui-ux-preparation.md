# AgentTrading UI UX 수정 준비

2026-10-17 기준으로 기존 Streamlit 화면을 다듬기 위한 작업 순서와 검수 기준을 정리한다. FreqUI의 상태·손익·비교 패턴과 Hummingbot의 Streamlit 구성을 활용한다. 첫 수정 대상은 대시보드의 정보 우선순위와 다음 화면으로 이어지는 동선이다.

설계 기준은 Obsidian `Projects/work/AgentTrading/design/ui-ux-v2-benchmark.md`다. 이 준비 작업은 화면 코드 변경이나 기존 v2 구현의 완료 판정을 포함하지 않는다.

## 현재 화면과 수정 위치

작업 시작 시 HEAD는 `448ce31fe6b5fdf84a5138bd60f45ff04c556ab0`이며, UI·테마·테스트에 커밋되지 않은 변경이 있다. 아래 상태는 HEAD만이 아니라 그 변경을 포함한 작업 디렉터리 기준이다.

| 역할 | 파일 | 현재 구현 |
| --- | --- | --- |
| 내비게이션 | `src/ui/app.py` | 홈·에이전트·백테스트·설정 및 안전의 4개 그룹, 10개 화면, 대시보드 기본 진입 |
| 상태와 공용 표시 | `src/ui/common.py`, `src/ui/theme.py` | DEMO/LIVE·HALT·키·전략·판정, 부호와 색을 함께 쓰는 손익 표시 |
| 첫 화면 | `src/ui/views/overview.py`, `src/ui/dashboard.py` | 상태 → 검증 맥락·게이트 KPI → 성과·낙폭·검증 상태 → 분석·최근 거래·작업 현황 |
| 후보 비교 | `src/ui/views/explore.py` | 시장·기간·비용 맥락, 4개 정렬 기준, 상위 20개 중 최대 4개 후보 비교 |
| 계정 모니터 | `src/ui/views/console_monitor.py` | 시세·포지션·미체결 조회, 기본 꺼짐인 10초 자동 새로고침 |
| 차트 | `src/ui/charts.py` | 자본곡선·낙폭 2단, 시작 자본 1.0 기준선, 기간 선택 |
| 테마 | `src/ui/theme.py`, `.streamlit/config.toml` | 다크 테마, 숫자 폭 정렬, 카드·배지·모바일 여백 |
| 데이터와 제어 연결 | `src/ui/data.py`, `src/ui/trading.py` | 로컬 산출물 로딩과 거래소 서비스 연결 |

이미 있는 기능은 위 구현을 출발점으로 다듬는다. 새 에이전트 목록·실행 이력·DB 화면은 플랫폼 W2/W3 범위로 유지한다.

## 오픈소스 소스 확인

다음 커밋의 실제 소스와 저장소 라이선스 표기를 확인했다. 링크는 해당 스냅샷에 고정한다. 원본 코드는 프로젝트에 복사하지 않았으며, 아래 적용 방향은 AgentTrading에 맞춘 설계 제안이다.

| 참조 | 확인한 소스 | 적용 방향 |
| --- | --- | --- |
| FreqUI `bd4e3d6`, [GPL-3.0 표기](https://github.com/freqtrade/frequi/blob/bd4e3d668430c27e66965a57bea8613decf8022c/LICENSE) | [BotStatus.vue](https://github.com/freqtrade/frequi/blob/bd4e3d668430c27e66965a57bea8613decf8022c/src/components/ftbot/BotStatus.vue), [ProfitPill.vue](https://github.com/freqtrade/frequi/blob/bd4e3d668430c27e66965a57bea8613decf8022c/src/components/general/ProfitPill.vue), [TradeList.vue](https://github.com/freqtrade/frequi/blob/bd4e3d668430c27e66965a57bea8613decf8022c/src/components/ftbot/TradeList.vue) | 계정 모드와 적용 전략을 먼저 읽게 하고, 손익·미확인 값·거래 상세 진입을 일관되게 표현한다. |
| FreqUI 동일 커밋 | [BacktestResultComparison.vue](https://github.com/freqtrade/frequi/blob/bd4e3d668430c27e66965a57bea8613decf8022c/src/components/ftbot/BacktestResultComparison.vue) | 지표를 기준으로 여러 결과를 나란히 비교하는 구조를 참고한다. 현재 후보 비교의 지표 배치와 선택 흐름에 활용한다. |
| Hummingbot Dashboard `ee42595`, [Apache-2.0](https://github.com/hummingbot/dashboard/blob/ee42595bec85518eda0b9fe1a56046d09a3e36c6/LICENSE) | [permissions.py](https://github.com/hummingbot/dashboard/blob/ee42595bec85518eda0b9fe1a56046d09a3e36c6/frontend/pages/permissions.py), [backtesting_metrics.py](https://github.com/hummingbot/dashboard/blob/ee42595bec85518eda0b9fe1a56046d09a3e36c6/frontend/visualization/backtesting_metrics.py), [instances/app.py](https://github.com/hummingbot/dashboard/blob/ee42595bec85518eda0b9fe1a56046d09a3e36c6/frontend/pages/orchestration/instances/app.py) | Streamlit 페이지 그룹, 지표 열, 상태 카드, `st.fragment(run_every=...)` 부분 갱신을 현재 구조 안에서 활용한다. |
| Jesse `34dc9df`, [MIT](https://github.com/jesse-ai/jesse/blob/34dc9dfc369958fc613782662f4e20436f5a1388/LICENSE) | [backtest_controller.py](https://github.com/jesse-ai/jesse/blob/34dc9dfc369958fc613782662f4e20436f5a1388/jesse/controllers/backtest_controller.py), [BacktestSession.py](https://github.com/jesse-ai/jesse/blob/34dc9dfc369958fc613782662f4e20436f5a1388/jesse/models/BacktestSession.py) | 상태・날짜・제목으로 필터링하는 세션 모델을 향후 실행 이력 설계에 참고한다. 이번 화면 수정에는 새 저장소를 도입하지 않는다. |

Hummingbot은 [공식 문서에 적극적인 유지보수 중단이 명시](https://hummingbot.org/dashboard/)되어 있어 구현 패턴의 참고 대상으로 사용한다. Jesse의 해당 공개 트리에는 Nuxt 정적 번들이 있지만 `.vue`·`.tsx` UI 원본이 없어, 공개 컨트롤러·모델에서 확인한 동작과 화면 구현을 구분한다. 기존 검토의 Jesse 비교 패턴은 [FreqUI 비교 소스](https://github.com/freqtrade/frequi/blob/bd4e3d668430c27e66965a57bea8613decf8022c/src/components/ftbot/BacktestResultComparison.vue)로도 근거를 보강할 수 있다.

## 수정 순서

| 순서 | 수정 제안 | 대상 | 검수 기준 |
| --- | --- | --- | --- |
| 1 | 첫 화면에서 현재 상태·검증 결과·다음 행동이 순서대로 읽히게 한다. 연구 지표와 프로젝트 진행 정보의 시각적 비중을 조정한다. | `overview.py`, `dashboard.py`, `common.py` | 첫 화면에서 모드, 가동 여부, 전략, 판정과 다음에 볼 화면을 찾을 수 있다. 리포트가 없어도 상태와 시작 안내가 남는다. |
| 2 | 판정 미달 → 진단, 후보 비교 → 거래 상세로 이동하는 연결을 정리한다. 필요한 선택값의 전달 범위를 먼저 확인한다. | `app.py`, `overview.py`, `explore.py`, `trades.py` | 이동 후 시장·기간·비용·후보 맥락을 확인할 수 있다. 필터 변경 후 비교 선택과 순위가 일치한다. |
| 3 | 테이블의 핵심 열·한글 라벨·수치 단위·손익 표현과 빈 결과 안내를 통일한다. | `common.py`, `explore.py`, `console_history.py`, `trades.py` | 정렬이 표시 문자열 대신 원본 수치로 동작한다. 비어 있음·0·조회 실패가 구분된다. |
| 4 | 모니터의 성공·실패와 갱신 시각을 읽기 쉽게 하고 작은 화면의 배치를 확인한다. | `console_monitor.py`, `theme.py`, `charts.py` | 부분 조회 실패를 전체 데이터의 성공 갱신으로 오해하지 않는다. 1440·1024·390px에서 주요 상태·필터·차트 조작을 확인한다. |

우선 1번을 작은 변경으로 구현하고 검수한 뒤 다음 순서로 진행한다. 화면 크기별 배치·키보드 조작·차트 가독성은 브라우저 검수 항목이며 현재 테스트 통과만으로 완료 처리하지 않는다.

## 구현 제약

- Streamlit·Plotly의 현재 구성과 공용 컴포넌트를 활용한다. 이번 준비 단계에서는 새 의존성이 필요하지 않다.
- 백테스트 검증 수익과 실제 계정 손익은 데이터 출처·종목·기간·비용 맥락을 함께 표시한다. 일치하는 데이터가 준비되기 전에는 BTC 보유 수익 기준선을 추가하지 않는다.
- 수동 주문 금지, 게이트·OOS 접근·판정 계산, 모드 전환·킬스위치의 기존 확인 절차를 유지한다. 화면 제어 범위는 기존 설계의 미결 결정 D를 따른다.
- 작업 시작 전 파일별 diff를 확인하고, 기존 미커밋 UI 변경을 보존한다. 원본 코드·에셋을 실제로 도입하는 작업은 출처와 해당 라이선스를 별도로 기록한다.

## 확인한 테스트 기준

2026-10-17 작업 디렉터리에서 기존 UI 테스트 10개 파일을 실행했다. 테스트는 합성 산출물과 FakeClient를 사용하며, 실제 계정으로 화면 동작을 확인한 결과가 아니다.

| 환경 | 결과 |
| --- | --- |
| Python 3.12.13 `.venv312` | 119 passed, Authlib/httpx 관련 deprecation warning 1건 |
| Python 3.10.6 `.venv` | 118 passed, 1 skipped — `authlib` 미설치로 선택적 OIDC import 검사 생략 |

실행 명령:

```bash
.venv312/bin/python -m pytest tests/test_ui_pages.py tests/test_ui_charts.py tests/test_ui_diagnose_real.py tests/test_ui_data.py tests/test_ui_strategy_page.py tests/test_ui_v2.py tests/test_ui_theme.py tests/test_ui_serve.py tests/test_ui_auth.py tests/test_ui_trading_pages.py -q
```

Python 3.10 검증은 같은 명령에서 실행 파일을 `.venv/bin/python`으로 바꾼다. 현재 잠금 환경의 UI 버전은 Streamlit 1.65.0·Plotly 7.1.0이다.

검수에는 리포트 없음, 곡선 없음·오래된 곡선, 후보 0개, 키 없음, 거래소 오류, DEMO/LIVE·HALT, 모드 전환 확인과 출금 권한 차단을 포함한다. 자동 새로고침의 타이머·반응형 배치·색 대비·포커스 이동은 브라우저에서 추가 확인한다. 수정 구현 후에는 영향받는 기존 테스트를 먼저 실행하고, v2 완료 판정은 상위 설계의 전체 pytest 기준을 따른다.

로컬 화면 실행은 `scripts/ui.sh`, 기본 주소는 `http://127.0.0.1:8501`이다. 이번 준비에서는 서버를 새로 띄우거나 브라우저 시각 검수를 진행하지 않았다.
