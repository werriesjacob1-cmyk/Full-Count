#!/usr/bin/env python3
"""FC-MLB-004: pitcher contact-suppression refinement for Hits 1+ (DEVELOPMENT evidence).

Criteria: engineering/ops/TASKS/FC-MLB-004.md on claude/full-count-ops-state (frozen 3f627ad8...).
Reuses FC-MLB-002's repaired machinery (population, settlement, switch-hitter side, attach/asof, champion
functions, market identity) by IMPORT -- nothing in fc_mlb_002/ is modified.

  P0 = repaired CH1a exactly as frozen (FC-MLB-002 FROZEN_FIT_2025.json), fidelity-gated against b52ac418ba
  P1 = Lb + Lpx + same_hand     Lpx = shrunk logit of pitcher expected hits (xBA) per BF vs batter hand
  P2 = P1 + Dteam               Dteam = fielding team's shrunk (actual - expected) hit conversion on balls in play

  python3 fc_mlb_004.py contact    verified raw -> per-PA contact table (xBA) + provenance
  python3 fc_mlb_004.py fit        2025 only; writes out/FROZEN_FIT_004.json (commit BEFORE evaluate)
  python3 fc_mlb_004.py selfcheck  2025 in-sample mechanics check (not evidence)
  python3 fc_mlb_004.py evaluate   ONE 2026 evaluation
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
F002_DIR = os.path.join(os.path.dirname(HERE), "fc_mlb_002")
sys.path.insert(0, F002_DIR)
import fc_mlb_002 as F  # noqa: E402

DATA = F.DATA
OUT = os.path.join(HERE, "out")
F002_FIT = os.path.join(F002_DIR, "out", "FROZEN_FIT_2025.json")
F002_FIT_SHA = "6c0ecde23577659c1e72e18448415208eb4f5d7d2748f24b9631cd01ded7d6aa"
F002_PRED = os.path.join(F002_DIR, "out", "predictions_2026.csv.gz")
F002_PRED_SHA = "50c1d98b137e95799d0170af9c392026715cd5567a12b8f92dcc21ef8a18521c"  # b52ac418ba predictions_2026.csv.gz
KPX_GRID = [50, 150, 400, 1000]
KD_GRID = [500, 1500, 4000]
P1_COLS = ["Lb", "Lpx", "same_hand"]
P2_COLS = ["Lb", "Lpx", "same_hand", "Dteam"]
CONTACT = "contact.parquet"


# ------------------------------------------------------------------ contact table (from verified raw bytes)
def stage_contact():
    verified = F.verify_raw_files(os.path.join(DATA, "statcast"))
    cols = {"game_pk", "at_bat_number", "pitch_number", "game_type", "type", "estimated_ba_using_speedangle"}
    parts = []
    for raw in verified.values():
        d = pd.read_csv(io.BytesIO(raw), usecols=lambda c: c in cols, low_memory=False)
        if len(d):
            parts.append(d[d["game_type"] == "R"])
    p = pd.concat(parts, ignore_index=True)
    last = p.sort_values(["game_pk", "at_bat_number", "pitch_number"]).groupby(
        ["game_pk", "at_bat_number"], as_index=False).tail(1)
    c = last[["game_pk", "at_bat_number", "type", "estimated_ba_using_speedangle"]].rename(
        columns={"estimated_ba_using_speedangle": "xba"})
    c.to_parquet(os.path.join(DATA, CONTACT))
    prov = {"raw_files_verified": len(verified), "manifest_sha256": F.sha256_file(F.COMMITTED_MANIFEST),
            "contact_parquet_sha256": F.sha256_file(os.path.join(DATA, CONTACT)), "rows": int(len(c))}
    json.dump(prov, open(os.path.join(DATA, "CONTACT_PROVENANCE.json"), "w"), indent=1)
    print(json.dumps(prov))


def expected_hits(pa, contact):
    """Per-PA expected hits: xBA on balls in play; realized hit when a ball in play lacks xBA (past PAs only);
    0 for PAs without contact (K, BB, HBP)."""
    m = pa.merge(contact, on=["game_pk", "at_bat_number"], how="left")
    bip = (m["type"] == "X").astype(int)
    xh = np.where(bip == 1, np.where(m["xba"].notna(), m["xba"], m["hit"]), 0.0)
    return m.assign(bip=bip, xh=xh.astype(float), h_bip=m["hit"] * bip, xh_bip=xh * bip)


def load_contact():
    prov = json.load(open(os.path.join(DATA, "CONTACT_PROVENANCE.json")))
    if F.sha256_file(os.path.join(DATA, CONTACT)) != prov["contact_parquet_sha256"] or \
            prov["manifest_sha256"] != F.sha256_file(F.COMMITTED_MANIFEST):
        raise F.ManifestError("contact table is not the one built from verified raw bytes")
    return pd.read_parquet(os.path.join(DATA, CONTACT)), prov


def tables4(pa4):
    T = {}
    T["px"] = F.through(pa4, ["pitcher", "stand"], ["one", "xh", "hit"])
    T["rx"] = F.through(pa4[pa4["vs_starter"] == 0], ["fld_team", "stand"], ["one", "xh"])
    T["d"] = F.through(pa4, ["fld_team"], ["bip", "h_bip", "xh_bip"])
    T["l4"] = F.through(pa4.assign(k=0), ["k"], ["one", "xh", "bip", "h_bip", "xh_bip"])
    for t in T.values():                      # an empty table would otherwise carry a different datetime unit
        t["game_date"] = t["game_date"].astype("datetime64[us]")
    return T


def attach4(r, T4, pitcher_col):
    r = r.copy()
    r["k"] = 0
    r = r.merge(F.asof(T4["l4"], ["k"], ["one", "xh", "bip", "h_bip", "xh_bip"], r, "c4_"),
                on=["season", "k", "game_date"], how="left")
    pk = r[["season", pitcher_col, "stand", "game_date"]].rename(columns={pitcher_col: "pitcher"})
    pv = F.asof(T4["px"], ["pitcher", "stand"], ["one", "xh", "hit"], pk, "px_").rename(columns={"pitcher": pitcher_col})
    r = r.merge(pv, on=["season", pitcher_col, "stand", "game_date"], how="left")
    r = r.merge(F.asof(T4["rx"], ["fld_team", "stand"], ["one", "xh"], r, "rx_"),
                on=["season", "fld_team", "stand", "game_date"], how="left")
    r = r.merge(F.asof(T4["d"], ["fld_team"], ["bip", "h_bip", "xh_bip"], r, "d_"),
                on=["season", "fld_team", "game_date"], how="left")
    return r


def lpx(r, kpx, src="px_"):
    lam = r["c4_xh"] / r["c4_one"]
    return F.logit((r[f"{src}xh"] + kpx * lam) / (r[f"{src}one"] + kpx))


def dteam(r, kd):
    lb, lx = r["c4_h_bip"] / r["c4_bip"], r["c4_xh_bip"] / r["c4_bip"]
    return F.logit((r["d_h_bip"] + kd * lb) / (r["d_bip"] + kd)) - F.logit((r["d_xh_bip"] + kd * lx) / (r["d_bip"] + kd))


def fit_cols(D, y, cols, A=None):
    m = F.fit_lr(D[cols].to_numpy()[A] if A is not None else D[cols].to_numpy(), y[A] if A is not None else y)
    return m


def coef_dict(m, cols):
    return {"intercept": float(m.intercept_[0]), **{c: float(v) for c, v in zip(cols, m.coef_[0])}}


# ------------------------------------------------------------------ fit (2025 only)
def stage_fit():
    f2 = json.load(open(F002_FIT))
    assert F.sha256_file(F002_FIT) == F002_FIT_SHA
    F.verified_provenance()
    contact, _ = load_contact()
    pa, pit = F.load()
    T, _ = F.tables(pa, pit)
    pa4 = expected_hits(pa, contact)
    T4 = tables4(pa4)
    tr = pa4[(pa4["season"] == 2025) & (pa4["game_date"] >= F.START[2025])].copy()
    tr = F.attach(tr, T)
    tr = attach4(tr, T4, "pitcher")
    tr = tr[tr["b_one"] >= F.MIN_PA].reset_index(drop=True)
    y = tr["hit"].to_numpy()
    A = (tr["game_date"] <= "2025-07-15").to_numpy()
    base = F.design(tr, f2["k"])                      # Lb (kb=200), Lp (kp=150), same_hand -- FC-MLB-002 frozen k
    grid1 = []
    for kpx in KPX_GRID:
        D = base.assign(Lpx=np.asarray(lpx(tr, kpx)))
        m = fit_cols(D, y, P1_COLS, A)
        grid1.append((F.mean_ll(m.predict_proba(D[P1_COLS].to_numpy()[~A])[:, 1], y[~A]), kpx))
    kpx = max(grid1)[1]
    D = base.assign(Lpx=np.asarray(lpx(tr, kpx)))
    grid2 = []
    for kd in KD_GRID:
        D2 = D.assign(Dteam=np.asarray(dteam(tr, kd)))
        m = fit_cols(D2, y, P2_COLS, A)
        grid2.append((F.mean_ll(m.predict_proba(D2[P2_COLS].to_numpy()[~A])[:, 1], y[~A]), kd))
    kd = max(grid2)[1]
    D = D.assign(Dteam=np.asarray(dteam(tr, kd)), resid=lambda x: x["Lp"] - x["Lpx"])
    hold = {}
    for name, cols in (("P0_CH1a_refit_reference", ["Lb", "Lp", "same_hand"]), ("P1", P1_COLS), ("P2", P2_COLS),
                       ("diag_P1_plus_pitcher_residual", ["Lb", "Lpx", "resid", "same_hand"])):
        mA = fit_cols(D, y, cols, A)
        hold[name] = {"holdout_ll_jul16_end": F.mean_ll(mA.predict_proba(D[cols].to_numpy()[~A])[:, 1], y[~A]),
                      "coef_first_half": coef_dict(mA, cols)}
    frozen = {"label": "FC-MLB-004 frozen 2025 fit", "kb_kp_from_fc_mlb_002": f2["k"], "kpx": kpx, "kd": kd,
              "grid_kpx": grid1, "grid_kd": grid2, "n_pa_2025": int(len(tr)),
              "coef": {"P1": coef_dict(fit_cols(D, y, P1_COLS), P1_COLS), "P2": coef_dict(fit_cols(D, y, P2_COLS), P2_COLS)},
              "holdout_2025": hold,
              "p2_valid_point_in_time": bool(np.isfinite(D["Dteam"]).all())}
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "FROZEN_FIT_004.json")
    json.dump(frozen, open(path, "w"), indent=1)
    print(json.dumps({k_: frozen[k_] for k_ in ("kpx", "kd", "coef", "holdout_2025")}, indent=1), F.sha256_file(path))


# ------------------------------------------------------------------ evaluation
def p_game(p_pa, proj_pa, lg_h1, pp):
    modelled = [pp.p_at_least_hits(1, {0: 1 - p, 1: p}, n) for p, n in zip(p_pa, proj_pa)]
    return 0.5 * lg_h1 + 0.5 * np.array(modelled)


def fidelity_gate(ev, path=F002_PRED):
    """P0 (CH1a) and CH0 recomputed here must equal FC-MLB-002's committed b52ac418ba predictions row for row."""
    assert F.sha256_file(path) == F002_PRED_SHA, "FC-MLB-002 predictions are not the b52ac418ba file"
    ref = pd.read_csv(path)
    j = ev[["game_pk", "batter", "CH0", "P0"]].merge(ref[["game_pk", "batter", "CH0", "CH1a"]], on=["game_pk", "batter"],
                                                     how="outer", suffixes=("", "_ref"), indicator=True)
    if (j["_merge"] != "both").any():
        raise SystemExit(f"FIDELITY GATE: population differs from FC-MLB-002 ({(j['_merge'] != 'both').sum()} rows)")
    d0 = float(np.abs(j["CH0"] - j["CH0_ref"]).max()); d1 = float(np.abs(j["P0"] - j["CH1a"]).max())
    if d0 > 1e-9 or d1 > 1e-9:
        raise SystemExit(f"FIDELITY GATE: max |dCH0|={d0}, |dP0-CH1a|={d1}")
    return {"rows": int(len(j)), "max_abs_diff_CH0": d0, "max_abs_diff_P0_vs_CH1a": d1}


def stage_evaluate(season=2026, out_name="RESULTS.json", market=True):
    out_json = os.path.join(OUT, out_name)
    if os.path.exists(out_json) and out_name == "RESULTS.json":
        raise SystemExit("RESULTS.json exists: the frozen evaluation runs ONCE")
    f2 = json.load(open(F002_FIT)); assert F.sha256_file(F002_FIT) == F002_FIT_SHA
    fz_path = os.path.join(OUT, "FROZEN_FIT_004.json"); fz = json.load(open(fz_path))
    k2, kpx, kd = f2["k"], fz["kpx"], fz["kd"]
    sys.path.insert(0, F.CHAMP)
    import prop_probability as pp          # noqa: E402
    import generate_picks as gp            # noqa: E402
    champ_head = subprocess.check_output(["git", "-C", F.CHAMP, "rev-parse", "HEAD"]).decode().strip()
    assert champ_head == F.CHAMP_SHA, champ_head
    prov = F.verified_provenance()
    contact, cprov = load_contact()
    pa, pit = F.load()
    T, bg = F.tables(pa, pit)
    T4 = tables4(expected_hits(pa, contact))
    # ---- population: identical to FC-MLB-002 repaired evaluation
    ev = bg[(bg["season"] == season) & (bg["game_date"] >= F.START[season]) & (bg["slot"] <= 9)].copy()
    info = F.people_info(sorted(set(ev["batter"].tolist())))
    ev, side_excl = F.assign_matchup_side(ev, {k_: v["bat_side"] for k_, v in info.items()}, F.pitcher_hands(pa))
    ev = F.attach(ev, T, pitcher_col="opp_sp")
    ev = ev[ev["b_one"] >= F.MIN_PA].reset_index(drop=True)
    sp = F.asof(T["s"], ["pitcher"], ["bf", "starts"], ev.rename(columns={"opp_sp": "pitcher"}), "s_").rename(
        columns={"pitcher": "opp_sp"})
    ev = ev.merge(sp, on=["season", "opp_sp", "game_date"], how="left")
    lg_bf = ev["c_sp_bf"] / ev["c_sp_starts"]
    team_pa = ev["c_one"] / ev["c_team_games"]
    ev["w_sp"] = np.minimum(1.0, ((ev["s_bf"] + F.SP_PRIOR_STARTS * lg_bf) / (ev["s_starts"] + F.SP_PRIOR_STARTS)) / team_pa)
    ev["proj_pa"] = [gp.project_batter_pa(int(s), None) for s in ev["slot"]]
    ev["lg_h1"] = ev["c_h1"] / ev["c_bgn"]
    # ---- CH0 reference (production path, unchanged functions)
    comp = lambda r, c: r[f"b_{c}"] / r["b_one"]                                          # noqa: E731
    mod0 = [pp.p_at_least_hits(1, pp.pa_outcome_distribution(singles_rate=comp(r, "single"), double_rate=comp(r, "double"),
                                                              triple_rate=comp(r, "triple"), hr_rate=comp(r, "home_run")),
                               r["proj_pa"]) for _, r in ev.iterrows()]
    ev["CH0"] = 0.5 * ev["lg_h1"] + 0.5 * np.array(mod0)
    # ---- design matrices: starter, non-switch bullpen, switch bullpen at L and R
    sw = (ev["switch"] == 1).to_numpy()
    sh_sp = np.where(sw, 0.0, (ev["stand"] == ev["p_throws_sp"]).astype(float))
    Dsp = F.design(ev, k2, "p_", sh_override=sh_sp)
    Dpen = F.design(ev, k2, "r_")
    e4 = attach4(ev, T4, "opp_sp")
    Dsp = Dsp.assign(Lpx=np.asarray(lpx(e4, kpx, "px_")), Dteam=np.asarray(dteam(e4, kd)))
    Dpen = Dpen.assign(Lpx=np.asarray(lpx(e4, kpx, "rx_")), Dteam=np.asarray(dteam(e4, kd)))
    side = {}
    for st_ in "LR":
        e2 = ev.drop(columns=[c_ for c_ in ev.columns if c_.startswith("r_")]).assign(stand=st_)
        e2 = e2.merge(F.asof(T["r"], ["fld_team", "stand"], T["pcols"], e2, "r_"),
                      on=["season", "fld_team", "stand", "game_date"], how="left")
        e2x = attach4(e2, T4, "opp_sp")
        side[st_] = F.design(e2, k2, "r_", sh_override=0.0).assign(
            Lpx=np.asarray(lpx(e2x, kpx, "rx_")), Dteam=np.asarray(dteam(e2x, kd)))
    s_rhp = (ev["c_rel_rhp"] / ev["c_rel_all"]).to_numpy()
    models = {"P0": (f2["coef"]["CH1a"], ["Lb", "Lp", "same_hand"]), "P1": (fz["coef"]["P1"], P1_COLS)}
    p2_valid = bool(fz["p2_valid_point_in_time"]) and bool(np.isfinite(Dsp["Dteam"]).all())
    if p2_valid:
        models["P2"] = (fz["coef"]["P2"], P2_COLS)
    for name, (c, cols) in models.items():
        z = lambda D: c["intercept"] + sum(c[x] * D[x] for x in cols)                      # noqa: E731
        pen = np.where(sw, s_rhp * F.expit(z(side["L"])) + (1 - s_rhp) * F.expit(z(side["R"])), F.expit(z(Dpen)))
        p_pa = ev["w_sp"] * F.expit(z(Dsp)) + (1 - ev["w_sp"]) * pen
        ev[f"p_pa_{name}"] = p_pa
        ev[name] = p_game(p_pa, ev["proj_pa"], ev["lg_h1"], pp)
    ev["Lpx_sp"], ev["Lp_sp"], ev["Dteam"] = Dsp["Lpx"].to_numpy(), Dsp["Lp"].to_numpy(), Dsp["Dteam"].to_numpy()
    res = {"label": ("DEVELOPMENT_EVIDENCE (2026 outcomes previously visible; hypothesis chosen after FC-MLB-002 2026 "
                     "results -> doubly development; not confirmatory)" if season == 2026 else
                     "SELFCHECK on 2025 training season, IN-SAMPLE: mechanics only, not evidence"),
           "criteria": "engineering/ops/TASKS/FC-MLB-004.md @ claude/full-count-ops-state (sha256 3f627ad8...)",
           "fc_mlb_002_fit_sha256": F002_FIT_SHA, "frozen_fit_004_sha256": F.sha256_file(fz_path),
           "kpx": kpx, "kd": kd, "p2_valid": p2_valid, "raw_provenance": prov, "contact_provenance": cprov,
           "population": {"player_games": int(len(ev)), "dates": int(ev["game_date"].nunique()),
                          "first": ev["game_date"].min(), "last": ev["game_date"].max(),
                          "h1_base_rate": float(ev["h1"].mean()), "side_excluded": int(len(side_excl)),
                          "switch_player_games": int(ev["switch"].sum())}}
    if season == 2026:
        res["fidelity_gate"] = fidelity_gate(ev)
    names = ["CH0", *models]
    pairs = [("P1", "P0"), ("P1", "CH0"), ("P0", "CH0")] + ([("P2", "P0"), ("P2", "P1"), ("P2", "CH0")] if p2_valid else [])
    prim = {}
    for K in F.TOPK:
        row = {"counts": F.topk_counts(ev, K), "rates": {n: float(F.topk_rates(ev, n, K).mean()) for n in names}}
        for a_, b_ in pairs:
            row[f"{a_}_minus_{b_}"] = F.paired(ev, a_, b_, K)
        prim[f"K{K}"] = row
    d = prim["K10"]["P1_minus_P0"]
    verdict = ("IMPROVES" if d["diff_pp"] >= 1.0 and d["ci95_pp"][0] > 0 else
               "WORSE" if d["ci95_pp"][1] < 0 else
               "NO_GAIN" if d["ci95_pp"][0] <= 0 <= d["ci95_pp"][1] and abs(d["diff_pp"]) < 0.5 else "INCONCLUSIVE")
    res["primary"] = prim
    res["verdict_K10_P1_vs_P0"] = verdict
    # ---- stability (K=10)
    month = ev["game_date"].str[:7]
    groups = [("apr15_jun30", ev["game_date"] <= f"{season}-06-30"), ("jul01_end", ev["game_date"] > f"{season}-06-30")]
    groups += [(m_, month == m_) for m_ in sorted(month.unique())]
    bf = ev["s_bf"].fillna(0)
    groups += [("sp_bf_lt100", bf < 100), ("sp_bf_100_299", (bf >= 100) & (bf < 300)),
               ("sp_bf_300_499", (bf >= 300) & (bf < 500)), ("sp_bf_ge500", bf >= 500)]
    stab = {}
    for lab, mask in groups:
        sub = ev[mask]
        if sub["game_date"].nunique() == 0:
            continue
        stab[lab] = {"dates": int(sub["game_date"].nunique()), "rows": int(len(sub)),
                     **{f"{a_}_minus_{b_}_pp": F.paired(sub, a_, b_, 10)["diff_pp"] for a_, b_ in pairs},
                     **{f"ll_gain_{a_}_vs_{b_}": F.mean_ll(sub[a_], sub["h1"]) - F.mean_ll(sub[b_], sub["h1"]) for a_, b_ in pairs}}
    res["stability_K10"] = stab
    e0 = stab.get("apr15_jun30", {})
    res["reduces_early_instability"] = bool(e0) and (e0.get("P1_minus_CH0_pp", 0) - e0.get("P0_minus_CH0_pp", 0)) >= 1.0
    # ---- probability quality
    pq = {}
    for n in names:
        p = ev[n].to_numpy()
        pq[n] = {"logloss": -F.mean_ll(p, ev["h1"].to_numpy()), "brier": float(np.mean((p - ev["h1"]) ** 2)),
                 "mean_pred": float(p.mean())}
        dec = pd.qcut(ev[n], 10, labels=False, duplicates="drop")
        pq[n]["calibration_deciles"] = ev.groupby(dec).agg(pred=(n, "mean"), act=("h1", "mean"), n=("h1", "size")).round(4).to_dict("records")
    pq["corr"] = {f"{a_}~{b_}": float(np.corrcoef(ev[a_], ev[b_])[0, 1]) for a_, b_ in pairs}
    pq["feature_corr_Lp_Lpx_sp"] = float(np.corrcoef(ev["Lp_sp"], ev["Lpx_sp"])[0, 1])
    res["probability_quality"] = pq
    if market:
        ev["player_norm"] = ev["batter"].map(lambda i: F.norm((info.get(int(i)) or {}).get("name", "")))
        props, props_sha = F.load_props(sorted(ev["game_date"].unique()))
        sched = F.schedule_games(sorted(props["game_date"].unique())) if len(props) else pd.DataFrame()
        m, ident = F.match_market(props, sched, ev, pp)
        mres = {"props_source": f"data/props @ origin/main {props_sha}", "identity": ident,
                "devig": "one-sided FanDuel price, production assumed hold (pp.devig): APPROXIMATE", "rows": int(len(m))}
        if len(m):
            mres["dates"] = int(m["game_date"].nunique())
            y = m["h1"].to_numpy()
            mres["logloss"] = {n: -F.mean_ll(m[n].to_numpy(), y) for n in ("mkt_fair", *names)}
            mp = [("P1", "P0"), ("P1", "mkt_fair"), ("P0", "mkt_fair")] + ([("P2", "P0"), ("P2", "mkt_fair")] if p2_valid else [])
            for K in (3, 5):
                row = {"counts": F.topk_counts(m, K), "rates": {n: float(F.topk_rates(m, n, K).mean()) for n in ("mkt_fair", *names)}}
                for a_, b_ in mp:
                    row[f"{a_}_minus_{b_}"] = F.paired(m, a_, b_, K)
                mres[f"topK{K}"] = row
        res["market"] = mres
    keep = ["game_date", "game_pk", "batter", "bat_side", "switch", "stand", "p_throws_sp", "slot", "opp_sp", "s_bf",
            "proj_pa", "w_sp", "lg_h1", "h1", "Lp_sp", "Lpx_sp", "Dteam", *names, *[f"p_pa_{n}" for n in models]]
    os.makedirs(OUT, exist_ok=True)
    pred = os.path.join(OUT, f"predictions_{season}.csv.gz")
    ev[keep].to_csv(pred, index=False, compression="gzip")
    res["artifacts"] = {"predictions_sha256": F.sha256_file(pred)}
    json.dump(res, open(out_json, "w"), indent=1, default=float)
    print(json.dumps({k_: res[k_] for k_ in ("population", "verdict_K10_P1_vs_P0") if k_ in res}, indent=1, default=float))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("contact", "fit", "selfcheck", "evaluate"))
    a = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    if a.stage == "contact":
        stage_contact()
    elif a.stage == "fit":
        stage_fit()
    elif a.stage == "selfcheck":
        stage_evaluate(season=2025, out_name="SELFCHECK_2025_INSAMPLE.json", market=False)
    else:
        stage_evaluate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
