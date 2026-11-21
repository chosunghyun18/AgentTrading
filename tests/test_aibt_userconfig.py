"""사용자 세팅 JSON — 읽기·검증·프리셋 완성·프롬프트 블록. 파일만 다루고 네트워크 0."""

import json
from pathlib import Path

import pytest

from src.aibt import spec as S
from src.aibt import userconfig as uc

CAT = {s: ("2010-01-01", "2026-11-09") for s in ("QQQ", "QLD", "TQQQ", "XLK", "TECL")}
GOOD = {
    "defaults": {"initial_cash": 50000, "monthly_contribution": 3000, "benchmark": "QQQ"},
    "preferences": "10년 이상 보고 하락장에 담는다.",
    "presets": {
        "내 기본": {"rule": "buy_and_hold", "weights": [{"symbol": "QLD", "weight": 1.0}]},
        "200일선": {"rule": "ma_filter", "signal_symbol": "QQQ", "ma_window": 200, "hold_symbol": "TQQQ"},
    },
}


def write(tmp_path: Path, data) -> Path:
    p = tmp_path / "aibt.json"
    p.write_text(json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data, encoding="utf-8")
    return p


def test_template_matches_schema_keys():
    assert set(S.TEMPLATE) == set(S.SCHEMA["properties"])


def test_missing_file_is_empty_not_error(tmp_path):
    cfg = uc.load(tmp_path / "없다.json")
    assert cfg.exists is False and cfg.presets == {} and cfg.defaults == {} and cfg.preferences == ""
    assert uc.prompt_block(cfg) == "" and cfg.names == []


def test_loads_and_exposes_settings(tmp_path):
    cfg = uc.load(write(tmp_path, GOOD))
    assert cfg.exists and cfg.names == ["내 기본", "200일선"]
    assert cfg.defaults["initial_cash"] == 50000 and "하락장" in cfg.preferences


def test_path_follows_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("AT_AIBT_CONFIG", str(tmp_path / "x.json"))
    assert uc.path() == tmp_path / "x.json"
    assert uc.example_path(uc.path()).name == "aibt.example.json"


def test_shipped_example_is_valid_and_runnable():
    cfg = uc.load(Path(__file__).resolve().parents[1] / "config" / "aibt.example.json")
    assert cfg.exists and cfg.presets
    for name in cfg.names:  # 예시의 프리셋은 전부 바로 실행 가능해야 한다
        S.validate(uc.complete_spec(cfg, name), CAT)


def test_comment_keys_are_ignored(tmp_path):
    cfg = uc.load(write(tmp_path, {"_설명": "메모", "_todo": 1, "presets": {}}))
    assert cfg.exists and cfg.presets == {}


# ── 검증 ─────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("data,msg", [
    ("{not json", "JSON 문법 오류"),
    ([1, 2], "최상위는 JSON 객체"),
    ({"nope": 1}, "모르는 키"),
    ({"defaults": []}, "defaults 는 객체"),
    ({"defaults": {"rule": "dca"}}, "쓸 수 없는 키"),
    ({"preferences": 5}, "문자열"),
    ({"preferences": "x" * 3000}, "2000자"),
    ({"presets": []}, "presets 는"),
    ({"presets": {"a": 1}}, "객체여야"),
    ({"presets": {"a": {"bogus": 1}}}, "모르는 키"),
    ({"presets": {"": {}}}, "이름이 비어"),
])
def test_rejects_bad_files(tmp_path, data, msg):
    with pytest.raises(uc.UserConfigError) as ei:
        uc.load(write(tmp_path, data))
    assert any(msg in e for e in ei.value.errors), ei.value.errors


def test_too_many_presets(tmp_path):
    with pytest.raises(uc.UserConfigError, match="40개 이하"):
        uc.load(write(tmp_path, {"presets": {f"p{i}": {} for i in range(41)}}))


def test_error_carries_path(tmp_path):
    p = write(tmp_path, "{oops")
    with pytest.raises(uc.UserConfigError) as ei:
        uc.load(p)
    assert ei.value.path == p


# ── 프리셋 찾기·완성 ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,want", [("내 기본", "내 기본"), ("  내 기본  ", "내 기본"), ("/내 기본", "내 기본"),
                                       ("200일선", "200일선"), ("/ 200일선 ", "200일선")])
def test_find_preset_exact_forms(tmp_path, text, want):
    assert uc.find_preset(uc.load(write(tmp_path, GOOD)), text) == want


@pytest.mark.parametrize("text", ["", "   ", "내", "내 기본 전략으로 돌려줘", "없는 이름", "/"])
def test_find_preset_ignores_partial_and_sentences(tmp_path, text):
    assert uc.find_preset(uc.load(write(tmp_path, GOOD)), text) is None


def test_find_preset_is_case_insensitive(tmp_path):
    cfg = uc.load(write(tmp_path, {"presets": {"Buy AND Hold": {}}}))
    assert uc.find_preset(cfg, "buy and hold") == "Buy AND Hold"


def test_complete_spec_layers_template_defaults_preset(tmp_path):
    cfg = uc.load(write(tmp_path, GOOD))
    d = uc.complete_spec(cfg, "내 기본")
    assert set(d) == set(S.TEMPLATE)
    assert d["initial_cash"] == 50000 and d["monthly_contribution"] == 3000 and d["benchmark"] == "QQQ"  # defaults
    assert d["rule"] == "buy_and_hold" and d["weights"] == [{"symbol": "QLD", "weight": 1.0}]            # preset
    assert d["check"] == "daily" and d["stages"] == []                                                   # template
    assert d["title"] == "내 기본"
    sp = S.validate(d, CAT)
    assert sp.weights == {"QLD": 1.0} and sp.initial_cash == 50000


def test_preset_overrides_defaults(tmp_path):
    cfg = uc.load(write(tmp_path, {"defaults": {"initial_cash": 1}, "presets": {"p": {"initial_cash": 999,
                                                                                      "rule": "buy_and_hold"}}}))
    assert uc.complete_spec(cfg, "p")["initial_cash"] == 999


def test_preset_keeps_its_own_title(tmp_path):
    cfg = uc.load(write(tmp_path, {"presets": {"p": {"title": "직접 쓴 제목"}}}))
    assert uc.complete_spec(cfg, "p")["title"] == "직접 쓴 제목"


def test_incomplete_preset_fails_validation_with_reason(tmp_path):
    cfg = uc.load(write(tmp_path, {"presets": {"빈 것": {"rule": "ma_filter"}}}))
    with pytest.raises(S.SpecError) as ei:
        S.validate(uc.complete_spec(cfg, "빈 것"), CAT)
    assert any("signal_symbol" in e or "ma_window" in e for e in ei.value.errors)


# ── 프롬프트 블록 ────────────────────────────────────────────────────────────────────────────────

def test_prompt_block_carries_all_three_parts(tmp_path):
    b = uc.prompt_block(uc.load(write(tmp_path, GOOD)))
    assert "기본값" in b and "initial_cash=50000" in b
    assert "하락장에 담는다" in b
    assert '"내 기본"' in b and '"200일선"' in b and '"ma_window": 200' in b


def test_prompt_block_skips_empty_parts(tmp_path):
    b = uc.prompt_block(uc.load(write(tmp_path, {"preferences": "짧은 메모"})))
    assert "짧은 메모" in b and "기본값" not in b and "프리셋" not in b


def test_prompt_block_is_capped(tmp_path):
    big = {"presets": {f"p{i}": {"title": "가" * 300} for i in range(30)}}
    b = uc.prompt_block(uc.load(write(tmp_path, big)))
    assert len(b) <= uc.MAX_BLOCK + 100 and "잘렸다" in b


def test_comment_keys_inside_presets_are_ignored(tmp_path):
    cfg = uc.load(write(tmp_path, {"presets": {"p": {"_메모": "설명", "rule": "buy_and_hold",
                                                     "weights": [{"symbol": "QQQ", "weight": 1}]}}}))
    d = uc.complete_spec(cfg, "p")
    assert "_메모" not in d and set(d) == set(S.TEMPLATE)
    S.validate(d, CAT)


def test_users_own_config_file_is_valid_if_present():
    """실제 `config/aibt.json` 이 있으면 프리셋이 전부 실행 가능해야 한다(사용자 파일이라 없으면 건너뛴다)."""
    p = Path(__file__).resolve().parents[1] / "config" / "aibt.json"
    if not p.exists():
        pytest.skip("config/aibt.json 없음")
    cfg = uc.load(p)
    for name in cfg.names:
        S.validate(uc.complete_spec(cfg, name), CAT)
