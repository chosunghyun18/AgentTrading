"""UI 테스트용 소형 산출물 트리 — 실제 CLI(백테스트·워크포워드 엔진·export_equity)로 합성 1분봉에서 만든다(실데이터 금지)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from src.backtest import export_equity as ee
from src.backtest import run as br
from src.ingest.normalize import save_manifest
from tests.test_backtest_run import write_days
from tests.test_backtest_wfengine import FOLDS, LOOSE, PARAMS, SAMPLE, SYM

DAYS = [date(2020, 3, 1) + timedelta(days=i) for i in range(6)]  # [2020-03-01, 2020-03-07)
SPAN = "20200301_20200307"
REPORT = "default"
GRID = {"trigger": ["h1", "h2"], "n": [15], "k": [2], "stop_pct": [0.5], "tp_r": [2], "max_hold": [60],
        "risk_pct": [1, 2]}

TASKS = {
    "T-1 Phase 0 조사.md": {"id": "T-1", "title": "Phase 0 조사", "status": "done"},
    "T-2 Phase 3 판정.md": {"id": "T-2", "title": "Phase 3 판정", "status": "done"},
    "T-3 Phase 3 원인.md": {"id": "T-3", "title": "Phase 3 원인 분석", "status": "todo"},
    "T-4 사람 다운로드.md": {"id": "T-4", "title": "사람: 원본 수동 다운로드", "status": "manual"},
}


def build(root: Path) -> dict[str, Path]:
    """root 아래 `out`(data/out/backtest 대응)·`norm`(정규화)·`vault`(볼트 프로젝트) 를 만든다."""
    norm, out, vault = root / "norm", root / "out", root / "vault"
    write_days(norm, days=DAYS)
    save_manifest({d.strftime("%Y%m%d"): {"bars_rows": 1440} for d in DAYS}, norm / "_manifest" / f"{SYM}.json")

    grid = root / "grid.json"
    grid.write_text(json.dumps(GRID), encoding="utf-8")
    assert br.main(["--start", "2020-03-01", "--end", "2020-03-07", "--grid", str(grid), "--data-dir", str(norm),
                    "--out", str(out)]) == 0

    rep = br.run_walkforward(FOLDS, br.store_loader(SYM, norm), PARAMS, gate=LOOSE, sample=SAMPLE)
    path = br.walkforward_paths(out)["json"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(br.build_walkforward_report(rep, SYM, {"trigger": GRID["trigger"]}),
                               allow_nan=True), encoding="utf-8")
    assert ee.main(["--report", str(path), "--data-dir", str(norm)]) == 0

    tasks = vault / "task" / "autodev"
    tasks.mkdir(parents=True)
    for fn, fm in TASKS.items():
        (tasks / fn).write_text("---\ntype: autodev-task\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n"
                                                                     for k, v in fm.items()) + "---\n\n# x\n",
                                encoding="utf-8")
    (tasks / "not-a-task.md").write_text("그냥 메모\n", encoding="utf-8")
    return {"out": out, "norm": norm, "vault": vault}
