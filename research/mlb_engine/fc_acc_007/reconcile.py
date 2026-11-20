"""Read-only reconciliation of deployment-proven MLB Top Picks.

Run from the repository root with ``python research/mlb_engine/fc_acc_007/reconcile.py``.
Only registry entries define public exposure. Grade files supply settlement;
the optional board freezes are used solely to measure replay coverage.
"""

from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import math
import pathlib
import statistics
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[3]
AUDIT_BASE_SHA = "53810a27c4ac5439e0153e38589c69959593896a"


def utc(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def raw_break_even(american):
    if not isinstance(american, int) or american == 0:
        raise ValueError(f"missing/malformed American odds: {american!r}")
    return 100 / (100 + american) if american > 0 else -american / (100 - american)


def net_return(american, won):
    if not won:
        return -1.0
    return american / 100 if american > 0 else 100 / -american


def cluster_ci(rows, field, clusters):
    """Two-way game/player sandwich interval, or one-way slate interval.

    This is a descriptive large-sample interval, not a correction for
    hypothesis selection or the small number of independent slate dates.
    """
    n = len(rows)
    if n < 2:
        return None
    mean = statistics.mean(row[field] for row in rows)

    def component(keys):
        sums = collections.defaultdict(float)
        for row in rows:
            sums[tuple(row[key] for key in keys)] += row[field] - mean
        return sum(total * total for total in sums.values()) / n**2

    if clusters == "slate":
        n_slates = len({row["slate"] for row in rows})
        if n_slates < 2:
            return None
        variance = component(("slate",)) * n_slates / (n_slates - 1)
    elif clusters == "game_player":
        variance = (component(("game",)) + component(("player",))
                    - component(("game", "player")))
    else:
        raise ValueError(clusters)
    se = math.sqrt(max(variance, 0))
    return [round(mean - 1.96 * se, 6), round(mean + 1.96 * se, 6)]


def summary(rows):
    settled = [row for row in rows if row["grade"] in ("hit", "miss")]
    n = len(settled)
    out = {"published": len(rows), "settled": n,
           "wins": sum(row["grade"] == "hit" for row in settled),
           "losses": sum(row["grade"] == "miss" for row in settled),
           "voids": sum(row["grade"] == "void" for row in rows),
           "ungraded": sum(row["grade"] == "ungraded" for row in rows)}
    if n:
        for field, key in (("win", "hit_rate"), ("p", "mean_model_probability"),
                           ("raw_be", "mean_raw_break_even"),
                           ("roi", "flat_stake_roi"),
                           ("model_gap", "model_minus_realized"),
                           ("market_gap", "raw_break_even_minus_realized"),
                           ("brier_delta", "model_minus_raw_price_brier")):
            out[key] = round(statistics.mean(row[field] for row in settled), 6)
        for field in ("model_gap", "roi", "brier_delta"):
            out[field + "_ci_game_player"] = cluster_ci(settled, field, "game_player")
            out[field + "_ci_slate"] = cluster_ci(settled, field, "slate")
        out["games"] = len({row["game"] for row in settled})
        out["players"] = len({row["player"] for row in settled})
        out["slates"] = len({row["slate"] for row in settled})
    return out


def grouped(rows, key):
    groups = collections.defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return {str(name): summary(group) for name, group in sorted(groups.items())}


def main():
    tracked_evidence_unchanged = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--quiet", AUDIT_BASE_SHA, "HEAD", "--",
         "data/public_top_picks/registry.json", "data/prediction_ledger/events.jsonl", "results"],
        check=False,
    ).returncode
    if tracked_evidence_unchanged != 0:
        raise ValueError("tracked public evidence differs from pinned audit base")
    registry = json.loads((ROOT / "data/public_top_picks/registry.json").read_text(encoding="utf-8"))
    entries = registry["entries"]
    grades = {}
    grade_files = sorted((ROOT / "results").glob("grades_*.json"))
    for path in grade_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("public_top_picks", []):
            key = row["id"]
            if key in grades:
                raise ValueError(f"duplicate public grade identity: {key}")
            if payload["date"] != entries.get(key, {}).get("slate_date"):
                raise ValueError(f"grade slate mismatch: {key}")
            grades[key] = row
    if set(grades) != set(entries):
        raise ValueError(f"registry/grade coverage mismatch: "
                         f"missing={len(set(entries)-set(grades))}, "
                         f"extra={len(set(grades)-set(entries))}")

    rows = []
    mismatch = []
    seen_settlement_identities = set()
    for key, entry in entries.items():
        snap, grade = entry["snapshot"], grades[key]
        if key != entry["canonical_id"] or key != snap["id"]:
            raise ValueError(f"publication identity mismatch: {key}")
        settlement_identity = json.dumps(entry["settlement_identity"], sort_keys=True)
        if settlement_identity in seen_settlement_identities:
            raise ValueError(f"duplicate settlement identity: {key}")
        seen_settlement_identities.add(settlement_identity)
        if not entry.get("publication_provenance", {}).get("artifact_id"):
            raise ValueError(f"missing publication artifact identity: {key}")
        for field in ("game_pk", "player_id", "stat", "market_side", "projection",
                      "hit_probability", "market_odds", "prop", "game_start"):
            if snap.get(field) != grade.get(field):
                mismatch.append([key, field])
        if grade.get("published_top_pick_at") != entry["first_published_at"]:
            mismatch.append([key, "published_top_pick_at"])
        state = grade.get("settlement_state")
        if state != grade.get("grade") or state not in ("hit", "miss", "void", "ungraded"):
            raise ValueError(f"unsupported or inconsistent settlement: {key}")
        price = snap["market_odds"]
        be = raw_break_even(price)
        observed = int(state == "hit")
        market_value = snap.get("market_implied")
        odds_time = (snap.get("versions") or {}).get("odds_fetched_at")
        age = ((utc(entry["first_published_at"]) - utc(odds_time)).total_seconds() / 60
               if odds_time else None)
        rows.append({
            "id": key, "slate": entry["slate_date"],
            "game": str(snap["game_pk"]), "player": str(snap["player_id"]),
            "market": snap["stat"], "side": snap["market_side"], "grade": state,
            "price": price, "p": snap["hit_probability"], "raw_be": be,
            "win": observed, "roi": net_return(price, observed),
            "model_gap": snap["hit_probability"] - observed,
            "market_gap": be - observed,
            "brier_delta": ((snap["hit_probability"] - observed)**2
                            - (be - observed)**2),
            "edge_raw": snap["hit_probability"] - be,
            "market_implied_mode": (
                "raw_equal" if market_value is not None and abs(be - market_value) < 0.00015
                else "below_raw" if market_value is not None and market_value < be
                else "other_or_missing"),
            "odds_age_min": age,
            "published_after_start": utc(entry["first_published_at"]) >= utc(snap["game_start"]),
            "grade_authority": grade.get("settlement_authority"),
            "grade_source": grade.get("settlement_source"),
            "reason": grade.get("reason"),
        })

    if mismatch:
        raise ValueError(f"published snapshot/grade mutation: {mismatch[:10]}")
    ledger_path = ROOT / "data/prediction_ledger/events.jsonl"
    ledger = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line]
    ledger_ids = set()
    prev = None
    ledger_mismatch = []
    for index, event in enumerate(ledger):
        canonical = json.dumps({field: event[field] for field in
                                ("prev_hash", "prop_id", "event_type", "payload", "recorded_at")},
                               sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if (event["event_seq"] != index or event["prev_hash"] != prev
                or event["event_hash"] != digest or event["prop_id"] in ledger_ids
                or event["event_type"] != "publication"):
            raise ValueError(f"publication ledger integrity failure at event {index}")
        ledger_ids.add(event["prop_id"])
        if event["prop_id"] in entries and event["payload"] != entries[event["prop_id"]]:
            ledger_mismatch.append(event["prop_id"])
        prev = digest

    freeze_files = sorted((ROOT / "output").glob("board_freeze_20??-??-??.json"))
    frozen = {}
    for path in freeze_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        frozen[payload["date"]] = {record["candidate_id"] for record in payload["records"]}
    same_day = [row for row in rows if row["slate"] in frozen]
    freeze_matched = sum(row["id"] in frozen[row["slate"]] for row in same_day)
    settled = [row for row in rows if row["grade"] in ("hit", "miss")]
    by_game = collections.defaultdict(list)
    by_player_game = collections.defaultdict(list)
    for row in settled:
        by_game[(row["slate"], row["game"])].append(row)
        by_player_game[(row["slate"], row["game"], row["player"])].append(row)

    def odds_band(row):
        price = row["price"]
        if price <= -250: return "<=-250"
        if price <= -200: return "-249..-200"
        if price <= -150: return "-199..-150"
        if price <= -110: return "-149..-110"
        if price <= 100: return "-109..+100"
        return ">+100"

    def prob_band(row):
        p = row["p"]
        return "0.60-0.649" if p < .65 else "0.65-0.699" if p < .70 else "0.70-0.749" if p < .75 else ">=0.75"

    def edge_band(row):
        edge = row["edge_raw"]
        return "<0.05" if edge < .05 else "0.05-0.099" if edge < .10 else "0.10-0.149" if edge < .15 else ">=0.15"

    output = {
        "audit_base_sha": AUDIT_BASE_SHA,
        "registry_updated_at": registry["updated_at"],
        "grade_files": len(grade_files),
        "population": summary(rows),
        "date_range": [min(row["slate"] for row in rows), max(row["slate"] for row in rows)],
        "published_slates": len({row["slate"] for row in rows}),
        "through_oct_11": summary([row for row in rows if row["slate"] <= "2026-10-11"]),
        "by_market": grouped(rows, lambda row: row["market"]),
        "by_side": grouped(rows, lambda row: row["side"]),
        "by_month": grouped(rows, lambda row: row["slate"][:7]),
        "by_odds_band": grouped(rows, odds_band),
        "by_probability_band": grouped(rows, prob_band),
        "by_raw_edge_band": grouped(rows, edge_band),
        "portfolio": {
            "settled_game_count": len(by_game),
            "games_with_multiple_picks": sum(len(group) > 1 for group in by_game.values()),
            "picks_in_multi_pick_games": sum(len(group) for group in by_game.values() if len(group) > 1),
            "max_picks_in_one_game": max(map(len, by_game.values())),
            "same_player_game_multi_market_pairs": sum(len(group) > 1 for group in by_player_game.values()),
            "picks_in_same_player_game_pairs": sum(len(group) for group in by_player_game.values() if len(group) > 1),
        },
        "reconciliation": {
            "registry_entries": len(entries), "grade_rows": len(grades),
            "snapshot_grade_mismatches": len(mismatch),
            "publication_ledger_events": len(ledger),
            "publication_ledger_missing": len(set(entries) - ledger_ids),
            "publication_ledger_extra": len(ledger_ids - set(entries)),
            "publication_ledger_payload_mismatches": len(ledger_mismatch),
            "market_implied_semantics": dict(collections.Counter(
                row["market"] + ":" + row["market_implied_mode"] for row in rows)),
            "post_start_publications": sum(row["published_after_start"] for row in rows),
            "odds_observed_after_publication": sum(row["odds_age_min"] is not None and row["odds_age_min"] < 0 for row in rows),
            "odds_age_over_30_min": sum(row["odds_age_min"] is not None and row["odds_age_min"] > 30 for row in rows),
            "odds_age_over_60_min": sum(row["odds_age_min"] is not None and row["odds_age_min"] > 60 for row in rows),
            "max_odds_age_min": round(max(row["odds_age_min"] for row in rows if row["odds_age_min"] is not None), 3),
            "settlement_authority": dict(collections.Counter(row["grade_authority"] for row in rows)),
            "settlement_source": dict(collections.Counter(row["grade_source"] for row in rows)),
            "ungraded_reasons": dict(collections.Counter(row["reason"] for row in rows if row["grade"] == "ungraded")),
            "void_reasons": dict(collections.Counter(row["reason"] for row in rows if row["grade"] == "void")),
            "freeze_dates": len(frozen), "published_on_freeze_dates": len(same_day),
            "published_id_in_same_day_freeze": freeze_matched,
            "published_outside_freeze_dates": len(rows) - len(same_day),
        },
    }
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
