"""백테스트 명세 — JSON 스키마(LLM structured output 용) · 검증 · dataclass · 한 줄 요약.

명세는 **평면 JSON** 이고 모든 키가 필수다(빈값은 "" · 0 · []). structured output 이 `oneOf`·nullable 을 지원하는지에
기대지 않기 위해서다. 규칙마다 쓰는 키가 다르고, 안 쓰는 키는 무시한다(`RULE_KEYS`).

    spec = validate(raw_dict, catalog)      # catalog: {symbol: (first_date, last_date)} — 위반이면 SpecError(.errors)
    describe(spec)                          # 한국어 한 줄
    spec.to_dict()                          # 화면 JSON 편집기 왕복용(모든 키)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import date

RULES = ("buy_and_hold", "dca", "ma_filter", "drawdown_trigger", "rebalance")
CHECKS = ("daily", "eom")
PERIODS = ("monthly", "quarterly", "yearly")
DEFAULT_CASH = 10_000.0
MAX_STAGES = 6
MAX_MA = 500
MAX_RESET_YEARS = 30

RULE_LABEL = {"buy_and_hold": "단순 보유", "dca": "월 적립", "ma_filter": "이동평균 필터",
              "drawdown_trigger": "하락 트리거 매수", "rebalance": "정기 리밸런싱"}
PERIOD_LABEL = {"monthly": "매월", "quarterly": "매 분기", "yearly": "매년"}
CHECK_LABEL = {"daily": "일별 종가", "eom": "월말 종가"}

# 규칙별로 뜻이 있는 키. 그 밖의 키는 검증하지 않고 비운다.
RULE_KEYS = {
    "buy_and_hold": {"weights"},
    "dca": {"weights"},
    "rebalance": {"weights", "rebalance_period"},
    "ma_filter": {"signal_symbol", "ma_window", "hold_symbol", "else_symbol"},
    "drawdown_trigger": {"weights", "ref_symbol", "stages", "vehicle", "check", "reset_years", "reserve_fraction"},
}

_SYMBOL = {"type": "string", "description": "종목 코드(대문자). 카탈로그에 있는 것만"}
SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "start", "end", "initial_cash", "monthly_contribution", "benchmark", "rule", "weights",
                 "signal_symbol", "ma_window", "hold_symbol", "else_symbol", "ref_symbol", "stages", "vehicle",
                 "check", "reset_years", "reserve_fraction", "rebalance_period"],
    "properties": {
        "title": {"type": "string", "description": "한국어 한 줄 제목"},
        "start": {"type": "string", "description": "시작일 YYYY-MM-DD. 언급 없으면 \"\"(데이터 시작)"},
        "end": {"type": "string", "description": "끝일 YYYY-MM-DD. 언급 없으면 \"\"(데이터 끝)"},
        "initial_cash": {"type": "number", "description": "시작 자금. 언급 없으면 10000"},
        "monthly_contribution": {"type": "number", "description": "매월 첫 거래일 적립액. 없으면 0"},
        "benchmark": {"type": "string", "description": "비교용 단순 보유 종목 1개. 없으면 \"\""},
        "rule": {"type": "string", "enum": list(RULES),
                 "description": "buy_and_hold 단순 보유 · dca 월 적립 · ma_filter 이동평균 위/아래 전환 · "
                                "drawdown_trigger 기준 종목이 고점 대비 x% 빠지면 수단 매수 · rebalance 정기 비중 복원"},
        "weights": {"type": "array", "description": "보유 비중(합 1). drawdown_trigger 는 예비 현금을 뺀 기본 보유 비중",
                    "items": {"type": "object", "additionalProperties": False, "required": ["symbol", "weight"],
                              "properties": {"symbol": _SYMBOL, "weight": {"type": "number"}}}},
        "signal_symbol": {**_SYMBOL, "description": "ma_filter: 이동평균을 계산할 종목. 다른 규칙은 \"\""},
        "ma_window": {"type": "integer", "description": "ma_filter: 이동평균 일수(예 200). 다른 규칙은 0"},
        "hold_symbol": {**_SYMBOL, "description": "ma_filter: 신호 종목이 이동평균 위일 때 보유. 다른 규칙은 \"\""},
        "else_symbol": {**_SYMBOL, "description": "ma_filter: 아래일 때 보유. 현금이면 \"\""},
        "ref_symbol": {**_SYMBOL, "description": "drawdown_trigger: 낙폭을 재는 기준 종목. 다른 규칙은 \"\""},
        "stages": {"type": "array", "description": "drawdown_trigger 단계. drawdown_pct 는 양수 퍼센트(30 = −30%), "
                                                   "cumulative_fraction 은 예비 현금 중 누적 투입 비율(0~1, 증가)",
                  "items": {"type": "object", "additionalProperties": False,
                            "required": ["drawdown_pct", "cumulative_fraction"],
                            "properties": {"drawdown_pct": {"type": "number"}, "cumulative_fraction": {"type": "number"}}}},
        "vehicle": {**_SYMBOL, "description": "drawdown_trigger: 트리거 때 사는 종목. 다른 규칙은 \"\""},
        "check": {"type": "string", "enum": list(CHECKS), "description": "drawdown_trigger 판정 주기. 다른 규칙은 daily"},
        "reset_years": {"type": "integer", "description": "drawdown_trigger 기준 고점: 0 = 시작일 이후 사상 최고, N = 직전 N년 최고"},
        "reserve_fraction": {"type": "number", "description": "drawdown_trigger: 예비 현금 비율(0~1, 예 0.3). 다른 규칙은 0"},
        "rebalance_period": {"type": "string", "enum": list(PERIODS), "description": "rebalance 주기. 다른 규칙은 monthly"},
    },
}


# 모든 키를 가진 중립 명세. 부분 프리셋을 이 위에 덮어 완전한 명세를 만든다(`SCHEMA` 와 키가 같아야 한다 — 테스트로 확인).
TEMPLATE: dict = {
    "title": "", "start": "", "end": "", "initial_cash": DEFAULT_CASH, "monthly_contribution": 0.0, "benchmark": "",
    "rule": "buy_and_hold", "weights": [], "signal_symbol": "", "ma_window": 0, "hold_symbol": "", "else_symbol": "",
    "ref_symbol": "", "stages": [], "vehicle": "", "check": "daily", "reset_years": 0, "reserve_fraction": 0.0,
    "rebalance_period": "monthly",
}


class SpecError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class Stage:
    drawdown_pct: float
    cumulative_fraction: float


@dataclass(frozen=True)
class Spec:
    title: str
    rule: str
    start: date | None
    end: date | None
    initial_cash: float
    monthly_contribution: float
    benchmark: str
    weights: dict[str, float] = field(default_factory=dict)
    signal_symbol: str = ""
    ma_window: int = 0
    hold_symbol: str = ""
    else_symbol: str = ""
    ref_symbol: str = ""
    stages: tuple[Stage, ...] = ()
    vehicle: str = ""
    check: str = "daily"
    reset_years: int = 0
    reserve_fraction: float = 0.0
    rebalance_period: str = "monthly"

    @property
    def symbols(self) -> list[str]:
        """엔진이 읽어야 할 종목(중복 없이, 등장 순)."""
        out: list[str] = []
        for s in [*self.weights, self.signal_symbol, self.hold_symbol, self.else_symbol, self.ref_symbol,
                  self.vehicle, self.benchmark]:
            if s and s not in out:
                out.append(s)
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"], d["end"] = (self.start.isoformat() if self.start else ""), (self.end.isoformat() if self.end else "")
        d["weights"] = [{"symbol": k, "weight": v} for k, v in self.weights.items()]
        d["stages"] = [asdict(s) for s in self.stages]
        return d


def _date(v, name: str, errors: list[str]) -> date | None:
    if v in ("", None):
        return None
    try:
        return date.fromisoformat(str(v).strip())
    except ValueError:
        errors.append(f"{name} 은 YYYY-MM-DD 여야 한다: {v!r}")
        return None


def _num(v, name: str, errors: list[str], default: float = 0.0) -> float:
    try:
        return float(v if v not in ("", None) else default)
    except (TypeError, ValueError):
        errors.append(f"{name} 은 숫자여야 한다: {v!r}")
        return default


def _sym(v, name: str, catalog: Mapping, errors: list[str], required: bool) -> str:
    s = str(v or "").strip().upper()
    if not s:
        if required:
            errors.append(f"{name} 종목이 필요하다")
        return ""
    if s not in catalog:
        errors.append(f"{name} 종목 {s!r} 은 데이터에 없다(가능: {', '.join(sorted(catalog))})")
    return s


def validate(raw: Mapping, catalog: Mapping[str, tuple]) -> Spec:
    """원시 dict → Spec. 모든 위반을 모아 `SpecError` 로 올린다(LLM 출력·손편집 둘 다 같은 경로)."""
    errors: list[str] = []
    unknown = set(raw) - set(SCHEMA["properties"])
    if unknown:
        errors.append(f"모르는 키: {sorted(unknown)}")
    rule = str(raw.get("rule", "")).strip()
    if rule not in RULES:
        errors.append(f"rule 은 {RULES} 중 하나: {rule!r}")
        raise SpecError(errors)
    keys = RULE_KEYS[rule]
    start, end = _date(raw.get("start"), "start", errors), _date(raw.get("end"), "end", errors)
    if start and end and start >= end:
        errors.append(f"start 가 end 보다 빠르지 않다: {start} ~ {end}")
    cash = _num(raw.get("initial_cash"), "initial_cash", errors, DEFAULT_CASH)
    if cash <= 0:
        errors.append("initial_cash 는 0 보다 커야 한다")
    monthly = _num(raw.get("monthly_contribution"), "monthly_contribution", errors)
    if monthly < 0:
        errors.append("monthly_contribution 은 0 이상이어야 한다")
    if rule == "dca" and monthly <= 0:
        errors.append("dca 는 monthly_contribution 이 0 보다 커야 한다")
    benchmark = _sym(raw.get("benchmark"), "benchmark", catalog, errors, required=False)

    weights: dict[str, float] = {}
    if "weights" in keys:
        items = raw.get("weights") or []
        if not isinstance(items, list) or not items:
            errors.append("weights 에 종목 비중이 하나 이상 필요하다")
        else:
            for it in items:
                if not isinstance(it, Mapping):
                    errors.append(f"weights 항목은 {{symbol, weight}} 객체여야 한다: {it!r}")
                    continue
                s = _sym(it.get("symbol"), "weights", catalog, errors, required=True)
                w = _num(it.get("weight"), "weight", errors)
                if w <= 0:
                    errors.append(f"weights[{s}] 는 0 보다 커야 한다")
                if s:
                    weights[s] = weights.get(s, 0.0) + w
            total = sum(weights.values())
            if weights and abs(total - 1.0) > 0.01:
                errors.append(f"weights 합이 1 이어야 한다(현재 {total:.3f})")
            elif weights:
                weights = {k: v / total for k, v in weights.items()}

    signal = hold = else_sym = ref = vehicle = ""
    ma_window = reset_years = 0
    reserve = 0.0
    stages: tuple[Stage, ...] = ()
    check, period = "daily", "monthly"
    if rule == "ma_filter":
        signal = _sym(raw.get("signal_symbol"), "signal_symbol", catalog, errors, required=True)
        hold = _sym(raw.get("hold_symbol"), "hold_symbol", catalog, errors, required=True)
        else_sym = _sym(raw.get("else_symbol"), "else_symbol", catalog, errors, required=False)
        ma_window = int(_num(raw.get("ma_window"), "ma_window", errors))
        if not 2 <= ma_window <= MAX_MA:
            errors.append(f"ma_window 는 2~{MAX_MA}: {ma_window}")
        if hold and hold == else_sym:
            errors.append("hold_symbol 과 else_symbol 이 같다")
    if rule == "drawdown_trigger":
        ref = _sym(raw.get("ref_symbol"), "ref_symbol", catalog, errors, required=True)
        vehicle = _sym(raw.get("vehicle"), "vehicle", catalog, errors, required=True)
        check = str(raw.get("check") or "daily")
        if check not in CHECKS:
            errors.append(f"check 는 {CHECKS} 중 하나: {check!r}")
        reset_years = int(_num(raw.get("reset_years"), "reset_years", errors))
        if not 0 <= reset_years <= MAX_RESET_YEARS:
            errors.append(f"reset_years 는 0~{MAX_RESET_YEARS}: {reset_years}")
        reserve = _num(raw.get("reserve_fraction"), "reserve_fraction", errors)
        if not 0 < reserve < 1:
            errors.append(f"reserve_fraction 은 0 과 1 사이: {reserve}")
        items = raw.get("stages") or []
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_STAGES:
            errors.append(f"stages 는 1~{MAX_STAGES}단계: {len(items) if isinstance(items, list) else items!r}")
        else:
            st_list = []
            for i, it in enumerate(items, 1):
                dd = _num(it.get("drawdown_pct") if isinstance(it, Mapping) else None, f"stages[{i}].drawdown_pct", errors)
                fr = _num(it.get("cumulative_fraction") if isinstance(it, Mapping) else None,
                          f"stages[{i}].cumulative_fraction", errors)
                if not 0 < dd < 100:
                    errors.append(f"stages[{i}].drawdown_pct 는 0~100 사이 양수 퍼센트: {dd}")
                if not 0 < fr <= 1:
                    errors.append(f"stages[{i}].cumulative_fraction 은 0~1: {fr}")
                st_list.append(Stage(dd, fr))
            for a, b in zip(st_list, st_list[1:]):
                if not (b.drawdown_pct > a.drawdown_pct and b.cumulative_fraction > a.cumulative_fraction):
                    errors.append("stages 는 drawdown_pct 와 cumulative_fraction 이 둘 다 증가해야 한다(누적 비율)")
                    break
            stages = tuple(st_list)
    if rule == "rebalance":
        period = str(raw.get("rebalance_period") or "monthly")
        if period not in PERIODS:
            errors.append(f"rebalance_period 는 {PERIODS} 중 하나: {period!r}")
    if errors:
        raise SpecError(errors)
    spec = Spec(title=str(raw.get("title") or "").strip(), rule=rule, start=start, end=end, initial_cash=cash,
                monthly_contribution=monthly, benchmark=benchmark, weights=weights, signal_symbol=signal,
                ma_window=ma_window, hold_symbol=hold, else_symbol=else_sym, ref_symbol=ref, stages=stages,
                vehicle=vehicle, check=check, reset_years=reset_years, reserve_fraction=reserve, rebalance_period=period)
    return spec if spec.title else Spec(**{**asdict(spec), "weights": weights, "stages": stages, "title": describe(spec)})


def _w(weights: Mapping[str, float]) -> str:
    return " · ".join(f"{k} {v:.0%}" for k, v in weights.items())


def describe(spec: Spec) -> str:
    """사람이 읽는 한 줄(코드가 만든다 — LLM 해설 아님)."""
    period = f"{spec.start or '데이터 시작'} ~ {spec.end or '데이터 끝'}"
    money = f"시작 {spec.initial_cash:,.0f}" + (f" + 월 {spec.monthly_contribution:,.0f} 적립" if spec.monthly_contribution else "")
    if spec.rule in ("buy_and_hold", "dca"):
        body = f"{RULE_LABEL[spec.rule]} {_w(spec.weights)}"
    elif spec.rule == "rebalance":
        body = f"{_w(spec.weights)} 를 {PERIOD_LABEL[spec.rebalance_period]} 리밸런싱"
    elif spec.rule == "ma_filter":
        body = (f"{spec.signal_symbol} 가 {spec.ma_window}일선 위면 {spec.hold_symbol}, 아래면 "
                f"{spec.else_symbol or '현금'} (종가 판정 → 다음 거래일 체결)")
    else:
        ladder = ", ".join(f"−{s.drawdown_pct:g}% → 누적 {s.cumulative_fraction:.0%}" for s in spec.stages)
        ref_high = "시작 후 사상 최고" if spec.reset_years == 0 else f"직전 {spec.reset_years}년 최고"
        body = (f"기본 {_w(spec.weights)} + 예비 현금 {spec.reserve_fraction:.0%}. {spec.ref_symbol} 이 {ref_high} 대비 "
                f"{ladder} 로 {spec.vehicle} 매수 ({CHECK_LABEL[spec.check]} 판정, 다음 거래일 체결, 수단은 팔지 않음)")
    bench = f" · 벤치마크 {spec.benchmark}" if spec.benchmark else ""
    return f"{body} · {period} · {money}{bench}"
