"""FC-ACC-004: fixed, outcome-only pitcher workload diagnostic.

Public MLB StatsAPI game logs are retrieved with response hashes. This is
retrospective exploratory work, never a source of historical sportsbook offers.
No tuning or threshold search is performed after the data are fetched.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw_all_starters"
API = "https://statsapi.mlb.com/api/v1"
SEASONS = (2024, 2025, 2026)
LEAGUE_BF = 23.1
BF_N0 = 2.62
RIDGE = 10.0


def fetch(path: str, params: dict[str, object], cache: Path) -> dict:
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        url = API + path + "?" + urllib.parse.urlencode(params, safe="[],")
        request = urllib.request.Request(url, headers={"User-Agent": "Full-Count-offline-research/1.0"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    body = response.read()
                cache.write_bytes(body)
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(1 + 2 * attempt)
        time.sleep(0.12)
    body = cache.read_bytes()
    return json.loads(body)


def raw_manifest() -> list[dict]:
    return [{"path": str(p.relative_to(ROOT)).replace("\\", "/"),
             "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
            for p in sorted(RAW.glob("*.json"))]


def load_starts() -> tuple[list[dict], dict]:
    candidates: set[int] = set()
    leaderboard = {}
    for season in SEASONS:
        d = fetch("/stats", {"stats": "season", "group": "pitching", "season": season,
                             "sportIds": 1, "playerPool": "ALL", "limit": 1000},
                  RAW / f"season_{season}.json")
        splits = d["stats"][0]["splits"]
        ids = {int(s["player"]["id"]) for s in splits if int(s["stat"].get("gamesStarted") or 0) >= 1}
        leaderboard[season] = {"total_pitchers": len(splits), "starters_ge1": len(ids)}
        candidates |= ids
    ids = sorted(candidates)
    starts = []
    missing = defaultdict(int)
    for season in SEASONS:
        for batch_index in range(0, len(ids), 25):
            batch = ids[batch_index:batch_index + 25]
            d = fetch("/people", {"personIds": ",".join(map(str, batch)),
                                  "hydrate": f"stats(group=[pitching],type=[gameLog],season={season},gameType=R)"},
                      RAW / f"logs_{season}_{batch_index // 25:02}.json")
            for person in d.get("people", []):
                for block in person.get("stats", []):
                    if block.get("type", {}).get("displayName") != "gameLog":
                        continue
                    for item in block.get("splits", []):
                        stat = item["stat"]
                        if stat.get("gamesStarted") != 1 or item.get("gameType") != "R":
                            continue
                        try:
                            pitches = int(stat["numberOfPitches"])
                            bf = int(stat["battersFaced"])
                            outs = int(stat["outs"])
                            ks = int(stat["strikeOuts"])
                            day = str(item["date"])
                            game_pk = int(item["game"]["gamePk"])
                            opponent = int(item["opponent"]["id"])
                        except (KeyError, TypeError, ValueError):
                            missing["required_fields"] += 1
                            continue
                        if not (0 <= outs <= 27 and 0 < bf <= 60 and 0 < pitches <= 200 and 0 <= ks <= bf):
                            missing["invalid_stat"] += 1
                            continue
                        starts.append({"season": season, "date": day, "game_pk": game_pk,
                                       "pitcher_id": int(person["id"]), "opponent_id": opponent,
                                       "pitches": pitches, "bf": bf, "outs": outs, "ks": ks})
    keys = [(x["pitcher_id"], x["game_pk"]) for x in starts]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate pitcher-game")
    return starts, {"leaderboard": leaderboard, "candidate_pitchers": len(ids),
                    "missing": dict(missing), "retrieved_at_utc": datetime.now(timezone.utc).isoformat()}


def build_rows(starts: list[dict]) -> tuple[list[dict], dict]:
    by_pitcher = defaultdict(list)
    for start in starts:
        by_pitcher[start["pitcher_id"]].append(start)
    rows = []
    excluded = defaultdict(int)
    for prior in by_pitcher.values():
        prior.sort(key=lambda x: (x["date"], x["game_pk"]))
        for index, current in enumerate(prior):
            # Same-day doubleheaders cannot establish ordered first pitch from
            # date-only game logs. Exclude both games from modeled opportunities.
            if any(x["date"] == current["date"] and x["game_pk"] != current["game_pk"] for x in prior):
                excluded["same_day_multiple_games"] += 1
                continue
            history = [x for x in prior[:index] if x["date"] < current["date"]]
            season_history = [x for x in history if x["season"] == current["season"]]
            if len(season_history) < 3:
                excluded["fewer_than_three_prior_season_starts"] += 1
                continue
            day = date.fromisoformat(current["date"])
            l14 = [x for x in season_history if 0 < (day - date.fromisoformat(x["date"])).days <= 14]
            n = len(l14)
            mean_bf = sum(x["bf"] for x in l14) / n if n else None
            baseline_bf = max(12.0, min(30.0, (n * mean_bf + BF_N0 * LEAGUE_BF) / (n + BF_N0))) if n else LEAGUE_BF
            last3 = season_history[-3:]
            off_days = (day - date.fromisoformat(season_history[-1]["date"])).days - 1
            if off_days < 0:
                excluded["negative_rest"] += 1
                continue
            # Fixed candidate features: pitch-count slope in 20-pitch units;
            # short rest means <=3 FULL off-days. No postgame inputs.
            pitch_slope_20 = (last3[-1]["pitches"] - last3[0]["pitches"]) / 20.0
            short_rest = int(off_days <= 3)
            hist_bf = sum(x["bf"] for x in season_history)
            hist_outs = sum(x["outs"] for x in season_history)
            hist_ks = sum(x["ks"] for x in season_history)
            # Fixed weak priors prevent unstable early-season rates.
            out_rate = (hist_outs + 100 * 0.70) / (hist_bf + 100)
            k_rate = (hist_ks + 100 * 0.22) / (hist_bf + 100)
            rows.append({**current, "baseline_bf": baseline_bf,
                         "pitch_slope_20": pitch_slope_20, "short_rest": short_rest,
                         "off_days": off_days, "out_rate": out_rate, "k_rate": k_rate,
                         "prior_season_starts": len(season_history), "l14_starts": n,
                         "last3_pitches": [x["pitches"] for x in last3]})
    return rows, dict(excluded)


def fit_one(train: list[dict], target: str) -> list[float]:
    x = np.asarray([[1.0, r["pitch_slope_20"], r["short_rest"]] for r in train])
    y = np.asarray([r[target] - r["baseline_bf"] * (r["out_rate"] if target == "outs" else r["k_rate"])
                    for r in train])
    # Fixed ridge, no post-evaluation fit or hyperparameter selection.
    penalty = np.diag([0.0, RIDGE, RIDGE])
    return np.linalg.solve(x.T @ x + penalty, x.T @ y).tolist()


def metrics(rows: list[dict], coefs: dict[str, list[float]]) -> dict:
    result = {"n": len(rows), "pitchers": len({r["pitcher_id"] for r in rows}),
              "games": len({r["game_pk"] for r in rows})}
    for target, threshold in (("outs", 15), ("ks", 5)):
        rate = "out_rate" if target == "outs" else "k_rate"
        coeff = coefs[target]
        vals = []
        for r in rows:
            base = r["baseline_bf"] * r[rate]
            challenger = base + coeff[0] + coeff[1] * r["pitch_slope_20"] + coeff[2] * r["short_rest"]
            actual = r[target]
            vals.append((base, challenger, actual, r["game_pk"], r["pitcher_id"]))
        if not vals:
            continue
        added = [v for v in vals if v[0] < threshold <= v[1]]
        removed = [v for v in vals if v[1] < threshold <= v[0]]
        result[target] = {
            "threshold_for_diagnostic_only": threshold,
            "baseline_mae": sum(abs(v[0] - v[2]) for v in vals) / len(vals),
            "challenger_mae": sum(abs(v[1] - v[2]) for v in vals) / len(vals),
            "baseline_over_decisions": sum(v[0] >= threshold for v in vals),
            "challenger_over_decisions": sum(v[1] >= threshold for v in vals),
            "added_over": len(added), "added_over_winners": sum(v[2] >= threshold for v in added),
            "removed_over": len(removed), "removed_over_winners": sum(v[2] >= threshold for v in removed),
            "added_ids": [[v[3], v[4]] for v in added],
            "removed_ids": [[v[3], v[4]] for v in removed]}
    return result


def main() -> None:
    starts, source = load_starts()
    rows, excluded = build_rows(starts)
    by_year = {year: [r for r in rows if r["season"] == year] for year in SEASONS}
    coefs = {target: fit_one(by_year[2024], target) for target in ("outs", "ks")}
    report = {"status": "RETROSPECTIVE_EXPLORATORY_OUTCOME_ONLY",
              "source": "MLB StatsAPI official public season stats and pitching game logs",
              "source_metadata": source, "raw_manifest": raw_manifest(),
              "all_starts_by_year": {year: sum(r["season"] == year for r in starts) for year in SEASONS},
              "excluded": excluded, "model_rows_by_year": {year: len(by_year[year]) for year in SEASONS},
              "fixed_design": {"baseline": "production L14 BF shrinkage 23.1, n0=2.62; outcome conversion uses as-of season rates with 100-BF weak priors",
                               "challenger": "2024 ridge residual adjustment using last-three pitch-count slope and <=3 full off-days",
                               "ridge": RIDGE, "outs_threshold": 15, "ks_threshold": 5,
                               "evaluation": "fit 2024, inspect 2025 validation and 2026 already-exposed test; neither confirmatory"},
              "coefficients_2024": coefs,
              "metrics": {year: metrics(by_year[year], coefs) for year in SEASONS}}
    (ROOT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    with (ROOT / "model_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = [k for k in rows[0] if k != "last3_pitches"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: r[k] for k in fields} for r in rows)
    print(json.dumps({k: report[k] for k in ("all_starts_by_year", "excluded", "model_rows_by_year", "coefficients_2024", "metrics")}, indent=2))


if __name__ == "__main__":
    main()
