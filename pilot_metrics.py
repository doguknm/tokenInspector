"""Pilot statistics (backend.md §7): pure functions over one paired complete-case cohort.

Thresholds and the minimum cohort size are pre-registered here and printed in the report header.
"""

from __future__ import annotations

import math
import random
from typing import Callable, Optional, Sequence

MIN_PAIRS = 40
RHO_MIN = 0.5
RHO_ADVANTAGE_MIN = 0.10
KAPPA_MIN = 0.40
LEVELS = 5
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_MAX_DROP = 0.10


def round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = rank
        i = j + 1
    return ranks


def _pearson(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None  # constant labels or scores: undefined
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy)


def spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    return _pearson(average_ranks(x), average_ranks(y))


def mae(predicted: Sequence[float], truth: Sequence[float]) -> Optional[float]:
    if not predicted:
        return None
    return sum(abs(p - t) for p, t in zip(predicted, truth)) / len(predicted)


def agreement(predicted: Sequence[int], truth: Sequence[int]) -> tuple[Optional[float], Optional[float]]:
    if not predicted:
        return None, None
    n = len(predicted)
    exact = sum(p == t for p, t in zip(predicted, truth)) / n
    within = sum(abs(p - t) <= 1 for p, t in zip(predicted, truth)) / n
    return exact, within


def confusion(truth: Sequence[int], predicted: Sequence[int], k: int = LEVELS) -> list[list[int]]:
    matrix = [[0] * k for _ in range(k)]
    for t, p in zip(truth, predicted):
        matrix[t][p] += 1
    return matrix


def weighted_kappa(a: Sequence[int], b: Sequence[int], k: int = LEVELS) -> Optional[float]:
    """Quadratic-weighted kappa; None when expected disagreement is 0."""
    n = len(a)
    if n == 0:
        return None
    observed = confusion(a, b, k)
    row = [sum(observed[i]) for i in range(k)]
    col = [sum(observed[i][j] for i in range(k)) for j in range(k)]
    weight = [[((i - j) ** 2) / ((k - 1) ** 2) for j in range(k)] for i in range(k)]
    observed_disagreement = sum(weight[i][j] * observed[i][j] for i in range(k) for j in range(k)) / n
    expected_disagreement = sum(weight[i][j] * row[i] * col[j] for i in range(k) for j in range(k)) / (n * n)
    if expected_disagreement == 0:
        return None
    return 1 - observed_disagreement / expected_disagreement


def bootstrap(pairs: list, statistic: Callable[[list], Optional[float]], seed: int,
              resamples: int = BOOTSTRAP_RESAMPLES) -> dict:
    """Percentile 95% CI; resamples with an undefined statistic are dropped and counted."""
    rng = random.Random(seed)
    values, dropped = [], 0
    for _ in range(resamples):
        sample = [pairs[rng.randrange(len(pairs))] for _ in pairs] if pairs else []
        value = statistic(sample)
        if value is None:
            dropped += 1
        else:
            values.append(value)
    if not pairs or dropped > BOOTSTRAP_MAX_DROP * resamples:
        return {"ci": None, "dropped": dropped, "available": False}
    values.sort()
    lo = values[int(0.025 * (len(values) - 1))]
    hi = values[int(0.975 * (len(values) - 1))]
    return {"ci": (lo, hi), "dropped": dropped, "available": True}


def verdict(n_pairs: int, rho_jev: Optional[float], rho_rs1: Optional[float], kappa: Optional[float]) -> str:
    if n_pairs < MIN_PAIRS or rho_jev is None or rho_rs1 is None or kappa is None:
        return "INCONCLUSIVE — insufficient evidence"
    passed = rho_jev >= RHO_MIN and rho_jev - rho_rs1 >= RHO_ADVANTAGE_MIN and kappa >= KAPPA_MIN
    return "PASS" if passed else "FAIL"


def cohort_metrics(pairs: list[dict], seed: int) -> dict:
    """pairs: {'human': 0..4, 'jev': float 0..4, 'rs1': 0..4 (RS-v1 - 1), 'confidence', 'provider', 'cost'}."""
    human = [p["human"] for p in pairs]
    jev = [p["jev"] for p in pairs]
    rs1 = [p["rs1"] for p in pairs]
    jev_int = [round_half_up(v) for v in jev]
    rho_jev, rho_rs1 = spearman(jev, human), spearman(rs1, human)
    kappa = weighted_kappa(human, jev_int)
    exact, within = agreement(jev_int, human)
    close = [p["confidence"] for p in pairs if abs(p["jev"] - p["human"]) <= 0.5]
    far = [p["confidence"] for p in pairs if abs(p["jev"] - p["human"]) > 0.5]

    def delta(sample):
        a = spearman([s["jev"] for s in sample], [s["human"] for s in sample])
        b = spearman([s["rs1"] for s in sample], [s["human"] for s in sample])
        return None if a is None or b is None else a - b

    providers: dict[str, int] = {}
    for p in pairs:
        providers[p["provider"] or "unknown"] = providers.get(p["provider"] or "unknown", 0) + 1
    return {
        "n": len(pairs),
        "rho_jev": rho_jev,
        "rho_rs1": rho_rs1,
        "rho_delta": None if rho_jev is None or rho_rs1 is None else rho_jev - rho_rs1,
        "kappa_jev": kappa,
        "mae_jev": mae(jev, human),
        "mae_rs1": mae(rs1, human),
        "exact": exact,
        "within1": within,
        "confusion": confusion(human, jev_int),
        "confidence_close": sum(close) / len(close) if close else None,
        "confidence_far": sum(far) / len(far) if far else None,
        "providers": providers,
        "cost_usd": sum(p["cost"] or 0.0 for p in pairs),
        "ci": {
            "rho_jev": bootstrap(pairs, lambda s: spearman([x["jev"] for x in s], [x["human"] for x in s]), seed),
            "rho_delta": bootstrap(pairs, delta, seed),
            "kappa_jev": bootstrap(
                pairs, lambda s: weighted_kappa([x["human"] for x in s], [round_half_up(x["jev"]) for x in s]), seed),
        },
        "verdict": verdict(len(pairs), rho_jev, rho_rs1, kappa),
    }
