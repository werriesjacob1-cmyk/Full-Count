#!/usr/bin/env python3
"""FC-ACC-019 hypothetical equal-N ranking diagnostic (NOT an operational substitution: prices
are unauthenticated and decision-time identity is not certified). Per regular-season slate, target
N = the champion's actual settled Top Pick count; challengers take N from the R2 pool, one per
player, at most 2 per game. Champion arm = actual public picks and grades (uncapped, as published).
Added beyond the preregistered k=3 diagnostic; labelled as such."""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analysis as A  # noqa: E402


def main(repo, raw):
    rng = np.random.default_rng(A.SEED)
    R1, _ = A.load_r1(repo)
    R2, _ = A.load_r2(raw)
    pool = defaultdict(list)
    for r in R2:
        pool[r["slate"]].append(r)
    champ = defaultdict(list)
    for r in R1:
        if r["slate"] <= "2026-09-27":
            champ[r["slate"]].append(r)
    keys = {"market_only_no_vig": lambda r: -r["no_vig"], "model_prob": lambda r: -r["model"],
            "model_minus_breakeven": lambda r: -(r["model"] - r["raw_implied"])}
    ids2 = {(r["slate"], r["game_pk"], r["player_id"], r["stat"]) for r in R2}
    present = sum((r["slate"], r["game_pk"], r["player_id"], r["stat"]) in ids2 for d in champ for r in champ[d])
    out = {"slates": len(champ), "champion_picks": sum(len(v) for v in champ.values()),
           "champion_player_market_present_in_R2": present,
           "champion": A.segment_stats([r for v in champ.values() for r in v], rng, fit_control=False)}
    out["champion"]["market_expected_wins"] = float(sum(r["no_vig"] for v in champ.values() for r in v))
    for name, kf in keys.items():
        sel, short = [], 0
        for d, C in champ.items():
            n = len(C)
            pick, pl, gm = [], set(), Counter()
            for r in sorted(pool.get(d, []), key=lambda r: (kf(r), str(r["id"]))):
                if len(pick) == n:
                    break
                if r["player_id"] in pl or gm[r["game_pk"]] >= 2:
                    continue
                pick.append(r); pl.add(r["player_id"]); gm[r["game_pk"]] += 1
            short += n - len(pick)
            sel += pick
        st = A.segment_stats(sel, rng, fit_control=False)
        st.pop("disagreement_bands", None)
        st.update({"shortfall": short, "wins": int(sum(r["y"] for r in sel)),
                   "market_expected_wins": float(sum(r["no_vig"] for r in sel)),
                   "odds_quantiles": [float(x) for x in np.percentile([r["american"] for r in sel], [0, 10, 50, 90, 100])],
                   "share_priced_-300_or_shorter": float(np.mean([r["american"] <= -300 for r in sel]))})
        out[name] = st
    out["champion"].pop("disagreement_bands", None)
    (HERE / "out" / "equal_n_diagnostic.json").write_text(json.dumps(out, indent=1, default=float))
    return out


if __name__ == "__main__":
    o = main(sys.argv[1], sys.argv[2])
    print('slates', o['slates'], 'champion picks', o['champion_picks'], 'present', o['champion_player_market_present_in_R2'])
    for k in ("champion", "market_only_no_vig", "model_prob", "model_minus_breakeven"):
        v = o[k]
        print(k, 'n', v['n'], 'hit', round(v['hit_rate'], 3), 'mktExpWins', round(v.get('market_expected_wins', 0), 1),
              'wins', v.get('wins', round(v['hit_rate'] * v['n'])), 'roi', round(v['roi_unauthenticated'], 3), v['roi_ci_game'],
              'short', v.get('shortfall'), 'oddsq', v.get('odds_quantiles'), '<=-300', v.get('share_priced_-300_or_shorter'), v['top_stats'])
