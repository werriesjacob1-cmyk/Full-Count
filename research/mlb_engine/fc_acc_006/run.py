"""FC-ACC-006: retrospective, outcome-only hitter substitution experiment.

Uses only public official MLB regular-season schedules and box scores. The
target game's final box score is used solely for its outcome; every predictor
is built from games on earlier *dates*. No sportsbook or pregame-lineup claim.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.request import Request, urlopen

import numpy as np


BASE_URL = "https://statsapi.mlb.com/api/v1"
YEARS = (2024, 2025, 2026)
LOOKBACK = 20
SHRINK_STARTS = 10
MAX_WORKERS = 4
TIMEOUT = 20


def get_bytes(url: str, attempts: int = 4) -> bytes:
    for attempt in range(attempts):
        try:
            req = Request(url, headers={"User-Agent": "FullCountResearch/FC-ACC-006"})
            with urlopen(req, timeout=TIMEOUT) as response:
                body = response.read(5_000_001)
                if len(body) > 5_000_000:
                    raise ValueError(f"response too large: {url}")
                return body
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(min(2 ** attempt, 8))
    raise AssertionError("unreachable")


def get_json(url: str) -> dict:
    return json.loads(get_bytes(url))


def schedule_games(year: int) -> list[dict]:
    url = (f"{BASE_URL}/schedule?sportId=1&gameType=R"
           f"&startDate={year}-03-01&endDate={year}-11-01")
    raw = get_bytes(url)
    data = json.loads(raw)
    schedule_sha256 = hashlib.sha256(raw).hexdigest()
    games = []
    for day in data.get("dates", []):
        for game in day.get("games", []):
            if game.get("gameType") != "R":
                continue
            if game.get("status", {}).get("abstractGameState") != "Final":
                continue
            games.append({"game_pk": game["gamePk"],
                          "date": game["officialDate"],
                          "game_time": game["gameDate"],
                          "season": year,
                          "schedule_url": url,
                          "schedule_sha256": schedule_sha256})
    # MLB can repeat a postponed/rescheduled game in this schedule response.
    # The official gamePk, not schedule-row count, is the game identity.
    unique = {g["game_pk"]: g for g in games}
    return sorted(unique.values(), key=lambda g: (g["date"], g["game_pk"]))


def cache_boxscore(game: dict, cache_root: Path) -> dict:
    pk = game["game_pk"]
    url = f"{BASE_URL}/game/{pk}/boxscore"
    path = cache_root / str(game["season"]) / f"{pk}.json.gz"
    if path.exists():
        raw = gzip.decompress(path.read_bytes())
    else:
        raw = get_bytes(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(gzip.compress(raw, compresslevel=6))
        temporary.replace(path)
    return {**game, "url": url, "sha256": hashlib.sha256(raw).hexdigest(),
            "source_observed_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            "bytes": len(raw), "body": json.loads(raw)}


def parse_side(game: dict, side: str) -> tuple[list[dict], list[str]]:
    """Require nine unique x00 starters; x01+ are replacements in that slot.

    A replacement who never bats establishes an exit, but not a lost PA.
    x00 players switching defensive positions remain in the same batting slot.
    """
    team = game["body"]["teams"][side]
    by_slot: dict[int, list[dict]] = defaultdict(list)
    problems: list[str] = []
    for player in team.get("players", {}).values():
        code = player.get("battingOrder")
        if code is None:
            continue
        try:
            number = int(code)
        except (ValueError, TypeError):
            problems.append("malformed_batting_order")
            continue
        slot, sequence = divmod(number, 100)
        if slot not in range(1, 10) or sequence >= 100:
            problems.append("invalid_batting_order")
            continue
        batting = player.get("stats", {}).get("batting", {})
        pa = batting.get("plateAppearances")
        hits = batting.get("hits")
        if not isinstance(pa, int) or pa < 0 or not isinstance(hits, int) or hits < 0:
            problems.append("missing_or_invalid_batting_stats")
            continue
        by_slot[slot].append({"player_id": player["person"]["id"],
                              "name": player["person"]["fullName"],
                              "sequence": sequence, "pa": pa, "hits": hits,
                              "positions": [p.get("abbreviation") for p in player.get("allPositions", [])]})

    starters = []
    for slot in range(1, 10):
        members = sorted(by_slot.get(slot, []), key=lambda p: p["sequence"])
        if not members or len([p for p in members if p["sequence"] == 0]) != 1:
            problems.append("missing_or_duplicate_starter")
            continue
        if len({p["sequence"] for p in members}) != len(members):
            problems.append("duplicate_slot_sequence")
            continue
        starter = members[0]
        if starter["sequence"] != 0:
            problems.append("starter_sequence_not_zero")
            continue
        replacements = members[1:]
        lost_pa = sum(p["pa"] for p in replacements)
        starters.append({"game_pk": game["game_pk"], "date": game["date"],
                         "game_time": game["game_time"], "season": game["season"],
                         "team_side": side, "team_id": team["team"]["id"],
                         "player_id": starter["player_id"], "player_name": starter["name"],
                         "slot": slot, "pa": starter["pa"], "hits": starter["hits"],
                         "slot_pa": starter["pa"] + lost_pa,
                         "replaced": bool(replacements),
                         "lost_pa": lost_pa,
                         "lost_turn": lost_pa > 0,
                         "replacement_ids": [p["player_id"] for p in replacements],
                         "replacement_pa": [p["pa"] for p in replacements],
                         "source_url": game["url"], "source_sha256": game["sha256"],
                         "source_observed_at": game.get("source_observed_at")})
    # teams.*.battingOrder is the FINAL on-field order in many boxes. It can
    # contain substitutes, so using it to identify starters creates false
    # labels. Explicit x00 is the stable original-starter encoding.
    if len(starters) != 9:
        problems.append("not_nine_starters")
    return starters, sorted(set(problems))


def parse_game(game: dict) -> tuple[list[dict], list[str]]:
    rows = []
    problems = []
    for side in ("away", "home"):
        side_rows, side_problems = parse_side(game, side)
        rows.extend(side_rows)
        problems.extend(f"{side}:{p}" for p in side_problems)
    if len(rows) != 18 or problems:
        return [], sorted(set(problems))
    return rows, []


def collect(cache_root: Path, output: Path) -> None:
    all_games = [g for year in YEARS for g in schedule_games(year)]
    rows = []
    manifest = []
    problems = Counter()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(cache_boxscore, game, cache_root): game
                   for game in all_games}
        for completed, future in enumerate(as_completed(futures), 1):
            game = futures[future]
            try:
                payload = future.result()
                parsed, errors = parse_game(payload)
                for error in errors:
                    problems[error] += 1
                rows.extend(parsed)
                manifest.append({k: payload[k] for k in
                                 ("game_pk", "date", "season", "schedule_url",
                                  "schedule_sha256", "url", "sha256", "bytes",
                                  "source_observed_at")})
                if errors:
                    manifest[-1]["parse_errors"] = errors
            except Exception as exc:
                problems[f"fetch:{type(exc).__name__}"] += 1
                manifest.append({**game, "error": type(exc).__name__})
            if completed % 500 == 0:
                print(f"collected {completed}/{len(all_games)} games", flush=True)
    rows.sort(key=lambda r: (r["date"], r["game_pk"], r["team_side"], r["slot"]))
    manifest.sort(key=lambda r: r["game_pk"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    summary = {"observed_at": datetime.now(timezone.utc).isoformat(),
               "games_in_schedule": len(all_games),
               "games_parsed": len(rows) // 18,
               "rows": len(rows), "problems": dict(problems),
               "manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
               "manifest": manifest}
    output.with_suffix(".manifest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    hashes = ["season\tgame_pk\tdate\tboxscore_sha256\tsource_observed_at\tparse_status"]
    for record in manifest:
        status = ",".join(record["parse_errors"]) if record.get("parse_errors") else "OK"
        hashes.append("\t".join([*(str(record.get(k, "")) for k in
                                    ("season", "game_pk", "date", "sha256", "source_observed_at")),
                                  status]))
    output.with_suffix(".source_hashes.tsv").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "manifest"}, indent=2))


def load_rows(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def production_pa_function():
    """Load only the literal PA constants/function; avoid live-source imports.

    This executes the same production formula without importing requests,
    pybaseball, or any network-capable production module.
    """
    source = Path(__file__).resolve().parents[3] / "generate_picks.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    wanted = {"PA_BY_SLOT_INTERCEPT", "PA_PER_RUN", "LEAGUE_TEAM_RUNS_MEAN"}
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in wanted for t in node.targets):
            nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "project_batter_pa":
            nodes.append(node)
    namespace = {"clamp": lambda x, lo, hi: max(lo, min(hi, x))}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    assert wanted.issubset(namespace) and "project_batter_pa" in namespace
    return namespace["project_batter_pa"], hashlib.sha256(source.read_bytes()).hexdigest()


def bootstrap_ci(rows: list[dict], field: str, cluster: str, draws: int = 1000) -> list[float]:
    groups = defaultdict(lambda: [0.0, 0])
    for r in rows:
        key = r[cluster]
        groups[key][0] += r[field]
        groups[key][1] += 1
    arr = np.array(list(groups.values()), dtype=float)
    if not len(arr):
        return [math.nan, math.nan]
    rng = np.random.default_rng(6006)
    outcomes = np.empty(draws)
    for i in range(draws):
        sample = arr[rng.integers(0, len(arr), len(arr))]
        outcomes[i] = sample[:, 0].sum() / sample[:, 1].sum()
    return np.quantile(outcomes, [0.025, 0.975]).round(7).tolist()


def two_way_cluster_ci(rows: list[dict], field: str) -> list[float]:
    """Game + player sandwich interval, subtracting their intersection.

    Each starter appears at most once per game, so the intersection term is
    the individual sum of squared centered influences. The separate game and
    player bootstraps are also reported, not replaced by this approximation.
    """
    mean = sum(r[field] for r in rows) / len(rows)
    by_game = defaultdict(float)
    by_player = defaultdict(float)
    intersection = 0.0
    for r in rows:
        centered = r[field] - mean
        by_game[r["game_pk"]] += centered
        by_player[r["player_id"]] += centered
        intersection += centered * centered
    variance = (sum(v*v for v in by_game.values()) +
                sum(v*v for v in by_player.values()) - intersection) / len(rows)**2
    half_width = 1.96 * math.sqrt(max(variance, 0.0))
    return [round(mean-half_width, 7), round(mean+half_width, 7)]


def evaluate(rows: list[dict], output: Path) -> None:
    # Only 2024 determines these population priors. They are unavailable in
    # 2024 itself as a point-in-time predictor, so no 2024 score is claimed.
    dev = [r for r in rows if r["season"] == 2024]
    slot_prior = {}
    loss_given_exit = {}
    slot_total_pa_2024 = {}
    slot_starter_pa_2024 = {}
    for slot in range(1, 10):
        sample = [r for r in dev if r["slot"] == slot]
        slot_prior[slot] = sum(r["lost_turn"] for r in sample) / len(sample)
        exits = [r["lost_pa"] for r in sample if r["lost_turn"]]
        loss_given_exit[slot] = sum(exits) / len(exits) if exits else 0.0
        slot_total_pa_2024[slot] = sum(r["slot_pa"] for r in sample) / len(sample)
        slot_starter_pa_2024[slot] = sum(r["pa"] for r in sample) / len(sample)
    league_hit_pa = sum(r["hits"] for r in dev) / sum(r["pa"] for r in dev)

    # Production's current formula was fitted on 2026 data. Therefore it is
    # only a descriptive *secondary* sensitivity reference for 2025/26. The
    # primary chronology-safe baseline is the 2024 slot mean starter PA.
    project_batter_pa, production_source_sha256 = production_pa_function()
    production_pa_fallback = {slot: float(project_batter_pa(slot, None))
                              for slot in range(1, 10)}
    history = defaultdict(lambda: deque(maxlen=LOOKBACK))
    hit_history = defaultdict(lambda: deque(maxlen=40))
    scored = []
    # Conservative same-date exclusion: all outcomes from the current date
    # update history only after every game of that date has been scored.
    by_date = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r)
    for day in sorted(by_date):
        for r in by_date[day]:
            player = r["player_id"]
            slot = r["slot"]
            past = history[player]
            prior_n = len(past)
            risk = (sum(past) + SHRINK_STARTS * slot_prior[slot]) / (prior_n + SHRINK_STARTS)
            baseline = slot_starter_pa_2024[slot]
            adjusted = slot_total_pa_2024[slot] - risk * loss_given_exit[slot]
            adjusted = max(0.0, adjusted)
            production_base = production_pa_fallback[slot] - slot_prior[slot] * loss_given_exit[slot]
            production_adjusted = production_pa_fallback[slot] - risk * loss_given_exit[slot]
            past_hits = hit_history[player]
            prior_hits = sum(h for h, _ in past_hits)
            prior_pa = sum(pa for _, pa in past_hits)
            p_hit_pa = (prior_hits + 40 * league_hit_pa) / (prior_pa + 40)
            # Fractional-trial extension of P(>=1 hit). Same per-PA rate in
            # both arms isolates the effect of PA; not the production model.
            p0 = 1 - (1 - p_hit_pa) ** baseline
            p1 = 1 - (1 - p_hit_pa) ** adjusted
            if r["season"] in (2025, 2026):
                y = int(r["hits"] >= 1)
                scored.append({"season": r["season"], "game_pk": r["game_pk"],
                               "date": r["date"],
                               "player_id": player, "slot": slot, "prior_n": prior_n,
                               "lost_turn": int(r["lost_turn"]), "pa": r["pa"],
                               "hits_1plus": y, "risk": risk,
                               "p_exit_base": slot_prior[slot], "p_exit_challenger": risk,
                               "pa_base": baseline, "pa_challenger": adjusted,
                               "production_pa_base": production_base,
                               "production_pa_challenger": production_adjusted,
                               "p_hits_base": p0, "p_hits_challenger": p1,
                               "d_exit_brier": (risk - int(r["lost_turn"]))**2 -
                                               (slot_prior[slot] - int(r["lost_turn"]))**2,
                               "d_pa_abs": abs(adjusted-r["pa"]) - abs(baseline-r["pa"]),
                               "d_pa_sq": (adjusted-r["pa"])**2 - (baseline-r["pa"])**2,
                               "d_hits_brier": (p1-y)**2 - (p0-y)**2,
                               "d_production_pa_abs": abs(production_adjusted-r["pa"]) -
                                                      abs(production_base-r["pa"]),
                               "d_production_pa_sq": (production_adjusted-r["pa"])**2 -
                                                     (production_base-r["pa"])**2,
                               "d_hit_prob": p1-p0,
                               "cross_0_60": (p0 >= 0.60) != (p1 >= 0.60)})
        for r in by_date[day]:
            history[r["player_id"]].append(int(r["lost_turn"]))
            hit_history[r["player_id"]].append((r["hits"], r["pa"]))

    report = {"definitions": {"lookback_starts": LOOKBACK, "shrink_starts": SHRINK_STARTS,
                               "production_pa_source_sha256": production_source_sha256,
                               "hit_rate_prior_pa": 40, "league_hit_pa_2024": league_hit_pa,
                               "slot_exit_prior_2024": slot_prior,
                               "slot_loss_given_exit_2024": loss_given_exit,
                               "slot_total_pa_2024": slot_total_pa_2024,
                               "slot_starter_pa_2024": slot_starter_pa_2024,
                               "production_fallback_slot_total_pa_2026_fitted": production_pa_fallback}}
    for year in (2025, 2026):
        subset = [r for r in scored if r["season"] == year]
        item = {"n": len(subset), "games": len({r["game_pk"] for r in subset}),
                "players": len({r["player_id"] for r in subset}),
                "exit_rate": np.mean([r["lost_turn"] for r in subset]).item(),
                "mean_pa": np.mean([r["pa"] for r in subset]).item(),
                "prior_n_ge_5": sum(r["prior_n"] >= 5 for r in subset),
                "challenger_pa_change_abs_ge_0_1": sum(abs(r["pa_challenger"]-r["pa_base"]) >= 0.1 for r in subset),
                "abs_hit_prob_change_ge_0_005": sum(abs(r["d_hit_prob"]) >= 0.005 for r in subset),
                "cross_0_60": sum(r["cross_0_60"] for r in subset)}
        item["baseline"] = {
            "exit_brier": np.mean([(r["p_exit_base"]-r["lost_turn"])**2 for r in subset]).item(),
            "pa_mae": np.mean([abs(r["pa_base"]-r["pa"]) for r in subset]).item(),
            "pa_mse": np.mean([(r["pa_base"]-r["pa"])**2 for r in subset]).item(),
            "hits_brier": np.mean([(r["p_hits_base"]-r["hits_1plus"])**2 for r in subset]).item()}
        item["risk_bins"] = {}
        bins = (("under_5pct", 0.0, 0.05), ("5_to_10pct", 0.05, 0.10),
                ("10_to_20pct", 0.10, 0.20), ("20pct_plus", 0.20, 1.01))
        for name, low, high in bins:
            piece = [r for r in subset if low <= r["risk"] < high]
            item["risk_bins"][name] = {
                "n": len(piece),
                "mean_predicted_exit_risk": np.mean([r["risk"] for r in piece]).item() if piece else None,
                "actual_lost_turn_rate": np.mean([r["lost_turn"] for r in piece]).item() if piece else None,
                "mean_pa_adjustment": np.mean([r["pa_challenger"]-r["pa_base"] for r in piece]).item() if piece else None,
                "mean_hit_probability_adjustment": np.mean([r["d_hit_prob"] for r in piece]).item() if piece else None}
        item["monthly"] = {}
        for month in sorted({r["date"][:7] for r in subset}):
            piece = [r for r in subset if r["date"].startswith(month)]
            item["monthly"][month] = {
                "n": len(piece),
                "d_exit_brier": np.mean([r["d_exit_brier"] for r in piece]).item(),
                "d_pa_abs": np.mean([r["d_pa_abs"] for r in piece]).item(),
                "d_hits_brier": np.mean([r["d_hits_brier"] for r in piece]).item()}
        item["by_slot"] = {}
        for slot in range(1, 10):
            piece = [r for r in subset if r["slot"] == slot]
            item["by_slot"][slot] = {
                "n": len(piece),
                "d_exit_brier": np.mean([r["d_exit_brier"] for r in piece]).item(),
                "d_pa_abs": np.mean([r["d_pa_abs"] for r in piece]).item(),
                "d_hits_brier": np.mean([r["d_hits_brier"] for r in piece]).item()}
        for field in ("d_exit_brier", "d_pa_abs", "d_pa_sq", "d_hits_brier",
                      "d_production_pa_abs", "d_production_pa_sq"):
            item[field] = np.mean([r[field] for r in subset]).item()
            item[field + "_game_ci"] = bootstrap_ci(subset, field, "game_pk")
            item[field + "_player_ci"] = bootstrap_ci(subset, field, "player_id")
            item[field + "_two_way_ci"] = two_way_cluster_ci(subset, field)
        report[str(year)] = item
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("collect", "evaluate"))
    p.add_argument("--cache", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if args.mode == "collect":
        collect(args.cache, args.out)
    else:
        evaluate(load_rows(args.cache), args.out)


if __name__ == "__main__":
    main()
