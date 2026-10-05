#!/usr/bin/env python3
"""V3 prospective scorecard (reporting only; no new selection, no new grading, no verdict change).

North-Star question: at the same legitimate, usable operational pick volume, does V3 realize a higher MLB prop hit
rate than the champion?

The scorecard is computed ONLY from the `pairs` that evaluate_v3.evaluate_from_evidence assembles AFTER its one-look
lock (verified manifests + pinned-grader outputs). It reuses the frozen per-slate machinery unchanged:
  * evaluate_v3._rows       -- the sealed eligible PRIMARY universe with its pinned grades;
  * harness.select          -- champion = the shadow board's sealed top picks; V3 (C2_RESIDUAL) takes the SAME N_d
                               from the same sealed eligible rows by its frozen key (no outcome is used);
  * harness_v2.clustered_diff -- the paired cluster bootstrap (game / player / week).
Hit rate counts settled picks only (pinned grader: hit / miss). Unresolved picks (game not final, player absent from
the box score) are excluded from BOTH arms by settlement status, never by outcome, and are reported per arm.

Every output carries its evidence regime. 2026 postseason = DESCRIPTIVE SHADOW; 2027 regular season = CONFIRMATORY.
The two are never combined: one scorecard per regime, and only the CONFIRMATORY one can feed a promotion statistic.
Cumulative series are computed retrospectively at the single preregistered look -- they are not interim looks.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(HERE))
import harness as H  # noqa: E402
import harness_v2 as H2  # noqa: E402
import regimes as RG  # noqa: E402

SCORECARD_VERSION = "fc-v3-scorecard-1"
V3_ARM = H.PRIMARY                                    # C2_RESIDUAL: the preregistered V3 challenger arm
REGIME_LABEL = {"2026_POSTSEASON_SHADOW": "DESCRIPTIVE SHADOW", "2027_REGULAR_CONFIRMATORY": "CONFIRMATORY",
                "SMOKE_TEST_SYNTHETIC": "SYNTHETIC FIXTURE (NOT EVIDENCE)"}
ROUND = {"F": "WILD_CARD", "D": "DIVISION_SERIES", "L": "LEAGUE_CHAMPIONSHIP", "W": "WORLD_SERIES"}


class ScorecardError(ValueError):
    pass


def _rate(picks):
    s = [r for r in picks if r["y"] is not None]
    h = sum(r["y"] for r in s)
    return {"selected": len(picks), "settled": len(s), "unresolved": len(picks) - len(s), "hits": h,
            "hit_rate": (h / len(s)) if s else None}


def _phase(row, regime):
    if regime == "2026_POSTSEASON_SHADOW":
        return ROUND.get(row.get("game_type") or "", "POSTSEASON_UNKNOWN_ROUND")
    m = int(row["date"][5:7])
    return "EARLY (Mar-May)" if m <= 5 else "MID (Jun-Jul)" if m <= 7 else "LATE (Aug-Oct)"


def _arm_pair(champ, v3):
    c, v = _rate(champ), _rate(v3)
    delta = (v["hit_rate"] - c["hit_rate"]) * 100 if c["hit_rate"] is not None and v["hit_rate"] is not None else None
    return {"champion": c, "v3": v, "delta_pp": delta}


def _concentration(picks):
    s = [r for r in picks if r["y"] is not None] or picks
    if not s:
        return {}
    pl, gm = Counter(r["player_id"] for r in s), Counter((r["date"], r["game_pk"]) for r in s)
    n = len(s)
    return {"max_picks_one_player": max(pl.values()), "max_picks_one_game": max(gm.values()),
            "top5_player_share": sum(v for _, v in pl.most_common(5)) / n,
            "player_hhi": sum((v / n) ** 2 for v in pl.values())}


def collect(pairs, coef, regime, rows_fn=None):
    """Per-slate equal-volume selections, reusing the frozen functions. Returns (champion picks, V3 picks, audit)."""
    if rows_fn is None:
        import evaluate_v3 as EV
        rows_fn = EV._rows
    if regime not in RG.REGIMES:
        raise ScorecardError(f"unknown regime {regime!r}")
    RG.check_regime(regime, [m for m, _ in pairs])                     # one regime per scorecard, enforced
    champ, v3, audit = [], [], []
    for m, g in pairs:
        rows = rows_fn(m, g, coef)
        for r in rows:
            r["game_type"] = next((x.get("game_type") for x in m["rows"] if x["candidate_id"] == r["id"]), None)
        n, sel = H.select(rows)
        c, v = sel["CHAMPION"], sel[V3_ARM]
        if len(c) != n or len(v) != min(n, len(rows)):
            raise ScorecardError(f"{m['date']}/{m['window']}: equal-volume violated (N_d={n}, champion={len(c)}, v3={len(v)})")
        ids = {r["id"] for r in rows}
        if not ({r["id"] for r in c} | {r["id"] for r in v}) <= ids:
            raise ScorecardError("a selection left the sealed eligible universe")
        champ += c
        v3 += v
        audit.append({"unit": f"{m['date']}/{m['window']}", "manifest_sha256": m["manifest_sha256"], "n_d": n,
                      "eligible_primary": len(rows), "champion": len(c), "v3": len(v),
                      "champion_ids": sorted(r["id"] for r in c), "v3_ids": sorted(r["id"] for r in v)})
    return champ, v3, audit


def build_scorecard(pairs, coef, regime, rows_fn=None):
    champ, v3, audit = collect(pairs, coef, regime, rows_fn)
    spec = RG.REGIMES[regime]
    ci, vi = {r["id"] for r in champ}, {r["id"] for r in v3}
    overall = _arm_pair(champ, v3)
    overall["paired_ci95_pp"] = {}
    for unit in ("game", "player", "week"):
        d = H2.clustered_diff(champ, v3, unit)
        overall["paired_ci95_pp"][unit] = {"n_clusters": d["n_clusters"],
                                           "ci95": [x * 100 for x in d["ci95"]] if d["ci95"] else None,
                                           "one_sided_lower95": d["one_sided_lower95"] * 100
                                           if d["one_sided_lower95"] is not None else None}
    total = len(champ)
    mech = {"overlap": len(ci & vi), "overlap_rate": (len(ci & vi) / total) if total else None,
            "champion_only": _rate([r for r in champ if r["id"] not in vi]),
            "v3_only": _rate([r for r in v3 if r["id"] not in ci])}

    def by(keyf):
        groups = defaultdict(lambda: ([], []))
        for r in champ:
            groups[keyf(r)][0].append(r)
        for r in v3:
            groups[keyf(r)][1].append(r)
        return {k: _arm_pair(*groups[k]) for k in sorted(groups, key=str)}
    breakdowns = {"market_family": by(lambda r: r["family"]), "date": by(lambda r: r["date"]),
                  "iso_week": by(lambda r: r["week"]), "month": by(lambda r: r["date"][:7]),
                  "season_phase": by(lambda r: _phase(r, regime))}
    cum, rc, rv = [], [], []
    for d in sorted({r["date"] for r in champ + v3}):
        rc += [r for r in champ if r["date"] == d]
        rv += [r for r in v3 if r["date"] == d]
        p = _arm_pair(rc, rv)
        cum.append({"through": d, "champion_selected": p["champion"]["selected"], "champion_hit_rate": p["champion"]["hit_rate"],
                    "v3_selected": p["v3"]["selected"], "v3_hit_rate": p["v3"]["hit_rate"], "delta_pp": p["delta_pp"]})
    return {"scorecard_version": SCORECARD_VERSION, "regime": regime, "evidence_label": REGIME_LABEL.get(regime, regime),
            "confirmatory": spec["confirmatory"],
            "promotion_statistic": ("ELIGIBLE (confirmatory regime; verdict per prereg v3 s13)" if spec["confirmatory"]
                                    else "NOT A PROMOTION STATISTIC (descriptive regime)"),
            "north_star": "at equal legitimate operational volume, V3 hit rate vs champion hit rate",
            "arms": {"champion": "shadow champion (sealed top picks)", "v3": V3_ARM},
            "n_units": len(audit), "total_legitimate_prospective_n": total, "overall": overall,
            "selection_mechanics": mech, "breakdowns": breakdowns,
            "concentration": {"champion": _concentration(champ), "v3": _concentration(v3)},
            "cumulative": cum, "equal_volume_audit": audit,
            "look": "single preregistered look; cumulative series are retrospective, not interim looks"}


def _pct(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


def render_markdown(sc):
    o, m = sc["overall"], sc["selection_mechanics"]
    lines = [f"# V3 prospective scorecard — {sc['regime']} — **{sc['evidence_label']}**", "",
             f"*{sc['promotion_statistic']}.* {sc['look']}. Units: {sc['n_units']}; "
             f"total legitimate prospective N (champion selections): {sc['total_legitimate_prospective_n']}.", "",
             "| arm | selected | settled | hits | hit rate |", "|---|---|---|---|---|"]
    for arm in ("champion", "v3"):
        a = o[arm]
        lines.append(f"| {arm} | {a['selected']} | {a['settled']} | {a['hits']} | {_pct(a['hit_rate'])} |")
    d = o["delta_pp"]
    lines += ["", f"**Delta (V3 − champion): {'n/a' if d is None else f'{d:+.1f} pp'}**", ""]
    for u, ci in o["paired_ci95_pp"].items():
        lines.append(f"- paired 95% CI by {u} ({ci['n_clusters']} clusters): "
                     + ("n/a" if not ci["ci95"] else f"[{ci['ci95'][0]:+.1f}, {ci['ci95'][1]:+.1f}] pp"))
    lines += ["", f"Overlap {m['overlap']} ({_pct(m['overlap_rate'])}); champion-only {m['champion_only']['selected']} "
                  f"@ {_pct(m['champion_only']['hit_rate'])}; V3-only {m['v3_only']['selected']} @ {_pct(m['v3_only']['hit_rate'])}.", ""]
    for name in ("market_family", "season_phase", "month"):
        lines += [f"## By {name.replace('_', ' ')}", "", "| key | champ N | champ HR | V3 N | V3 HR | Δ pp |", "|---|---|---|---|---|---|"]
        for k, p in sc["breakdowns"][name].items():
            dd = p["delta_pp"]
            lines.append(f"| {k} | {p['champion']['selected']} | {_pct(p['champion']['hit_rate'])} | {p['v3']['selected']} | "
                         f"{_pct(p['v3']['hit_rate'])} | {'n/a' if dd is None else f'{dd:+.1f}'} |")
        lines.append("")
    lines += ["## Cumulative (retrospective)", "", "| through | champ N | champ HR | V3 N | V3 HR | Δ pp |", "|---|---|---|---|---|---|"]
    for c in sc["cumulative"]:
        dd = c["delta_pp"]
        lines.append(f"| {c['through']} | {c['champion_selected']} | {_pct(c['champion_hit_rate'])} | {c['v3_selected']} | "
                     f"{_pct(c['v3_hit_rate'])} | {'n/a' if dd is None else f'{dd:+.1f}'} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(json.dumps({"scorecard_version": SCORECARD_VERSION, "regimes": REGIME_LABEL}, indent=1))
