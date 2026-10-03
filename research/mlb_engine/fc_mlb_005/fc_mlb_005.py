#!/usr/bin/env python3
"""FC-MLB-005: team conversion residual isolation for Hits 1+ (DEVELOPMENT evidence; hypothesis generated after
inspecting 2026 development results -- FC-MLB-004's secondary P2-P1 contrast).

Criteria: engineering/ops/TASKS/FC-MLB-005.md on claude/full-count-ops-state (frozen at f487b2e69d, sha256 7ef7d98b...).

  D0 = FC-MLB-004 P0 = FC-MLB-002 frozen CH1a (Lb + Lp + same_hand), fidelity-gated vs FC-MLB-004 predictions
  D1 = D0 + Dteam           Dteam = TEAM CONVERSION RESIDUAL (fielding team's shrunk logit actual - logit xBA-expected
                            hit rate on balls in play, through D-1), FC-MLB-004's frozen construction, k_d = 1500 reused

Everything else (population, settlement, switch side, projected PA, w_sp, top-K, market identity) is imported unchanged
from fc_mlb_002 (7c331c266b) and fc_mlb_004 (46712d8091).

  python3 fc_mlb_005.py fit        2025 only; writes out/FROZEN_FIT_005.json (commit BEFORE evaluate)
  python3 fc_mlb_005.py evaluate   ONE 2026 evaluation (+ descriptive mechanism diagnostics)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
F004_DIR = os.path.join(os.path.dirname(HERE), "fc_mlb_004")
sys.path.insert(0, F004_DIR)
import fc_mlb_004 as X  # noqa: E402

F = X.F
OUT = os.path.join(HERE, "out")
F004_FIT = os.path.join(F004_DIR, "out", "FROZEN_FIT_004.json")
F004_FIT_SHA = "fbe9362fe2ac0dddcb85ad363a1056ad4392a7a109385cca374e5d459e026c42"
F004_PRED = os.path.join(F004_DIR, "out", "predictions_2026.csv.gz")
F004_PRED_SHA = "3328267f08086cc59213240eaa4fc6edef9f956a9c501da2d58ac35183f5066d"  # 46712d8091
KD = 1500                                   # reused from FC-MLB-004; no grid (criteria §4)
D0_COLS = ["Lb", "Lp", "same_hand"]
D1_COLS = ["Lb", "Lp", "same_hand", "Dteam"]
REFIT_TOL = 1e-4


def frozen_inputs():
    assert F.sha256_file(X.F002_FIT) == X.F002_FIT_SHA
    assert F.sha256_file(F004_FIT) == F004_FIT_SHA
    f2, f4 = json.load(open(X.F002_FIT)), json.load(open(F004_FIT))
    if f4["kd"] != KD:
        raise SystemExit(f"STOP: FC-MLB-004 frozen kd is {f4['kd']}, criteria reuse {KD}")
    return f2, f4


def load_all():
    F.verified_provenance()
    contact, cprov = X.load_contact()
    pa, pit = F.load()
    T, bg = F.tables(pa, pit)
    pa4 = X.expected_hits(pa, contact)
    return pa, pit, T, bg, pa4, X.tables4(pa4), cprov


def home_teams(pa):
    """Home team per game = the fielding team on the game's first PA (top of the 1st). Identity only."""
    first = pa.sort_values(["game_pk", "at_bat_number"]).drop_duplicates("game_pk")
    return first.set_index("game_pk")["fld_team"].to_dict()


def raw_resid(h, x, n):
    """Unshrunk team conversion residual logit(H/BIP) - logit(xH/BIP)."""
    return F.logit(np.asarray(h, float) / n) - F.logit(np.asarray(x, float) / n)


# ------------------------------------------------------------------ fit (2025 only)
def training_rows(pa4, T, T4):
    tr = pa4[(pa4["season"] == 2025) & (pa4["game_date"] >= F.START[2025])].copy()
    tr = F.attach(tr, T)
    tr = X.attach4(tr, T4, "pitcher")
    return tr[tr["b_one"] >= F.MIN_PA].reset_index(drop=True)


def stage_fit():
    f2, _ = frozen_inputs()
    _, _, T, _, pa4, T4, _ = load_all()
    tr = training_rows(pa4, T, T4)
    y = tr["hit"].to_numpy()
    A = (tr["game_date"] <= "2025-07-15").to_numpy()
    D = F.design(tr, f2["k"]).assign(Dteam=lambda d: np.asarray(X.dteam(tr, KD)))
    if not np.isfinite(D["Dteam"]).all():
        raise SystemExit("STOP: Dteam not computable for every 2025 training row")
    refit = X.coef_dict(X.fit_cols(D, y, D0_COLS), D0_COLS)
    dev = max(abs(refit[c] - f2["coef"]["CH1a"][c]) for c in refit)
    if dev > REFIT_TOL:
        raise SystemExit(f"STOP: D0 refit does not reproduce frozen CH1a (max |d| {dev})")
    hold = {}
    for name, cols in (("D0_refit", D0_COLS), ("D1", D1_COLS)):
        mA = X.fit_cols(D, y, cols, A)
        hold[name] = {"holdout_ll_jul16_end": F.mean_ll(mA.predict_proba(D[cols].to_numpy()[~A])[:, 1], y[~A]),
                      "coef_first_half": X.coef_dict(mA, cols)}
    frozen = {"label": "FC-MLB-005 frozen 2025 fit (D1 = CH1a + team conversion residual)",
              "kb_kp_from_fc_mlb_002": f2["k"], "kd": KD, "kd_source": "FC-MLB-004 FROZEN_FIT_004.json (reused, no grid)",
              "n_pa_2025": int(len(tr)), "coef": {"D0": f2["coef"]["CH1a"], "D1": X.coef_dict(X.fit_cols(D, y, D1_COLS), D1_COLS)},
              "d0_refit_check": {"refit": refit, "max_abs_dev_vs_frozen_CH1a": dev, "tol": REFIT_TOL},
              "holdout_2025": hold, "dteam_2025_training": {"mean": float(D["Dteam"].mean()), "sd": float(D["Dteam"].std())}}
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "FROZEN_FIT_005.json")
    json.dump(frozen, open(path, "w"), indent=1)
    print(json.dumps({k_: frozen[k_] for k_ in ("coef", "d0_refit_check", "holdout_2025")}, indent=1), F.sha256_file(path))


# ------------------------------------------------------------------ evaluation
def fidelity_gate(ev, path=F004_PRED, sha=F004_PRED_SHA):
    """D0 and CH0 recomputed here must equal FC-MLB-004's committed P0/CH0 (46712d8091) row for row."""
    if F.sha256_file(path) != sha:
        raise SystemExit("FIDELITY GATE: FC-MLB-004 predictions file is not the 46712d8091 artifact")
    ref = pd.read_csv(path)
    j = ev[["game_pk", "batter", "CH0", "D0"]].merge(ref[["game_pk", "batter", "CH0", "P0"]], on=["game_pk", "batter"],
                                                     how="outer", suffixes=("", "_ref"), indicator=True)
    if (j["_merge"] != "both").any():
        raise SystemExit(f"FIDELITY GATE: population differs from FC-MLB-004 ({(j['_merge'] != 'both').sum()} rows)")
    d0 = float(np.abs(j["CH0"] - j["CH0_ref"]).max())
    d1 = float(np.abs(j["D0"] - j["P0"]).max())
    if d0 > 1e-9 or d1 > 1e-9:
        raise SystemExit(f"FIDELITY GATE: max |dCH0|={d0}, |dD0-P0|={d1}")
    return {"rows": int(len(j)), "max_abs_diff_CH0": d0, "max_abs_diff_D0_vs_P0": d1}


def build_population(season, pa, T, bg, T4, k2, gp):
    """Identical to FC-MLB-002/004 repaired evaluation population and mixture inputs."""
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
    e4 = X.attach4(ev, T4, "opp_sp")                       # left merges: row order preserved
    assert (e4[["game_pk", "batter"]].to_numpy() == ev[["game_pk", "batter"]].to_numpy()).all()
    ev["Dteam"] = np.asarray(X.dteam(e4, KD))
    ev["d_bip"] = e4["d_bip"].to_numpy()
    return ev, info, side_excl


def predict(ev, T, k2, models, pp):
    comp = lambda r, c: r[f"b_{c}"] / r["b_one"]                                          # noqa: E731
    mod0 = [pp.p_at_least_hits(1, pp.pa_outcome_distribution(singles_rate=comp(r, "single"), double_rate=comp(r, "double"),
                                                              triple_rate=comp(r, "triple"), hr_rate=comp(r, "home_run")),
                               r["proj_pa"]) for _, r in ev.iterrows()]
    ev["CH0"] = 0.5 * ev["lg_h1"] + 0.5 * np.array(mod0)
    sw = (ev["switch"] == 1).to_numpy()
    dt = ev["Dteam"].to_numpy()                          # team quantity: same for starter, bullpen and both switch sides
    Dsp = F.design(ev, k2, "p_", sh_override=np.where(sw, 0.0, (ev["stand"] == ev["p_throws_sp"]).astype(float))).assign(Dteam=dt)
    Dpen = F.design(ev, k2, "r_").assign(Dteam=dt)
    side = {}
    for st_ in "LR":
        e2 = ev.drop(columns=[c_ for c_ in ev.columns if c_.startswith("r_")]).assign(stand=st_)
        e2 = e2.merge(F.asof(T["r"], ["fld_team", "stand"], T["pcols"], e2, "r_"),
                      on=["season", "fld_team", "stand", "game_date"], how="left")
        side[st_] = F.design(e2, k2, "r_", sh_override=0.0).assign(Dteam=dt)
    s_rhp = (ev["c_rel_rhp"] / ev["c_rel_all"]).to_numpy()
    for name, (c, cols) in models.items():
        z = lambda D: c["intercept"] + sum(c[x] * D[x] for x in cols)                      # noqa: E731
        pen = np.where(sw, s_rhp * F.expit(z(side["L"])) + (1 - s_rhp) * F.expit(z(side["R"])), F.expit(z(Dpen)))
        p_pa = ev["w_sp"] * F.expit(z(Dsp)) + (1 - ev["w_sp"]) * pen
        ev[f"p_pa_{name}"] = p_pa
        ev[name] = X.p_game(p_pa, ev["proj_pa"], ev["lg_h1"], pp)
    return ev


def ll_gain(sub, a, b):
    return F.mean_ll(sub[a], sub["h1"]) - F.mean_ll(sub[b], sub["h1"])


def verdict_of(d):
    lo, hi = d["ci95_pp"]
    return ("IMPROVES" if d["diff_pp"] >= 1.0 and lo > 0 else "WORSE" if hi < 0 else
            "NO_GAIN" if lo <= 0 <= hi and abs(d["diff_pp"]) < 0.5 else "INCONCLUSIVE")


def mechanism(pa4, T4, ev, home, season=2026):
    """Descriptive only (criteria §9). No variant is built from these numbers."""
    p = pa4[pa4["bip"] == 1].assign(home=lambda d: d["game_pk"].map(home))
    p["fld_home"] = (p["fld_team"] == p["home"]).astype(int)
    out = {}

    def team_res(df, keys):
        g = df.groupby(keys)[["hit", "xh", "bip"]].sum()
        return pd.Series(raw_resid(g["hit"], g["xh"], g["bip"]), index=g.index), g["bip"]

    def corr(a, b):
        j = pd.concat([a, b], axis=1, join="inner").dropna()
        return {"r": float(np.corrcoef(j.iloc[:, 0], j.iloc[:, 1])[0, 1]), "n": int(len(j))}

    s25 = p[p["season"] == 2025]
    h1, _ = team_res(s25[s25["game_date"] <= "2025-07-15"], ["fld_team"])
    h2, _ = team_res(s25[s25["game_date"] > "2025-07-15"], ["fld_team"])
    doy = pd.to_datetime(s25["game_date"]).dt.dayofyear
    odd, _ = team_res(s25[doy % 2 == 1], ["fld_team"])
    even, _ = team_res(s25[doy % 2 == 0], ["fld_team"])
    full, nb = team_res(p, ["season", "fld_team"])
    out["persistence"] = {
        "2025_first_half_vs_second_half": corr(h1, h2),
        "2025_odd_vs_even_dates_split_half": corr(odd, even),
        "2025_full_vs_2026_full": corr(full.xs(2025), full.xs(2026)),
        "team_season_raw_residual_sd": {str(s): float(full.xs(s).std()) for s in (2025, 2026)},
        "team_season_bip_mean": float(nb.mean())}
    hres, _ = team_res(p[p["fld_home"] == 1], ["season", "fld_team"])
    ares, _ = team_res(p[p["fld_home"] == 0], ["season", "fld_team"])
    vis, _ = team_res(p[p["fld_home"] == 0].rename(columns={"fld_team": "f", "home": "fld_team"}), ["season", "fld_team"])
    out["park_home"] = {
        "team_home_resid_vs_team_away_resid": corr(hres, ares),
        "team_home_resid_vs_visitors_resid_at_same_park": corr(hres, vis),
        "team_away_resid_vs_visitors_resid_at_its_park": corr(ares, vis),
        "note": "team-seasons (2025, 2026). Skill -> home~away; park -> home~visitors-at-park."}
    # point-in-time Dteam vs realized rest-of-season raw residual, per team-date
    td = pa4[["season", "game_date", "fld_team"]].drop_duplicates().assign(k=0)
    td = td.merge(F.asof(T4["l4"], ["k"], ["one", "xh", "bip", "h_bip", "xh_bip"], td, "c4_"), on=["season", "k", "game_date"])
    td = td.merge(F.asof(T4["d"], ["fld_team"], ["bip", "h_bip", "xh_bip"], td, "d_"), on=["season", "fld_team", "game_date"])
    tot = pa4.groupby(["season", "fld_team"])[["bip", "h_bip", "xh_bip"]].sum().add_prefix("tot_").reset_index()
    td = td.merge(tot, on=["season", "fld_team"])
    fb = td["tot_bip"] - td["d_bip"]
    td["future_resid"] = raw_resid(td["tot_h_bip"] - td["d_h_bip"], td["tot_xh_bip"] - td["d_xh_bip"], fb)
    td["dteam"] = np.asarray(X.dteam(td, KD))
    td["past_raw"] = np.where(td["d_bip"] > 0, raw_resid(td["d_h_bip"], td["d_xh_bip"], td["d_bip"].clip(lower=1)), np.nan)
    ok = (fb >= 300) & (td["game_date"] >= td["season"].map(F.START))
    out["predictive_association"] = {}
    for s in (2025, 2026):
        t = td[ok & (td["season"] == s)]
        out["predictive_association"][str(s)] = {
            "corr_dteam_vs_rest_of_season_resid": float(np.corrcoef(t["dteam"], t["future_resid"])[0, 1]),
            "corr_unshrunk_past_vs_rest_of_season_resid": float(t[["past_raw", "future_resid"]].dropna().corr().iloc[0, 1]),
            "team_dates": int(len(t)), "rule": "rest-of-season BIP >= 300; from START date"}
    # distribution on evaluation rows, by season-to-date team BIP
    q = ev["Dteam"].quantile([0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99]).round(4)
    bins = pd.cut(ev["d_bip"], [-1, 500, 1000, 2000, 3000, 10 ** 6], labels=["<=500", "501-1000", "1001-2000", "2001-3000", ">3000"])
    out["distribution_eval_rows"] = {
        "mean": float(ev["Dteam"].mean()), "sd": float(ev["Dteam"].std()), "quantiles": {str(k): float(v) for k, v in q.items()},
        "by_team_bip_to_date": ev.groupby(bins, observed=True)["Dteam"].agg(["size", "mean", "std"]).round(4).reset_index().astype(
            {"d_bip": str}).to_dict("records")}
    tm = ev.groupby("fld_team")["Dteam"].transform("mean")
    out["team_identity_eta2_eval_rows"] = float(((tm - ev["Dteam"].mean()) ** 2).sum() / ((ev["Dteam"] - ev["Dteam"].mean()) ** 2).sum())
    late = ev[ev["game_date"] >= f"{season}-09-01"].groupby("fld_team")["Dteam"].mean()
    out["late_season_team_dteam_range"] = {"min": float(late.min()), "max": float(late.max()),
                                           "min_team": str(late.idxmin()), "max_team": str(late.idxmax())}
    return out


def stage_evaluate(season=2026):
    out_json = os.path.join(OUT, "RESULTS.json")
    if os.path.exists(out_json):
        raise SystemExit("RESULTS.json exists: the frozen evaluation runs ONCE")
    f2, _ = frozen_inputs()
    fz_path = os.path.join(OUT, "FROZEN_FIT_005.json")
    fz = json.load(open(fz_path))
    k2 = f2["k"]
    sys.path.insert(0, F.CHAMP)
    import prop_probability as pp          # noqa: E402
    import generate_picks as gp            # noqa: E402
    champ_head = subprocess.check_output(["git", "-C", F.CHAMP, "rev-parse", "HEAD"]).decode().strip()
    assert champ_head == F.CHAMP_SHA, champ_head
    prov = F.verified_provenance()
    pa, _, T, bg, pa4, T4, cprov = load_all()
    ev, info, side_excl = build_population(season, pa, T, bg, T4, k2, gp)
    if not np.isfinite(ev["Dteam"]).all():
        raise SystemExit("STOP: Dteam not computable for every evaluation row")
    models = {"D0": (f2["coef"]["CH1a"], D0_COLS), "D1": (fz["coef"]["D1"], D1_COLS)}
    ev = predict(ev, T, k2, models, pp)
    gate = fidelity_gate(ev)                                     # aborts the run on failure
    home = home_teams(pa)
    ev["batter_home"] = (ev["fld_team"] != ev["game_pk"].map(home)).astype(int)
    names = ["CH0", "D0", "D1"]
    pairs = [("D1", "D0"), ("D0", "CH0"), ("D1", "CH0")]
    res = {"label": "DEVELOPMENT_EVIDENCE: 2026 outcomes previously visible AND hypothesis generated after inspecting "
                    "2026 development evidence (FC-MLB-004 P2-P1). Hypothesis-generating only; not confirmatory.",
           "criteria": "engineering/ops/TASKS/FC-MLB-005.md @ claude/full-count-ops-state f487b2e69d (sha256 7ef7d98b...)",
           "terminology": "Dteam = TEAM CONVERSION RESIDUAL (not established as defense)",
           "fc_mlb_002_fit_sha256": X.F002_FIT_SHA, "fc_mlb_004_fit_sha256": F004_FIT_SHA,
           "frozen_fit_005_sha256": F.sha256_file(fz_path), "kd": KD, "fidelity_gate": gate,
           "raw_provenance": prov, "contact_provenance": cprov,
           "population": {"player_games": int(len(ev)), "dates": int(ev["game_date"].nunique()),
                          "first": ev["game_date"].min(), "last": ev["game_date"].max(),
                          "h1_base_rate": float(ev["h1"].mean()), "side_excluded": int(len(side_excl)),
                          "switch_player_games": int(ev["switch"].sum())}}
    prim = {}
    for K in F.TOPK:
        row = {"counts": F.topk_counts(ev, K), "rates": {n: float(F.topk_rates(ev, n, K).mean()) for n in names}}
        for a_, b_ in pairs:
            row[f"{a_}_minus_{b_}"] = F.paired(ev, a_, b_, K)
        prim[f"K{K}"] = row
    res["primary"] = prim
    res["verdict_K10_D1_vs_D0"] = verdict_of(prim["K10"]["D1_minus_D0"])
    res["verdict_K5_K20_same_rule_descriptive"] = {K: verdict_of(prim[K]["D1_minus_D0"]) for K in ("K5", "K20")}
    month = ev["game_date"].str[:7]
    bf = ev["s_bf"].fillna(0)
    groups = [("apr15_jun30", ev["game_date"] <= f"{season}-06-30"), ("jul01_end", ev["game_date"] > f"{season}-06-30")]
    groups += [(m_, month == m_) for m_ in sorted(month.unique())]
    groups += [("sp_bf_lt100", bf < 100), ("sp_bf_100_299", (bf >= 100) & (bf < 300)),
               ("sp_bf_300_499", (bf >= 300) & (bf < 500)), ("sp_bf_ge500", bf >= 500),
               ("batter_away_team_fielding_at_home", ev["batter_home"] == 0), ("batter_home", ev["batter_home"] == 1)]
    stab = {}
    for lab, mask in groups:
        sub = ev[mask]
        stab[lab] = {"dates": int(sub["game_date"].nunique()), "rows": int(len(sub)),
                     **{f"{a_}_minus_{b_}_pp": F.paired(sub, a_, b_, 10)["diff_pp"] for a_, b_ in pairs},
                     **{f"ll_gain_{a_}_vs_{b_}": ll_gain(sub, a_, b_) for a_, b_ in pairs}}
    res["stability_K10"] = stab
    h1, h2 = stab["apr15_jun30"], stab["jul01_end"]
    res["half_gap_pp"] = {"D0_minus_CH0_jul_minus_apr": h2["D0_minus_CH0_pp"] - h1["D0_minus_CH0_pp"],
                          "D1_minus_CH0_jul_minus_apr": h2["D1_minus_CH0_pp"] - h1["D1_minus_CH0_pp"]}
    pq = {}
    for n in names:
        p_ = ev[n].to_numpy()
        pq[n] = {"logloss": -F.mean_ll(p_, ev["h1"].to_numpy()), "brier": float(np.mean((p_ - ev["h1"]) ** 2)),
                 "mean_pred": float(p_.mean())}
        dec = pd.qcut(ev[n], 10, labels=False, duplicates="drop")
        pq[n]["calibration_deciles"] = ev.groupby(dec).agg(pred=(n, "mean"), act=("h1", "mean"), n=("h1", "size")).round(4).to_dict("records")
    pq["corr_D0_D1"] = float(np.corrcoef(ev["D0"], ev["D1"])[0, 1])
    pq["topK10_overlap_share_D0_D1"] = float(np.mean([
        len(set(F.topk_select(g, "D0", 10)["batter"]) & set(F.topk_select(g, "D1", 10)["batter"])) / min(10, len(g))
        for _, g in ev.groupby("game_date")]))
    res["probability_quality"] = pq
    res["mechanism"] = mechanism(pa4, T4, ev, home, season)
    ev["player_norm"] = ev["batter"].map(lambda i: F.norm((info.get(int(i)) or {}).get("name", "")))
    props, props_sha = F.load_props(sorted(ev["game_date"].unique()))
    sched = F.schedule_games(sorted(props["game_date"].unique())) if len(props) else pd.DataFrame()
    m, ident = F.match_market(props, sched, ev, pp)
    mres = {"props_source": f"data/props @ origin/main {props_sha}", "identity": ident,
            "devig": "one-sided FanDuel price, production assumed hold (pp.devig): APPROXIMATE", "rows": int(len(m))}
    if len(m):
        mres["dates"] = int(m["game_date"].nunique())
        mres["logloss"] = {n: -F.mean_ll(m[n].to_numpy(), m["h1"].to_numpy()) for n in ("mkt_fair", *names)}
        for K in (3, 5):
            row = {"counts": F.topk_counts(m, K), "rates": {n: float(F.topk_rates(m, n, K).mean()) for n in ("mkt_fair", *names)}}
            for a_, b_ in (("D1", "D0"), ("D1", "mkt_fair"), ("D0", "mkt_fair")):
                row[f"{a_}_minus_{b_}"] = F.paired(m, a_, b_, K)
            mres[f"topK{K}"] = row
    res["market"] = mres
    keep = ["game_date", "game_pk", "batter", "bat_side", "switch", "stand", "p_throws_sp", "slot", "opp_sp", "fld_team",
            "batter_home", "s_bf", "d_bip", "Dteam", "proj_pa", "w_sp", "lg_h1", "h1", *names, "p_pa_D0", "p_pa_D1"]
    pred = os.path.join(OUT, f"predictions_{season}.csv.gz")
    ev[keep].to_csv(pred, index=False, compression="gzip")
    res["artifacts"] = {"predictions_sha256": F.sha256_file(pred)}
    json.dump(res, open(out_json, "w"), indent=1, default=float)
    print(json.dumps({k_: res[k_] for k_ in ("population", "fidelity_gate", "verdict_K10_D1_vs_D0")}, indent=1, default=float))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("fit", "evaluate"))
    a = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    stage_fit() if a.stage == "fit" else stage_evaluate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
