"""전략 레지스트리 — 관리자 "전략" 페이지가 보여 줄 전략 목록·규칙 요약·관문 진행 상태.

기준은 볼트 설계 문서다. `doc_status` 가 볼트 frontmatter `status` 와 다르면 `tests/test_agent_strategies.py` 가 실패한다.
결과 수치는 여기 적지 않고 산출물(`walkforward/<report>.json`)에서 읽는다.
설계: Obsidian `Projects/work/AgentTrading/design/agent-strategy-page.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

STEPS = ("설계", "근거 데이터 검증", "구현", "워크포워드", "OOS 1회", "페이퍼 8주", "실거래")
STEP_STATES = ("done", "fail", "pending", "running", "na")  # 완료·미달·대기·진행 중·해당 없음
STATUSES = {  # status → (화면 라벨, 색 이름)
    "active": ("운용 중", "success"),
    "next": ("다음 시도 — 구현 전", "primary"),
    "rejected": ("후보 아님 — 게이트 미달", "danger"),
    "on_hold": ("보류", "warning"),
    "discarded": ("폐기", "info"),
}


@dataclass(frozen=True)
class Strategy:
    id: str
    name: str
    status: str
    summary: str
    evidence: str
    entry: tuple[str, ...]
    exit: tuple[str, ...]
    sizing: tuple[str, ...]
    params: tuple[str, ...]
    steps: dict[str, str]
    notes: dict[str, str] = field(default_factory=dict)  # 단계별 한 줄 사유
    doc: str = ""               # 볼트 프로젝트 폴더 기준 경로
    doc_status: str | None = None  # 볼트 frontmatter status(없으면 None)
    report: str | None = None   # 워크포워드 리포트 이름(결과가 있으면)
    n_trials: str = ""


def _steps(**kw) -> dict[str, str]:
    keys = dict(zip(("design", "evidence", "impl", "wf", "oos", "paper", "live"), STEPS))
    return {keys[k]: v for k, v in kw.items()}


REGISTRY: tuple[Strategy, ...] = (
    Strategy(
        id="C1", name="C1 역추세 · 메이커 진입 · 저빈도", status="next",
        summary="가격이 단기 평균에서 크게 벗어나면 반대로 들어가되, 지정가(메이커)로만 체결하고 거래 수를 v1 의 1/10 이하로 줄인다.",
        evidence="aoa 원본 체결(2018-03~2021-12) 진위 확인 후 재현한 지표 — 역추세 진입 76%(5분)·71%(1시간)·66%(4시간), "
                 "메이커 체결 67%. v1 실패 원인 1순위(비용·빈도) 분석.",
        entry=("직전 N분(60·240) 평균에서 표준편차의 k배(2.0·2.5) 이상 벗어나면 반대 방향 신호",
               "신호 봉 마감가에 지정가 주문 — 다음 봉에서 가격이 그 값을 뚫고 지나가야 체결(보수적 메이커 가정), 아니면 취소"),
        exit=("손절·익절은 직전 N분 변동성(σ) 배수로 처음부터 정한다",
              "청산 방식 3가지 중 하나: 평균 복귀 / σ 배수 익절 / 시간 청산"),
        sizing=("원칙 유지: 손절 필수, 레버리지 ≤ 10배, 총 노출 상한", "레짐 필터(C3) 포함 여부는 구현 계획에서 실행 전에 고정"),
        params=("창 길이 2 × 임계 2 × 청산 방식 3 × 손절 σ 배수 2 = 24 시퀀스",
                "판정은 보수적 메이커 체결, 전부 테이커는 민감도(+24)"),
        steps=_steps(design="done", evidence="done", impl="pending", wf="pending", oos="pending", paper="pending",
                     live="pending"),
        notes={"설계": "2026-10-09 설계 완료", "근거 데이터 검증": "aoa 원본 진위 확인·행동 지표 재현",
               "구현": "구현 태스크 대기 · DSR V 범위 사람 승인 대기", "OOS 1회": "프로젝트 전체 1회 — 사람 판단"},
        doc="design/phase3-next-hypotheses.md", doc_status="done", n_trials="누적 804 (C3 포함 시 852)",
    ),
    Strategy(
        id="v1", name="v1 합성 전략 (돌파·모멘텀·평균회귀)", status="rejected",
        summary="공개 발언에서 가져온 원칙(1분봉·저배율·손절 필수) 위에 진입 트리거 가설 3종 중 하나를 얹은 전략. 워크포워드 게이트 미달.",
        evidence="원칙: 공개 발언 2차 인용. 진입 트리거·손절 폭: 근거 없는 가설. 시장 데이터: BitMEX 공개 체결 테이프(2018-03~2021-12).",
        entry=("H1 돌파: 종가가 직전 N분 최고가 위(롱) / 최저가 아래(숏)",
               "H2 모멘텀: 직전 N분 수익률이 같은 기간 변동성의 k배 초과 → 그 방향",
               "H3 평균회귀: 종가가 N분 평균에서 표준편차의 k배 이상 벗어남 → 반대 방향",
               "1분봉 마감에 판단, 다음 봉 시가에 시장가 진입 · 동시 포지션 1개 · 추가 진입 없음"),
        exit=("손절: 진입가 대비 고정 % (이동 금지)", "익절: 손절 폭의 R배 (또는 없음)",
              "시간 청산: 최대 보유 시간 경과", "같은 봉에서 손절·익절이 다 닿으면 손절 우선"),
        sizing=("손절되면 자본의 risk_pct% 를 잃도록 계약 수 결정 (계약 수 상한 10,000,000)",
                "총 노출 ≤ 4배, 레버리지 ≤ 10배(격리), 1회 예정 손실 ≤ 30%"),
        params=("트리거 3 · N 15/60/240 · k 1.5/2/2.5 · 손절 0.5/1/2% · 익절 1/2/3R/없음 · 보유 60/240/1440분 · 위험 1/2/5%",
                "총 2,268 run (독립 시퀀스 756)"),
        steps=_steps(design="done", evidence="fail", impl="done", wf="fail", oos="na", paper="na", live="na"),
        notes={"근거 데이터 검증": "트리거는 공개 발언에 없는 가설", "워크포워드": "Sharpe·DSR 미달",
               "OOS 1회": "워크포워드 미달이라 실행하지 않음"},
        doc="design/phase2-synthetic-strategy.md", doc_status=None, report="default+funding", n_trials="756",
    ),
    Strategy(
        id="B", name="전략 B — aoa 역추세 분할 스캘핑", status="on_hold",
        summary="고변동 구간에서 역추세로 3분할 지정가 진입, 소폭 익절. C1 이 역추세·메이커 부분을 이어받아 대체.",
        evidence="aoa 원본 라운드트립 2,205건 일회성 측정(정의 A). 중심 가정(고빈도 소폭 익절)이 원본 재현의 손익 집중"
                 "(정의 B: 포지션 2,589개 중 상위 100건 = 157%)과 맞지 않음 — 두 수치는 포지션 정의가 달라 맞추지 않았다.",
        entry=("60분 변동성 ≥ 30일 중앙값 × 1.5 일 때만", "z ≤ −k 롱 / z ≥ +k 숏 (n 15·60, k 1.0·1.5·2.0)",
               "신호가·−0.5%·−1.0% 에 1/3씩 지정가 분할 진입, 첫 진입 60분 뒤 남은 주문 취소"),
        exit=("평단 대비 +0.25~0.5% 익절 또는 평균 복귀", "첫 진입가 대비 −1.5~3% 손절", "60·240분 시간 청산"),
        sizing=("3분할 모두 체결 후 손절 시 손실 = 자본의 1%",),
        params=("n 2 × k 3 × 익절 3 × 손절 3 × 보유 2 = 108",),
        steps=_steps(design="done", evidence="fail", impl="na", wf="na", oos="na", paper="na", live="na"),
        notes={"근거 데이터 검증": "중심 가정이 원본 재현과 불일치 → 보류"},
        doc="design/strategy-b-aoa-meanrev.md", doc_status="on-hold",
    ),
    Strategy(
        id="v2", name="v2 aoa 관찰 스타일", status="discarded",
        summary="평균회귀만·지정가 진입·노출 2배. 근거가 원본 검증 전 제3자(aoa.raoni.xyz) 수치라 폐기.",
        evidence="제3자 가공 수치 — 원본 재현 전. 사용자 원칙: 검증 전 데이터로 만든 전략은 폐기.",
        entry=("H3 평균회귀 신호만", "신호 봉 마감가 지정가, 다음 봉에서만 유효"),
        exit=("v1 과 같음(진입 봉은 손절만 판정)",),
        sizing=("총 노출 상한 2배",),
        params=("972 run (독립 시퀀스 324) — 실행하지 않음",),
        steps=_steps(design="done", evidence="fail", impl="na", wf="na", oos="na", paper="na", live="na"),
        notes={"근거 데이터 검증": "제3자 수치(원본 재현 전)", "구현": "코드 되돌림"},
        doc="design/phase3-v2-aoa-style.md", doc_status="discarded",
    ),
)


def registry() -> tuple[Strategy, ...]:
    return REGISTRY


def active(strategies=REGISTRY) -> Strategy | None:
    """실거래 단계가 완료(또는 페이퍼 진행 중)인 전략. 없으면 None — 에이전트가 쓸 전략이 없다."""
    for s in strategies:
        if s.steps.get("실거래") == "done" or s.steps.get("페이퍼 8주") == "running":
            return s
    return None


def blocking_step(s: Strategy) -> tuple[str, str] | None:
    """처음으로 완료되지 않은 단계(이름, 상태)."""
    for step in STEPS:
        st_ = s.steps.get(step, "na")
        if st_ != "done":
            return step, st_
    return None


# v1 파라미터 문장화 ---------------------------------------------------------------------------------------

def parse_param_id(param_id: str) -> dict[str, str]:
    return dict(kv.split("=", 1) for kv in param_id.split(";") if "=" in kv)


def describe_v1(param_id: str) -> list[str]:
    """v1 param_id → 사람이 읽는 규칙 문장."""
    p = parse_param_id(param_id)
    n, k, stop = p.get("n", "?"), p.get("k"), float(p.get("stop_pct", "nan"))
    trig = {
        "h1": f"종가가 직전 {n}분 최고가를 넘으면 롱, 최저가 아래로 내려가면 숏 (돌파)",
        "h2": f"직전 {n}분 수익률이 같은 기간 변동성의 {k}배를 넘으면 그 방향으로 진입 (모멘텀)",
        "h3": f"종가가 {n}분 평균에서 표준편차의 {k}배 이상 벗어나면 반대 방향으로 진입 (평균회귀)",
    }.get(p.get("trigger", ""), f"트리거 {p.get('trigger')}")
    tp = p.get("tp_r", "none")
    hold = int(p.get("max_hold", "0"))
    out = [f"진입: {trig}", f"손절: 진입가 대비 {stop:g}% 반대로 가면 청산"]
    out.append("익절: 없음" if tp == "none" else f"익절: 진입가 대비 {stop * float(tp):g}% ({float(tp):g}R)")
    out.append(f"최대 보유: {hold}분" + (f" ({hold / 60:g}시간)" if hold >= 60 else ""))
    out.append(f"크기: 손절되면 자본의 {float(p.get('risk_pct', 'nan')):g}% 를 잃도록 계약 수 결정 (총 노출 4배 상한)")
    return out
