#!/usr/bin/env python3
"""Build contract.status_record lines for F1/F5/F6/F7/F10 from team_context_report.json.

Declared criteria (written before the final report was read for these
decisions; see README):
* A factor config is credited only where it beats the DEV-fitted scale
  control (harness rule).
* Team factors (F1/F5/F6/F7) live inside the team-volume ratio consumer, so
  their own information is judged INCREMENTALLY against VOLUME_BASE (same
  consumer, no Tier 1 factor). The volume base's own gain is not credited to
  any factor.
* VALIDATED needs: vs scale control CI < 0 on HOLDOUT_2023_2025 (exploratory),
  incremental CI < 0 on HOLDOUT (team factors), point estimate not worse on
  FRESH_2026, AND the DEV in-sample CI < 0 (consistency). Even then:
  "historically supported, not prospectively validated".
* F1 cannot be VALIDATED: historical values are closing lines
  (CLOSING_LINE_PROXY_RETROSPECTIVE) that no pre-kickoff prediction had.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from nfl.research.tier1.contract import status_record  # noqa: E402

MARKETS = ("passing_yards", "receptions", "receiving_yards")


def part(res, which, p):
    return res.get(which, {}).get("partitions", {}).get(p, {})


def summary(r, config):
    out = {}
    for m in MARKETS:
        res = r["markets"][m]["configs"][config]
        row = {"params": res["params"], "uses_market_input": res["uses_market_input"]}
        for p in ("DEV_2016_2022", "HOLDOUT_2023_2025", "FRESH_2026"):
            k, b, v = part(res, "vs_scale_control", p), part(res, "vs_b0", p), part(res, "vs_volume_base", p)
            row[p] = {"n_matched": k.get("n_matched"), "activation": k.get("activation_share"),
                      "vs_b0": [b.get("paired_delta_mean"), b.get("paired_delta_ci95")],
                      "vs_scale_control": [k.get("paired_delta_mean"), k.get("paired_delta_ci95")],
                      "vs_volume_base": [v.get("paired_delta_mean"), v.get("paired_delta_ci95")] if v else None,
                      "bias": res["bias"].get(p, {}).get("mean_pred_minus_actual")}
        out[m] = row
    return out


def passes(s, m, incremental):
    d, h, f = s[m]["DEV_2016_2022"], s[m]["HOLDOUT_2023_2025"], s[m]["FRESH_2026"]
    ok = h["vs_scale_control"][1][1] < 0 and d["vs_scale_control"][1][1] < 0 and f["vs_scale_control"][0] <= 0
    if incremental:
        ok = ok and h["vs_volume_base"][1][1] < 0 and d["vs_volume_base"][1][1] < 0 and f["vs_volume_base"][0] <= 0
    return bool(ok)


def main():
    r = json.loads((HERE / "team_context_report.json").read_text())
    consumer = "nfl.research.tier1.team_context_challenger (config {})"
    records = []
    s1 = summary(r, "F1")
    records.append(status_record(
        "F1_GAME_CONTEXT", milestone="BUILT", consumer=consumer.format("F1 / ALL"),
        evidence=("Historical F1 = nflverse CLOSING spread/total -> CLOSING_LINE_PROXY_RETROSPECTIVE. "
                  "Incremental over VOLUME_BASE: passing_yards and receiving_yards HOLDOUT CI < 0, receptions null. "
                  "Not VALIDATED by doctrine: no pre-kickoff prediction had closing lines. uses_market_input=True: "
                  "never independent evidence against the same book's player prices. Live path: timestamped "
                  "FanDuel spread/total captured for all 16 week-3 games."),
        blockers=["No archive of timestamped historical pregame lines in these sources -> no point-in-time backtest"],
        activation={m: s1[m]["HOLDOUT_2023_2025"]["activation"] for m in MARKETS}, evaluation=s1))
    s5 = summary(r, "F5")
    records.append(status_record(
        "F5_PASS_TENDENCY_PACE", milestone="REJECTED", consumer=consumer.format("F5"),
        evidence=("PROE, neutral dropback rate, plays/game and neutral sec/play add nothing measurable over the "
                  "strictly-prior team-volume base at the player level: incremental HOLDOUT CI straddles 0 in all three "
                  "markets. They do sharpen the team dropback/pass-yard intermediate. The VOLUME_BASE ratio itself "
                  "(B0 x E[team volume]/historical team volume) beats the scale control on HOLDOUT in all three "
                  "markets -- credited to VOLUME_BASE, not to F5."),
        activation={m: s5[m]["HOLDOUT_2023_2025"]["activation"] for m in MARKETS}, evaluation=s5))
    s6 = summary(r, "F6")
    ok6 = {m: passes(s6, m, True) for m in MARKETS}
    records.append(status_record(
        "F6_ENVIRONMENT", milestone="VALIDATED" if any(ok6.values()) else "BUILT", consumer=consumer.format("F6"),
        evidence=("Historically supported, not prospectively validated, for markets "
                  f"{[m for m, v in ok6.items() if v]} (criteria in status_records.py). Driver is the GFS MOS forecast "
                  "(wind, 6-h PoP; 12Z run of the day before, runtime proven by IEM) -- a static roof/surface-only "
                  "DEV run gave a much smaller increment (team_context_dev_report_prelim_f6_static_only.json). "
                  "games.csv observed temp/wind never used. Open-Meteo historical/previous-runs/live APIs returned "
                  "'Daily API request limit exceeded' from this egress on 2026-09-24."),
        blockers=["Live week-3 Sunday/Monday outdoor forecasts: the pre-declared MOS run is not issued until "
                  "2026-09-26 17:00Z (Sun games) / 2026-09-27 17:00Z (MNF); rows carry NOT_YET_ISSUED",
                  "International venues have no MOS station -> UNKNOWN -> fallback"],
        activation={m: s6[m]["HOLDOUT_2023_2025"]["activation"] for m in MARKETS},
        evaluation={"passes_by_market": ok6, **s6}))
    s7 = summary(r, "F7")
    records.append(status_record(
        "F7_REST_TRAVEL", milestone="REJECTED", consumer=consumer.format("F7"),
        evidence=("Rest days, short week, post-bye, rest differential, travel km, time zones crossed, west-to-east "
                  "early kickoff and international flag: incremental over VOLUME_BASE is null on HOLDOUT in all three "
                  "markets (point estimates ~0, CIs straddle 0)."),
        activation={m: s7[m]["HOLDOUT_2023_2025"]["activation"] for m in MARKETS}, evaluation=s7))
    s10 = summary(r, "F10")
    ok10 = {m: passes(s10, m, False) for m in MARKETS}
    records.append(status_record(
        "F10_OPPONENT_POSITION", milestone="VALIDATED" if any(ok10.values()) else "BUILT",
        consumer=consumer.format("F10"),
        evidence=("Opponent-adjusted allowed-by-position index (WR/TE/RB receptions, yards; QB passing yards), "
                  "shrunk toward 1. NOT an individual WR-vs-CB matchup; no slot/wide split (no alignment data). "
                  f"Criteria pass by market: {ok10}. passing_yards HOLDOUT beats scale control with CI < 0 but the DEV "
                  "in-sample CI crosses 0, so it fails the consistency condition; receptions HOLDOUT CI < 0 but FRESH "
                  "point estimate is worse; receiving_yards null."),
        activation={m: s10[m]["HOLDOUT_2023_2025"]["activation"] for m in MARKETS},
        evaluation={"passes_by_market": ok10, **s10}))
    extra = {"VOLUME_BASE": summary(r, "VOLUME_BASE"), "ALL_NO_MARKET": summary(r, "ALL_NO_MARKET"),
             "ALL": summary(r, "ALL")}
    (HERE / "team_context_status.json").write_text(json.dumps({"factors": records, "consumer_configs": extra},
                                                              indent=2, sort_keys=True, default=str) + "\n")
    for rec in records:
        print(rec["factor_id"], rec["milestone"])
    print("F6 passes", ok6, "F10 passes", ok10)


if __name__ == "__main__":
    main()
