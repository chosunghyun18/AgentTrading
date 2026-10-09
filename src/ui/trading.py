"""트레이딩 콘솔 페이지 공용: 서비스 생성, 모드 배너(LIVE 빨간 띠), 거래소 오류 표시."""

from __future__ import annotations

from contextlib import contextmanager

import streamlit as st

from src.agent import service as agent_service
from src.agent import settings
from src.agent.bybit import BybitError
from src.agent.service import OrderUnknown, RiskRejected
from src.ui import theme

MODE_COLORS = {"demo": theme.PRIMARY, "live": theme.DANGER}
MODE_LABELS = {"demo": "DEMO · 모의 자금", "live": "LIVE · 실제 자금"}

GUIDE = """
1. Bybit 가입·KYC → 우측 상단 **Demo Trading** 전환 → API 관리에서 **Demo API 키** 발급
   (권한: 주문·포지션만, **출금 끔**)
2. 프로젝트 루트 `.env` 에 추가 후 콘솔 재시작(`scripts/ui.sh`)
   ```
   BYBIT_DEMO_API_KEY=...
   BYBIT_DEMO_API_SECRET=...
   ```
3. **리스크** 화면에서 한도 입력 → 트레이딩 화면에서 주문·청산·킬스위치 연습
4. 실거래: 메인넷 **서브계정**에 투입할 금액만 이체 → 그 서브계정 API 키(출금 끔, IP 제한 권장) →
   `.env` 에 `BYBIT_LIVE_API_KEY`·`BYBIT_LIVE_API_SECRET`·`AT_LIVE_ENABLED=1` → 계정 화면에서 LIVE 전환
"""


def service() -> agent_service.TradingService:
    return agent_service.make_service()


def banner(svc) -> None:
    """페이지 상단 모드 배너. LIVE 면 화면 위 빨간 띠도 그린다."""
    color = MODE_COLORS[svc.mode]
    css = (f"<style>[data-testid='stHeader']{{border-top:6px solid {theme.DANGER};}}</style>"
           if svc.mode == "live" else "")
    halted = " " + theme.badge_html("정지(HALT)", theme.WARNING) if svc.halted() else ""
    st.markdown(f"{css}{theme.badge_html(MODE_LABELS[svc.mode], color)}{halted} "
                f"<span class='status-line'>{svc.symbol} · {svc.client.base_url}</span>", unsafe_allow_html=True)


def need_keys(svc) -> bool:
    """키가 없으면 안내를 띄우고 True."""
    if svc.client.has_keys:
        return False
    st.info(f"{svc.mode.upper()} API 키가 없다 — 시세만 표시한다. 계정 화면의 세팅 안내를 따른다.")
    return True


@contextmanager
def guarded(what: str):
    """거래소·리스크 예외를 화면 오류로 바꾼다(앱이 죽지 않게)."""
    try:
        yield
    except RiskRejected as e:
        st.error(f"{what} 거부 — 리스크 검사: " + " / ".join(e.violations))
    except OrderUnknown as e:
        st.warning(str(e))
    except BybitError as e:
        st.error(f"{what} 실패 — Bybit {e.code}: {e.msg}")
    except Exception as e:  # noqa: BLE001 — 네트워크 등
        st.error(f"{what} 실패 — {type(e).__name__}: {e}")


def live_allowed() -> bool:
    settings.load_env()
    return settings.live_unlocked()


def _flash(kind: str, msg: str) -> None:
    st.session_state.setdefault("_flash", []).append((kind, msg))


def show_flash() -> None:
    """직전 버튼 동작 결과(콜백에서 남긴 메시지)를 한 번 보여 준다."""
    for kind, msg in st.session_state.pop("_flash", []):
        getattr(st, kind)(msg)


def act(what: str, fn, ok_msg, reset: tuple[str, ...] = ()):
    """버튼 `on_click` 콜백: 동작 실행 → 결과 메시지 → 확인 체크박스 해제.

    콜백은 다음 실행의 스크립트보다 먼저 돌므로, 같은 실행에서 배너·상태가 바로 갱신되고
    확인 체크가 남아 다음 클릭이 확인 없이 실행되는 일이 없다.
    """
    def cb():
        try:
            res = fn()
            _flash("success", ok_msg(res) if callable(ok_msg) else ok_msg)
        except RiskRejected as e:
            _flash("error", f"{what} 거부 — 리스크 검사: " + " / ".join(e.violations))
        except OrderUnknown as e:
            _flash("warning", str(e))
        except BybitError as e:
            _flash("error", f"{what} 실패 — Bybit {e.code}: {e.msg}")
        except Exception as e:  # noqa: BLE001
            _flash("error", f"{what} 실패 — {type(e).__name__}: {e}")
        finally:
            for k in reset:
                st.session_state[k] = False
    return cb
