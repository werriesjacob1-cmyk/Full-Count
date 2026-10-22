#!/usr/bin/env python3
"""FC-ACC-016 prediction-only diagnostic: how often can each frozen challenger reach
the champion's Top Pick probability floor (0.60)?

Applies the frozen FC-ACC-015 artifact (no fitting) to the reproduced FC-ACC-014
feature rows. NEVER reads outcomes: the `y` field is deleted before scoring, so this
cannot inform any outcome-dependent choice. It answers a structural question only:
whether the preregistered primary comparison can have any discordant selections.

Usage: python3 floor_reach.py <dir with features_tb.jsonl and features_hits.jsonl>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import checker as C  # noqa: E402

FLOOR = 0.60


def main(feat_dir):
    feat_dir = Path(feat_dir)
    models = C.load_frozen_models()
    out = {"floor": FLOOR, "outcomes_read": False, "markets": {}}
    for mk, fn in (("total_bases", "features_tb.jsonl"), ("hits", "features_hits.jsonl")):
        ch, p0, season = [], [], []
        with open(feat_dir / fn) as fh:
            for line in fh:
                r = json.loads(line)
                r.pop("y", None)
                p, status = C.challenger_probability(models[mk], r["f"])
                ch.append(np.nan if p is None else p)
                p0.append(r["p0"])
                season.append(r["season"])
        ch, p0, season = np.array(ch), np.array(p0), np.array(season)
        res = {}
        for s in sorted(set(season.tolist())):
            m = season == s
            q, q0 = ch[m][np.isfinite(ch[m])], p0[m]
            res[str(s)] = {"rows": int(m.sum()), "challenger_scored": int(len(q)),
                           "challenger_quantiles_50_95_99_max": [round(float(x), 4) for x in np.quantile(q, [.5, .95, .99, 1])],
                           "challenger_share_ge_floor": float((q >= FLOOR).mean()),
                           "challenger_rows_ge_floor": int((q >= FLOOR).sum()),
                           "production_raw_max": round(float(q0.max()), 4),
                           "production_raw_share_ge_floor": float((q0 >= FLOOR).mean())}
        out["markets"][mk] = res
    return out


if __name__ == "__main__":
    res = main(sys.argv[1])
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "floor_reach.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))
