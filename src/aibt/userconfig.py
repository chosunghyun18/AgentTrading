"""사용자 세팅 JSON — 기본값·선호·전략 프리셋. 화면이 아니라 **파일**로 관리한다.

    {
      "defaults":    {"initial_cash": 50000000, "monthly_contribution": 3000000, "benchmark": "QQQ"},
      "preferences": "하락장에 담는 것을 좋아하고 투자 기간은 10년이다.",
      "presets":     {"내 기본 전략": { ...명세 일부 또는 전체... }}
    }

- AI 는 이 내용을 **프롬프트로** 받는다(`prompt_block`). 도구 호출을 쓰지 않는다 — 기본 공급자인 무료 모델
  상당수가 도구 호출을 제대로 못 하기 때문이다(결정 기록: 볼트 `task/ai-backtest.md`).
- 프리셋은 **AI 없이도** 바로 실행한다(`complete_spec` → `spec.validate`). 키가 없어도 쓸 수 있는 경로다.
- 화면은 이 파일을 **읽기만** 한다(쓰기 없음 원칙 유지). 편집은 사용자가 파일에서 직접 한다.
설계: Obsidian `Projects/work/AgentTrading/task/ai-backtest.md` "다음 방향".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.aibt.spec import TEMPLATE
from src.shared import settings

TOP_KEYS = {"defaults", "preferences", "presets"}  # `_` 로 시작하는 최상위 키는 메모로 보고 무시한다(JSON 에 주석이 없어서)
DEFAULT_KEYS = {"initial_cash", "monthly_contribution", "benchmark", "start", "end"}
MAX_PRESETS = 40
MAX_PREFERENCES = 2000
MAX_BLOCK = 8000
EXAMPLE_NAME = "aibt.example.json"


class UserConfigError(ValueError):
    """세팅 파일 형식 오류. `errors` 에 사람이 고칠 수 있는 문장 목록."""

    def __init__(self, errors: list[str], path: Path | None = None):
        self.errors = list(errors)
        self.path = path
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class UserConfig:
    path: Path
    exists: bool
    defaults: dict = field(default_factory=dict)
    preferences: str = ""
    presets: dict = field(default_factory=dict)

    @property
    def names(self) -> list[str]:
        return list(self.presets)


def path() -> Path:
    return Path(settings.get("AT_AIBT_CONFIG")).expanduser()


def example_path(p: Path | None = None) -> Path:
    return (p or path()).with_name(EXAMPLE_NAME)


def _validate(raw, p: Path) -> UserConfig:
    errors: list[str] = []
    if not isinstance(raw, dict):
        raise UserConfigError(["최상위는 JSON 객체여야 한다"], p)
    unknown = {k for k in raw if not str(k).startswith("_")} - TOP_KEYS
    if unknown:
        errors.append(f"모르는 키: {sorted(unknown)} (쓸 수 있는 키: {sorted(TOP_KEYS)}, `_` 로 시작하면 메모로 무시한다)")

    defaults = raw.get("defaults")
    if defaults is None:
        defaults = {}
    elif not isinstance(defaults, dict):
        errors.append("defaults 는 객체여야 한다")
        defaults = {}
    else:
        bad = set(defaults) - DEFAULT_KEYS
        if bad:
            errors.append(f"defaults 에 쓸 수 없는 키: {sorted(bad)} (쓸 수 있는 키: {sorted(DEFAULT_KEYS)})")
        defaults = {k: v for k, v in defaults.items() if k in DEFAULT_KEYS}

    preferences = raw.get("preferences")
    if preferences is None:
        preferences = ""
    elif not isinstance(preferences, str):
        errors.append("preferences 는 문자열이어야 한다")
        preferences = ""
    elif len(preferences) > MAX_PREFERENCES:
        errors.append(f"preferences 는 {MAX_PREFERENCES}자 이하여야 한다(현재 {len(preferences)})")

    presets = raw.get("presets")
    if presets is None:
        presets = {}
    elif not isinstance(presets, dict):
        errors.append("presets 는 {이름: 명세} 객체여야 한다")
        presets = {}
    else:
        if len(presets) > MAX_PRESETS:
            errors.append(f"presets 는 {MAX_PRESETS}개 이하여야 한다(현재 {len(presets)})")
        for name, body in list(presets.items()):
            if not str(name).strip():
                errors.append("프리셋 이름이 비어 있다")
            if not isinstance(body, dict):
                errors.append(f"프리셋 {name!r} 의 값은 객체여야 한다")
                presets.pop(name)
                continue
            bad = {k for k in body if not str(k).startswith("_")} - set(TEMPLATE)
            if bad:
                errors.append(f"프리셋 {name!r} 에 모르는 키: {sorted(bad)} (`_` 로 시작하면 메모로 무시한다)")
    if errors:
        raise UserConfigError(errors, p)
    return UserConfig(path=p, exists=True, defaults=defaults, preferences=preferences, presets=presets)


def load(p: Path | None = None) -> UserConfig:
    """세팅 파일을 읽는다. 파일이 없으면 빈 설정(`exists=False`) — 오류가 아니다."""
    p = p or path()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return UserConfig(path=p, exists=False)
    except OSError as e:
        raise UserConfigError([f"파일을 읽을 수 없다: {e.__class__.__name__}"], p) from None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise UserConfigError([f"JSON 문법 오류 {e.lineno}번째 줄: {e.msg}"], p) from None
    return _validate(raw, p)


def find_preset(cfg: UserConfig, text: str) -> str | None:
    """입력이 프리셋을 가리키면 그 이름. `/`·연속 공백·대소문자를 무시한 **정확 일치**만(부분 일치·문장은 AI 에게 넘긴다)."""
    norm = lambda s: " ".join(str(s).replace("/", " ").split()).casefold()  # noqa: E731
    key = norm(text)
    if not key:
        return None
    return next((n for n in cfg.presets if norm(n) == key), None)


def complete_spec(cfg: UserConfig, name: str) -> dict:
    """프리셋 → 모든 키를 갖춘 명세 dict. `TEMPLATE` ← `defaults` ← 프리셋 순으로 덮는다(검증은 호출자가)."""
    body = {k: v for k, v in cfg.presets[name].items() if not str(k).startswith("_")}  # `_` 키는 메모
    out = {**TEMPLATE, **{k: v for k, v in cfg.defaults.items() if k in TEMPLATE}, **body}
    if not str(out.get("title") or "").strip():
        out["title"] = name
    return out


def prompt_block(cfg: UserConfig) -> str:
    """AI 에게 넘길 사용자 세팅 설명. 비어 있으면 ""(프롬프트에 아무것도 붙이지 않는다)."""
    parts: list[str] = []
    if cfg.defaults:
        kv = ", ".join(f"{k}={v!r}" for k, v in sorted(cfg.defaults.items()))
        parts.append(f"- 기본값: {kv}\n  (사용자가 해당 값을 말하지 않으면 이 값을 쓴다. 말하면 말한 쪽이 우선이다.)")
    if cfg.preferences.strip():
        parts.append(f"- 사용자 선호: {cfg.preferences.strip()}")
    if cfg.presets:
        lines = [f'  * "{n}": {json.dumps(b, ensure_ascii=False, sort_keys=True)}' for n, b in cfg.presets.items()]
        parts.append("- 저장된 전략 프리셋(사용자가 이름을 말하면 그 내용을 바탕으로 요청한 부분만 바꾼다):\n"
                     + "\n".join(lines))
    if not parts:
        return ""
    block = "사용자 세팅:\n" + "\n".join(parts)
    if len(block) > MAX_BLOCK:
        block = block[:MAX_BLOCK] + "\n… (세팅이 길어 잘렸다. 프리셋을 줄이면 전부 전달된다.)"
    return block
