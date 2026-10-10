"""DSR 군집 방식 — 실효 시행 수 N(군집 수)·군집 V(대표 run `sr_daily` 분산).

설계 근거(단일 기준): Obsidian `Projects/work/AgentTrading/design/phase3-next-hypotheses.md`
"시행 수 누적 규칙" 5항 (다) 군집 방식, 태스크 T-20261009-18. 문서와 이 파일이 다르면 문서를 따른다.
알고리즘은 López de Prado (2019/2020) ONC(Optimal Number of Clusters): 상관 거리 행렬 → 거리 행렬의 행을
특징으로 k-means(k = 2..max_k) → 실루엣 품질 q = mean(s)/std(s) 최대 k 선택 → 품질이 평균 미만인 군집을
다시 군집하는 재귀 정제. 순수 함수(numpy/pandas 만, scipy/sklearn 없음)이고 `run.py`·`walkforward.py` 는
건드리지 않는다(연결은 호출자가 새 인자로).

    res = cluster_trials(returns, sr_daily)          # returns: 일자 × run 키, sr_daily: run 키 → 일 Sharpe
    sr0 = walkforward.expected_max_sr(res.effective_trials, res.cluster_var)

규칙(결정 기록은 볼트 태스크 문서):
- 열(run) 순서와 무관: 키를 정렬해 내부 처리하고, 군집 번호는 "군집 최소 키의 정렬 순"으로 매긴다.
- 일 수익률 결측은 0(거래 없는 날)으로 채운다.
- 완전히 같은 수익률 열(복제)은 군집 전에 하나로 합친다 → 복제를 더해도 실효 N 불변(빈틈 차단).
- 분산 0(거래 없음 등)인 서로 다른 열은 상관이 정의되지 않으므로 각각 독립 군집(단독)으로 센다. N 을
  줄이지 않는 쪽이라 SR0 를 낮추지 않는다. 소진 run 은 소진 전 수익률이 있어 분산 > 0 → 보통 군집한다.
- k = 1: 비상수 열이 1개이거나 모든 쌍 상관 ≥ 0.99 이면 1군집. 그 외에는 ONC 가 k ≥ 2 에서 고른다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.backtest.walkforward import sr_variance

# 모든 쌍 상관이 이 값 이상이면 한 군집(k=1)으로 본다. d = sqrt(0.5·(1−ρ)) ≤ sqrt(0.005) ≈ 0.0707.
RHO_ONE_CLUSTER = 0.99
# 기본 군집 수 상한. 800 run 규모에서 런타임을 분 단위 아래로 두려는 값이다(보고서의 측정 참고).
# 상한에 닿으면 실효 N 이 과소(느슨한 쪽)일 수 있어 `hit_max_k` 로 알린다.
DEFAULT_MAX_K = 100
_MAX_ITER = 100


@dataclass(frozen=True)
class ClusterResult:
    effective_trials: int      # 실효 N = 군집 수
    cluster_var: float         # V = 군집 대표 run sr_daily 표본 분산(ddof=1); 군집 < 2 → NaN
    labels: pd.Series          # run 키 → 군집 번호 (0..k-1, 최소 키의 정렬 순)
    representatives: list      # 군집별 대표 run 키 (군집 번호 순)
    silhouette_score: float    # 선택된 분할의 품질 mean(s)/std(s) (ONC). 군집 < 2 → NaN
    n_input: int               # 입력 run 수
    n_clustered: int           # 군집에 들어간 run 수(= 입력 전부; 제외 없음)
    hit_max_k: bool = False    # 최적 k 가 상한에 닿았는가(닿으면 실효 N 이 과소일 수 있음)


def correlation_distance(returns: pd.DataFrame) -> pd.DataFrame:
    """상관 거리 d = sqrt(0.5·(1−ρ)). 열 = run 키. 분산 0 열은 상관 NaN → 대각 0, 나머지 NaN."""
    rho = returns.corr()
    d = np.sqrt(np.clip(0.5 * (1.0 - rho.to_numpy()), 0.0, None))
    out = pd.DataFrame(d, index=rho.index, columns=rho.columns)
    np.fill_diagonal(out.values, 0.0)
    return out


def _sorted_keys(keys) -> list:
    keys = list(keys)
    try:
        return sorted(keys)
    except TypeError:                       # 섞인 타입 키는 문자열 표현으로 정렬(결정적)
        return sorted(keys, key=lambda k: (type(k).__name__, str(k)))


# k-means ----------------------------------------------------------------------------------------------

def _sqdist(x: np.ndarray, c: np.ndarray) -> np.ndarray:
    d = (x * x).sum(1)[:, None] - 2.0 * x @ c.T + (c * c).sum(1)[None, :]
    return np.maximum(d, 0.0)


def _kmeans_once(x: np.ndarray, k: int, rng: np.random.Generator) -> tuple[np.ndarray, float]:
    n = len(x)
    # k-means++ 초기화
    idx = [int(rng.integers(n))]
    d2 = _sqdist(x, x[idx[0]][None, :])[:, 0]
    for _ in range(1, k):
        tot = d2.sum()
        i = int(rng.choice(n, p=d2 / tot)) if tot > 0 else int(rng.integers(n))
        idx.append(i)
        d2 = np.minimum(d2, _sqdist(x, x[i][None, :])[:, 0])
    cent = x[idx].copy()
    labels = np.full(n, -1)
    for _ in range(_MAX_ITER):
        dist = _sqdist(x, cent)
        new = dist.argmin(1)
        # 빈 군집: 소속 군집 중심에서 가장 먼 점을 가져온다
        for j in range(k):
            if not (new == j).any():
                far = int(dist[np.arange(n), new].argmax())
                new[far] = j
                dist[far, new[far]] = 0.0
        if (new == labels).all():
            break
        labels = new
        for j in range(k):
            m = labels == j
            if m.any():                                  # 빈 군집 보정으로 비면 이전 중심 유지
                cent[j] = x[m].mean(0)
    inertia = float(_sqdist(x, cent)[np.arange(n), labels].sum())
    return labels, inertia


def _kmeans(x: np.ndarray, k: int, n_init: int, seed: int) -> np.ndarray:
    best, best_in = None, math.inf
    for i in range(n_init):
        lab, inert = _kmeans_once(x, k, np.random.default_rng([seed, k, i]))
        if inert < best_in - 1e-12:         # 동률은 먼저 나온 init 유지(결정적)
            best, best_in = lab, inert
    return best


# 실루엣 -----------------------------------------------------------------------------------------------

def _silhouette(e: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """표본별 실루엣. e = 점 사이 유클리드 거리 행렬. 단독 군집 표본은 0(sklearn 관례)."""
    n = len(labels)
    k = int(labels.max()) + 1
    h = np.zeros((n, k))
    h[np.arange(n), labels] = 1.0
    cnt = h.sum(0)
    sums = e @ h                                         # 점 → 군집별 거리 합
    own = cnt[labels]
    a = sums[np.arange(n), labels] / np.maximum(own - 1.0, 1.0)
    mean_other = sums / np.maximum(cnt, 1.0)[None, :]
    mean_other[np.arange(n), labels] = np.inf
    b = mean_other.min(1)
    m = np.maximum(a, b)
    s = np.where(m > 0, (b - a) / np.where(m > 0, m, 1.0), 0.0)
    return np.where(own > 1, s, 0.0)


def _quality(s: np.ndarray) -> float:
    """ONC 품질 mean/std. 표본 < 2 → 0. std ≈ 0: 평균 > 0 이면 +inf(완벽 분리), 아니면 −inf."""
    if len(s) < 2:
        return 0.0
    sd = float(s.std(ddof=1))
    mu = float(s.mean())
    if sd < 1e-12:
        return math.inf if mu > 0 else -math.inf
    return mu / sd


# ONC --------------------------------------------------------------------------------------------------

def _base_clustering(dist: np.ndarray, max_k: int, n_init: int, seed: int) -> tuple[np.ndarray, float, bool]:
    """거리 행렬 하나에 대한 1단계 군집. (labels, 품질, 상한 도달)."""
    n = len(dist)
    if n <= 1 or dist.max() <= math.sqrt(0.5 * (1 - RHO_ONE_CLUSTER)):
        return np.zeros(n, dtype=int), float("nan"), False
    e = np.sqrt(_sqdist(dist, dist))
    kmax = min(n - 1, max_k)
    if kmax < 2:                                         # n = 2 이고 상관 < 0.99
        return np.arange(n), float("nan"), False
    best_lab, best_q, best_k = None, -math.inf, 0
    for k in range(2, kmax + 1):
        lab = _kmeans(dist, k, n_init, seed)
        q = _quality(_silhouette(e, lab))
        if best_lab is None or q > best_q:               # 동률은 작은 k 유지
            best_lab, best_q, best_k = lab, q, k
    return best_lab, best_q, best_k == kmax and kmax == max_k and kmax < n - 1


def _cluster_qualities(e: np.ndarray, labels: np.ndarray) -> np.ndarray:
    s = _silhouette(e, labels)
    return np.array([_quality(s[labels == j]) for j in range(int(labels.max()) + 1)])


def _renumber(labels: np.ndarray) -> np.ndarray:
    _, inv = np.unique(labels, return_inverse=True)
    return inv


def _onc(corr: np.ndarray, max_k: int, n_init: int, seed: int) -> tuple[np.ndarray, bool]:
    """ONC: 1단계 군집 후 평균 미만 품질 군집을 합쳐 재귀로 다시 군집, 평균 군집 품질이 개선되면 채택."""
    dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, None))
    np.fill_diagonal(dist, 0.0)
    labels, _, hit = _base_clustering(dist, max_k, n_init, seed)
    k = int(labels.max()) + 1
    if k <= 2 or len(dist) <= 3:
        return labels, hit
    e = np.sqrt(_sqdist(dist, dist))
    qs = _cluster_qualities(e, labels)
    finite = qs[np.isfinite(qs)]
    if len(finite) == 0:
        return labels, hit
    redo = [j for j in range(k) if qs[j] < finite.mean()]
    if len(redo) <= 1:
        return labels, hit
    members = np.flatnonzero(np.isin(labels, redo))
    sub_lab, sub_hit = _onc(corr[np.ix_(members, members)], max_k, n_init, seed)
    new = labels.copy()
    keep = [j for j in range(k) if j not in set(redo)]
    base = len(keep)
    for r, j in enumerate(keep):
        new[labels == j] = r
    new[members] = base + sub_lab
    new = _renumber(new)
    q_new = _cluster_qualities(e, new)
    q_new = q_new[np.isfinite(q_new)]
    if len(q_new) and q_new.mean() > finite.mean():
        return new, hit or sub_hit
    return labels, hit


# 공개 API ---------------------------------------------------------------------------------------------

def cluster_trials(returns: pd.DataFrame, sr_daily: pd.Series, max_k: int | None = None,
                   n_init: int = 10, seed: int = 0) -> ClusterResult:
    """run 일 수익률을 상관으로 군집해 실효 N 과 군집 V 를 낸다.

    `returns`: 인덱스 = UTC 일자, 열 = run 키, 값 = 일 수익률(정렬 끝, 거래 없는 날 0). `sr_daily`: run 키 →
    일 Sharpe(비연환산, 정의 불가 NaN). `max_k` 기본 `DEFAULT_MAX_K`. 같은 입력 → 같은 출력(열 순서 무관).
    """
    n_input = returns.shape[1]
    if n_input == 0:
        return ClusterResult(0, float("nan"), pd.Series(dtype=int), [], float("nan"), 0, 0)
    keys = _sorted_keys(returns.columns)
    x = returns[keys].fillna(0.0).to_numpy(dtype=float) + 0.0   # +0.0: -0.0 → 0.0
    sr = pd.Series(sr_daily).reindex(keys).astype(float)

    # 1) 완전히 같은 열 합치기(정렬 순 첫 키가 대표 열)
    first_of: dict[bytes, int] = {}
    uniq_of = np.empty(n_input, dtype=int)               # 열 → 고유 열 번호
    uniq_cols: list[int] = []
    for i in range(n_input):
        b = np.ascontiguousarray(x[:, i]).tobytes()
        if b not in first_of:
            first_of[b] = len(uniq_cols)
            uniq_cols.append(i)
        uniq_of[i] = first_of[b]

    # 2) 분산 0 고유 열은 단독 군집, 나머지만 ONC
    ux = x[:, uniq_cols]
    sd = ux.std(axis=0, ddof=1) if len(ux) > 1 else np.zeros(ux.shape[1])
    const = ~(sd > 0)
    live = np.flatnonzero(~const)
    group = np.full(len(uniq_cols), -1)                  # 고유 열 → 임시 군집 id
    hit = False
    nxt = 0
    e_lab = np.zeros(0, dtype=int)
    if len(live):
        corr = np.corrcoef(ux[:, live], rowvar=False) if len(live) > 1 else np.ones((1, 1))
        corr = np.atleast_2d(corr)
        lab, hit = _onc(corr, DEFAULT_MAX_K if max_k is None else max_k, n_init, seed)
        group[live] = lab
        nxt = int(lab.max()) + 1
        e_lab = lab
    for u in np.flatnonzero(const):
        group[u] = nxt
        nxt += 1

    col_group = group[uniq_of]                           # 열 → 임시 군집 id
    # 3) 결정적 번호: 군집 최소 키(정렬 순 = 열 인덱스)의 순서
    order = {}
    for i in range(n_input):
        order.setdefault(int(col_group[i]), len(order))
    num = np.array([order[int(g)] for g in col_group])
    k = len(order)
    labels = pd.Series(num, index=keys, name="cluster")

    # 4) 대표 run: 군집 sr_daily 중앙값에 가장 가까운 run (동률 → 정렬상 앞 키)
    reps = []
    for c in range(k):
        idx = np.flatnonzero(num == c)
        vals = sr.to_numpy()[idx]
        ok = np.isfinite(vals)
        if not ok.any():
            reps.append(keys[idx[0]])
            continue
        med = float(np.median(vals[ok]))
        dd = np.where(ok, np.abs(vals - med), np.inf)
        reps.append(keys[idx[int(dd.argmin())]])         # argmin: 첫 최소 = 정렬상 앞 키
    v = sr_variance({"sr_daily": sr[r]} for r in reps) if k >= 2 else float("nan")

    # 5) 선택된 분할 품질(비상수 고유 열만, 군집 < 2 이면 NaN)
    q = float("nan")
    if len(live) > 2:
        corr_full = np.atleast_2d(np.corrcoef(ux[:, live], rowvar=False))
        d = np.sqrt(np.clip(0.5 * (1.0 - corr_full), 0.0, None))
        np.fill_diagonal(d, 0.0)
        if e_lab.max() >= 1:
            q = _quality(_silhouette(np.sqrt(_sqdist(d, d)), _renumber(e_lab)))
    return ClusterResult(k, v, labels, reps, q, n_input, n_input, bool(hit))
