#!/usr/bin/env python3
"""FC-ACC-012: read-only, exploratory H+R+RBI plate-appearance diagnostic.

Requires the canonical point-in-time backtest JSONL. It never fetches data,
modifies a board, or interprets these historical rows as sportsbook offers.
The frozen specification is in engineering/FC_ACC_012_HRRBI_PA_SPEC.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path

YEARS = ("2024", "2025", "2026")
NEEDS = (1, 2, 3, 4)
RIDGE = 10.0
EPS = 0.01


def order_from_signal(value):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    raw = 9.0 - value * 8.0 / 100.0
    slot = round(raw)
    return slot if 1 <= slot <= 9 and abs(raw - slot) < 0.01 else None


def sigmoid(z):
    if z >= 0:
        q = math.exp(-z)
        return 1 / (1 + q)
    q = math.exp(z)
    return q / (1 + q)


def logit(p):
    p = min(1 - EPS, max(EPS, p))
    return math.log(p / (1 - p))


def solve(matrix, rhs):
    n = len(rhs)
    a = [list(row) + [rhs[i]] for i, row in enumerate(matrix)]
    for j in range(n):
        pivot = max(range(j, n), key=lambda i: abs(a[i][j]))
        if abs(a[pivot][j]) < 1e-10:
            raise ValueError("singular information matrix")
        a[j], a[pivot] = a[pivot], a[j]
        scale = a[j][j]
        a[j] = [v / scale for v in a[j]]
        for i in range(n):
            if i == j:
                continue
            factor = a[i][j]
            a[i] = [a[i][k] - factor * a[j][k] for k in range(n + 1)]
    return [a[i][-1] for i in range(n)]


def fit(rows, use_pa):
    """Line-specific ridge logistic fit around unchanged raw-model mapping."""
    d = 3 if use_pa else 2
    beta = [0.0, 1.0] + ([0.0] if use_pa else [])
    for _ in range(50):
        grad = [0.0] * d
        hess = [[0.0] * d for _ in range(d)]
        for row in rows:
            x = [1.0, logit(row["p"])] + ([row["pa_expected"] - 4.0] if use_pa else [])
            p = sigmoid(sum(beta[j] * x[j] for j in range(d)))
            w = p * (1 - p)
            for j in range(d):
                grad[j] += (row["y"] - p) * x[j]
                for k in range(d):
                    hess[j][k] += w * x[j] * x[k]
        target = [0.0, 1.0] + ([0.0] if use_pa else [])
        for j in range(d):
            grad[j] -= RIDGE * (beta[j] - target[j])
            hess[j][j] += RIDGE
        step = solve(hess, grad)
        beta = [beta[j] + step[j] for j in range(d)]
        if max(abs(v) for v in step) < 1e-8:
            break
    return beta


def predict(row, beta):
    x = [1.0, logit(row["p"])]
    if len(beta) == 3:
        x.append(row["pa_expected"] - 4.0)
    return sigmoid(sum(b * v for b, v in zip(beta, x)))


def load(path):
    """Reject ambiguous identities and invalid probabilities; never infer missing rows."""
    digest = hashlib.sha256()
    pa_games, market_rows = {}, []
    code_shas = set()
    row_count = 0
    seen = set()
    with Path(path).open("rb") as source:
        for number, raw in enumerate(source, 1):
            digest.update(raw)
            if not raw.strip():
                continue
            r = json.loads(raw)
            year = str(r.get("date", ""))[:4]
            if year not in YEARS:
                continue
            row_count += 1
            if year == "2024":
                pa = r.get("actual_pa")
                slot = order_from_signal((r.get("signals") or {}).get("lineup_slot"))
                if pa is not None and slot is not None:
                    game_key = (r.get("date"), r.get("game_pk"), r.get("player_id"))
                    if any(v is None for v in game_key):
                        raise ValueError(f"missing player-game PA identity at line {number}")
                    old = pa_games.get(game_key)
                    if old is not None and (old["actual_pa"], old["signals"]["lineup_slot"]) != (pa, r["signals"]["lineup_slot"]):
                        raise ValueError(f"conflicting same-player-game PA/order: {game_key}")
                    pa_games[game_key] = {"date": r["date"], "game_pk": r["game_pk"],
                                          "player_id": r["player_id"], "actual_pa": pa,
                                          "signals": {"lineup_slot": r["signals"]["lineup_slot"]}}
            if r.get("prop_type") != "hits_runs_rbis":
                continue
            code_sha = r.get("code_git_sha")
            if not isinstance(code_sha, str) or len(code_sha) < 7 or not r.get("backtest_generated_at"):
                raise ValueError(f"missing H+R+RBI replay-code or generation provenance at line {number}")
            code_shas.add(code_sha)
            need = r.get("needs")
            key = (r.get("date"), r.get("game_pk"), r.get("player_id"), need)
            if any(v is None for v in key) or key in seen:
                raise ValueError(f"missing or duplicate H+R+RBI identity at line {number}: {key}")
            seen.add(key)
            if need not in NEEDS or r.get("outcome") not in (0, 1):
                raise ValueError(f"invalid threshold/outcome at line {number}")
            p = r.get("predicted_prob")
            if p is not None and (not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 < p < 1):
                raise ValueError(f"invalid probability at line {number}")
            if p is not None and r.get("actual") is not None:
                if bool(r["actual"] >= need) != bool(r["outcome"]):
                    raise ValueError(f"threshold/outcome mismatch at line {number}")
            market_rows.append(r)
    if not row_count:
        raise ValueError("no 2024-2026 rows: canonical dataset unavailable or empty")
    if len(code_shas) != 1:
        raise ValueError(f"mixed H+R+RBI replay-code regimes: {sorted(code_shas)}")
    return digest.hexdigest(), row_count, list(pa_games.values()), market_rows, next(iter(code_shas))


def training_pa_means(all_rows):
    games = {}
    for r in all_rows:
        if str(r.get("date", ""))[:4] != "2024":
            continue
        key = (r.get("date"), r.get("game_pk"), r.get("player_id"))
        slot = order_from_signal((r.get("signals") or {}).get("lineup_slot"))
        pa = r.get("actual_pa")
        if slot is None or pa is None:
            continue
        if not isinstance(pa, (int, float)) or not 0 <= pa <= 10:
            raise ValueError(f"invalid actual PA: {key}")
        value = (slot, pa)
        if key in games and games[key] != value:
            raise ValueError(f"conflicting same-player-game PA/order: {key}")
        games[key] = value
    by_slot = defaultdict(list)
    for slot, pa in games.values():
        by_slot[slot].append(pa)
    if any(len(by_slot[k]) < 50 for k in range(1, 10)):
        raise ValueError("insufficient 2024 PA support for one or more batting slots")
    return {k: sum(by_slot[k]) / len(by_slot[k]) for k in range(1, 10)}, {k: len(by_slot[k]) for k in range(1, 10)}


def auc(rows, field):
    ranked = sorted(rows, key=lambda r: r[field])
    positive = sum(r["y"] for r in ranked)
    negative = len(ranked) - positive
    if not positive or not negative:
        return None
    rank_sum = 0.0
    index = 0
    while index < len(ranked):
        end = index + 1
        while end < len(ranked) and ranked[end][field] == ranked[index][field]:
            end += 1
        rank_sum += sum(r["y"] for r in ranked[index:end]) * ((index + 1 + end) / 2)
        index = end
    return (rank_sum - positive * (positive + 1) / 2) / (positive * negative)


def metrics(rows, field):
    if not rows:
        return None
    n = len(rows)
    ps = [min(1 - 1e-12, max(1e-12, r[field])) for r in rows]
    return {
        "n": n,
        "mean_probability": sum(ps) / n,
        "hit_rate": sum(r["y"] for r in rows) / n,
        "brier": sum((p - r["y"]) ** 2 for p, r in zip(ps, rows)) / n,
        "log_loss": -sum(r["y"] * math.log(p) + (1 - r["y"]) * math.log1p(-p) for p, r in zip(ps, rows)) / n,
        "auc": auc(rows, field),
    }


def paired_bootstrap(rows, field_a, field_b, repetitions=1000):
    by_date = defaultdict(lambda: [0.0, 0])
    for r in rows:
        by_date[r["date"]][0] += (r[field_a] - r["y"]) ** 2 - (r[field_b] - r["y"]) ** 2
        by_date[r["date"]][1] += 1
    days = [by_date[d] for d in sorted(by_date)]
    if len(days) < 2:
        return None
    rng = random.Random(12012)
    diffs = []
    for _ in range(repetitions):
        sample = rng.choices(days, k=len(days))
        diffs.append(sum(v[0] for v in sample) / sum(v[1] for v in sample))
    diffs.sort()
    return {"mean_delta_brier": sum(diffs) / len(diffs), "ci95": [diffs[int(0.025 * repetitions)], diffs[int(0.975 * repetitions) - 1]], "cluster": "date"}


def decisions(rows, base, challenger):
    base_set = {r["key"] for r in rows if r[base] >= 0.60}
    challenger_set = {r["key"] for r in rows if r[challenger] >= 0.60}
    groups = defaultdict(list)
    for r in rows:
        groups[(r["date"], r["needs"])].append(r)
    same_n = {"base": 0, "challenger": 0, "overlap": 0, "added": 0, "removed": 0, "added_wins": 0, "removed_wins": 0}
    for group in groups.values():
        n = sum(r[base] >= 0.60 for r in group)
        b = {r["key"] for r in group if r[base] >= 0.60}
        c = {r["key"] for r in sorted(group, key=lambda r: (-r[challenger], r["key"]))[:n]}
        outcomes = {r["key"]: r["y"] for r in group}
        same_n["base"] += len(b)
        same_n["challenger"] += len(c)
        same_n["overlap"] += len(b & c)
        same_n["added"] += len(c - b)
        same_n["removed"] += len(b - c)
        same_n["added_wins"] += sum(outcomes[k] for k in c - b)
        same_n["removed_wins"] += sum(outcomes[k] for k in b - c)
    return {"fixed_0_60": {"base_n": len(base_set), "challenger_n": len(challenger_set), "added": len(challenger_set - base_set), "removed": len(base_set - challenger_set)}, "same_n_by_date_and_line": same_n}


def analyze(path):
    sha, row_count, pa_rows, market_rows, code_sha = load(path)
    pa_means, pa_counts = training_pa_means(pa_rows)
    rows = []
    missing = defaultdict(int)
    for r in market_rows:
        year = str(r["date"])[:4]
        if r.get("predicted_prob") is None:
            missing[(year, "probability")] += 1
            continue
        slot = order_from_signal((r.get("signals") or {}).get("lineup_slot"))
        if slot is None:
            missing[(year, "order")] += 1
            continue
        rows.append({"date": r["date"], "needs": r["needs"], "key": (r["date"], r["game_pk"], r["player_id"], r["needs"]), "year": year, "order": slot, "pa_expected": pa_means[slot], "p": r["predicted_prob"], "y": r["outcome"]})
    report = {"dataset_sha256": sha, "dataset_path": str(Path(path).resolve()), "replay_code_git_sha": code_sha, "rows_all": row_count, "hrrbi_rows": len(market_rows), "paired_rows": len(rows), "missing": {f"{y}:{reason}": n for (y, reason), n in sorted(missing.items())}, "training_pa_mean_by_order": pa_means, "training_pa_n_by_order": pa_counts, "by_line": {}, "overall_by_year": {}}
    scored = defaultdict(list)
    for need in NEEDS:
        training = [r for r in rows if r["year"] == "2024" and r["needs"] == need]
        if len(training) < 100 or len({r["y"] for r in training}) < 2:
            report["by_line"][str(need)] = {"status": "insufficient_2024_training"}
            continue
        control = fit(training, False)
        challenger = fit(training, True)
        result = {"control_coefficients": control, "challenger_coefficients": challenger, "years": {}}
        for year in YEARS:
            subset = [dict(r) for r in rows if r["year"] == year and r["needs"] == need]
            for r in subset:
                r["control"] = predict(r, control)
                r["challenger"] = predict(r, challenger)
            scored[year].extend(subset)
            if not subset:
                result["years"][year] = {"status": "no_rows"}
                continue
            bands = {}
            for name, selected in {
                "order_1_3": [r for r in subset if r["order"] <= 3],
                "order_4_6": [r for r in subset if 4 <= r["order"] <= 6],
                "order_7_9": [r for r in subset if r["order"] >= 7],
                "expected_pa_below_4": [r for r in subset if r["pa_expected"] < 4],
                "expected_pa_at_least_4": [r for r in subset if r["pa_expected"] >= 4],
            }.items():
                bands[name] = {field: metrics(selected, field) for field in ("p", "control", "challenger")}
            result["years"][year] = {"baseline": metrics(subset, "p"), "control": metrics(subset, "control"), "challenger": metrics(subset, "challenger"), "challenger_vs_control_cluster_bootstrap": paired_bootstrap(subset, "challenger", "control"), "bands": bands, "decisions_vs_baseline": decisions(subset, "p", "challenger"), "decisions_vs_control": decisions(subset, "control", "challenger")}
        report["by_line"][str(need)] = result
    for year in YEARS:
        subset = scored[year]
        report["overall_by_year"][year] = (
            {"baseline": metrics(subset, "p"), "control": metrics(subset, "control"),
             "challenger": metrics(subset, "challenger"),
             "challenger_vs_control_cluster_bootstrap": paired_bootstrap(subset, "challenger", "control"),
             "decisions_vs_baseline": decisions(subset, "p", "challenger"),
             "decisions_vs_control": decisions(subset, "control", "challenger")}
            if subset else {"status": "no_rows"})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("canonical_jsonl", help="existing, independently verified canonical backtest rows")
    args = parser.parse_args()
    print(json.dumps(analyze(args.canonical_jsonl), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

