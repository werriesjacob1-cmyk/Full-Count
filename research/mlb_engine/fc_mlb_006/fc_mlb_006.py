#!/usr/bin/env python3
"""FC-MLB-006: coherent plate-appearance event world model for Hits 1+ (DEVELOPMENT evidence).

Criteria: engineering/ops/TASKS/FC-MLB-006.md on claude/full-count-ops-state (frozen at 22a0bc1a53,
sha256 4a63ef82...). Machinery (population, settlement, switch side, w_sp, projected PA, top-K, bootstrap, market
identity) is imported read-only from fc_mlb_002 (7c331c266b) and fc_mlb_005 (5e1d6bd8ec).

E1: per PA, an 8-class softmax over {1B, 2B, 3B, HR, BB, HBP, K, OUT}
      z_c = a_c + b_c*Lb_c + g_c*Lp_c + d_c*same_hand   (z_OUT = 0)
    Lb_c / Lp_c = shrunk batter / opposing-pitcher log-odds of c vs OUT, relative to the league (season-to-date
    through D-1). With a_c = league log-odds, b = g = 1, d = 0 this is exactly the generalized log5 rule.
    Player game: h = w_sp*h_sp + (1-w_sp)*h_pen with h = q_1B+q_2B+q_3B+q_HR;
                 E1_raw = pp.p_at_least_hits(1, {0: 1-h, 1: h}, proj_pa);  E1 = 0.5*lg_h1 + 0.5*E1_raw.

  python3 fc_mlb_006.py fit        2025 only -> out/FROZEN_FIT_006.json (commit BEFORE evaluate)
  python3 fc_mlb_006.py evaluate   ONE 2026 evaluation
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "fc_mlb_005"))
import fc_mlb_005 as M5  # noqa: E402

F = M5.F
OUT = os.path.join(HERE, "out")
CATS = ["1B", "2B", "3B", "HR", "BB", "HBP", "K", "OUT"]      # OUT is the softmax reference (last)
NONREF = CATS[:-1]
HITS = ["1B", "2B", "3B", "HR"]
EVENT_MAP = {"single": "1B", "double": "2B", "triple": "3B", "home_run": "HR", "walk": "BB", "intent_walk": "BB",
             "hit_by_pitch": "HBP", "strikeout": "K", "strikeout_double_play": "K",
             **{e: "OUT" for e in ("field_out", "force_out", "grounded_into_double_play", "double_play", "triple_play",
                                   "sac_fly", "sac_fly_double_play", "sac_bunt", "fielders_choice",
                                   "fielders_choice_out", "field_error", "catcher_interf")}}
ECOLS = [f"e_{c}" for c in CATS]
F002_FIT_SHA = "6c0ecde23577659c1e72e18448415208eb4f5d7d2748f24b9631cd01ded7d6aa"
PROPS_SHA = "a8e472272c3cb57884c8a6ccb7cb41beaabb9b26"
PEOPLE_SHA = "84b5c960f71a921c3a43c3db66a2a8634c584038cf89302c9eae416415ae3ec7"
SCHED_SHA = "f7987b83ab0e2ed10a97829f7849af4abb8e832238082c1073ac66737ff3d308"
K_MIN, K_MAX, MOM_MIN_N = 20.0, 5000.0, 200
NC = len(NONREF)


# ------------------------------------------------------------------ events and point-in-time tables
def event_table(pa):
    cat = pa["events"].map(EVENT_MAP)
    if cat.isna().any():
        raise SystemExit(f"STOP: unmapped PA events {sorted(pa.loc[cat.isna(), 'events'].unique())}")
    out = pa.copy()
    for c in CATS:
        out[f"e_{c}"] = (cat == c).astype(int)
    if not (out[[f"e_{c}" for c in HITS]].sum(axis=1) == out["hit"]).all():
        raise SystemExit("STOP: hit categories disagree with pa.hit")
    return out


def tables6(pa6):
    cols = ["one", *ECOLS]
    T = {"b": F.through(pa6, ["batter"], cols),
         "p": F.through(pa6, ["pitcher", "stand"], cols),
         "r": F.through(pa6[pa6["vs_starter"] == 0], ["fld_team", "stand"], cols),
         "l": F.through(pa6.assign(k=0), ["k"], cols)}
    for t in T.values():
        t["game_date"] = t["game_date"].astype("datetime64[us]")
    return T


def attach6(rows, T6, pitcher_col="pitcher"):
    cols = ["one", *ECOLS]
    r = rows.copy()
    r["k"] = 0
    r = r.merge(F.asof(T6["l"], ["k"], cols, r, "l6_"), on=["season", "k", "game_date"], how="left")
    r = r.merge(F.asof(T6["b"], ["batter"], cols, r, "b6_"), on=["season", "batter", "game_date"], how="left")
    pk = r[["season", pitcher_col, "stand", "game_date"]].rename(columns={pitcher_col: "pitcher"})
    pv = F.asof(T6["p"], ["pitcher", "stand"], cols, pk, "p6_").rename(columns={"pitcher": pitcher_col})
    r = r.merge(pv, on=["season", pitcher_col, "stand", "game_date"], how="left")
    r = r.merge(F.asof(T6["r"], ["fld_team", "stand"], cols, r, "r6_"),
                on=["season", "fld_team", "stand", "game_date"], how="left")
    return r


def league_rates(r):
    L = np.column_stack([r[f"l6_e_{c}"] / r["l6_one"] for c in CATS])
    return L


def talent(r, src, k, L):
    """Shrunk log-odds of each non-reference category vs OUT, relative to the league: shape (n, 7)."""
    rate = np.column_stack([(r[f"{src}e_{c}"].to_numpy() + k[c] * L[:, j]) / (r[f"{src}one"].to_numpy() + k[c])
                            for j, c in enumerate(CATS)])
    return np.log(rate[:, :NC] / rate[:, [NC]]) - np.log(L[:, :NC] / L[:, [NC]])


# ------------------------------------------------------------------ softmax model
def unpack(theta):
    t = np.asarray(theta, float).reshape(4, NC)
    return t[0], t[1], t[2], t[3]                          # alpha, beta, gamma, delta


def probs(theta, Lb, Lp, sh):
    a, b, g, d = unpack(theta)
    Z = a + b * Lb + g * Lp + d * np.asarray(sh, float)[:, None]
    Z = np.column_stack([Z, np.zeros(len(Z))])
    Z -= Z.max(axis=1, keepdims=True)
    E = np.exp(Z)
    return E / E.sum(axis=1, keepdims=True)


def nll_grad(theta, Lb, Lp, sh, Y):
    """Mean negative multinomial log-likelihood and its analytic gradient (Y one-hot, n x 8)."""
    Q = probs(theta, Lb, Lp, sh)
    n = len(Y)
    nll = -float(np.sum(Y * np.log(np.clip(Q, 1e-300, None)))) / n
    R = (Q - Y)[:, :NC]                                     # d nll / d z_c (c != OUT), per row
    sh = np.asarray(sh, float)[:, None]
    grad = np.concatenate([R.sum(0), (R * Lb).sum(0), (R * Lp).sum(0), (R * sh).sum(0)]) / n
    return nll, grad


def fit_softmax(Lb, Lp, sh, Y, L_mean):
    theta0 = np.concatenate([np.log(L_mean[:NC] / L_mean[NC]), np.ones(NC), np.ones(NC), np.zeros(NC)])
    res = minimize(nll_grad, theta0, args=(Lb, Lp, sh, Y), jac=True, method="L-BFGS-B",
                   options={"maxiter": 5000, "gtol": 1e-9, "ftol": 1e-13})
    gnorm = float(np.abs(res.jac).max())
    if not res.success and gnorm > 1e-5:
        raise SystemExit(f"STOP: softmax fit did not converge ({res.message}; max|grad| {gnorm})")
    return res.x, {"success": bool(res.success), "message": str(res.message), "nit": int(res.nit),
                   "nll": float(res.fun), "max_abs_grad": gnorm}


def theta_dict(theta):
    a, b, g, d = unpack(theta)
    return {c: {"alpha": float(a[j]), "beta_batter": float(b[j]), "gamma_pitcher": float(g[j]), "delta_same_hand": float(d[j])}
            for j, c in enumerate(NONREF)}


def theta_from(dct):
    return np.concatenate([[dct[c][k] for c in NONREF] for k in ("alpha", "beta_batter", "gamma_pitcher", "delta_same_hand")])


def mom_k(counts, N):
    """Beta-binomial method-of-moments prior strength per category (criteria §5). counts: (players x 8), N: (players,)."""
    out = {}
    for j, c in enumerate(CATS):
        x = counts[:, j] / N
        pbar = counts[:, j].sum() / N.sum()
        vt = np.var(x, ddof=1) - np.mean(pbar * (1 - pbar) / N)
        k = K_MAX if vt <= 0 else pbar * (1 - pbar) / vt - 1
        out[c] = float(np.clip(k, K_MIN, K_MAX))
    return out


def prior_strengths(pa6):
    s = pa6[pa6["season"] == 2025]
    res = {}
    for who, key in (("batter", "batter"), ("pitcher", "pitcher")):
        g = s.groupby(key)[["one", *ECOLS]].sum()
        g = g[g["one"] >= MOM_MIN_N]
        res[who] = mom_k(g[ECOLS].to_numpy(float), g["one"].to_numpy(float))
        res[f"{who}_n_players"] = int(len(g))
    return res


# ------------------------------------------------------------------ data
def load_all():
    prov = F.verified_provenance()
    pa, pit = F.load()
    T, bg = F.tables(pa, pit)
    pa6 = event_table(pa)
    return prov, pa, T, bg, pa6, tables6(pa6)


def pa_rows(season, pa6, T, T6, start=None):
    """PA-level rows (actual pitcher, actual same_hand) with b_one >= MIN_PA: the baseline's training definition."""
    r = pa6[(pa6["season"] == season) & (pa6["game_date"] >= (start or F.START[season]))].copy()
    r = F.attach(r, T)
    r = attach6(r, T6, "pitcher")
    return r[r["b_one"] >= F.MIN_PA].reset_index(drop=True)


def pa_design(r, kz):
    L = league_rates(r)
    return talent(r, "b6_", kz["batter"], L), talent(r, "p6_", kz["pitcher"], L), r["same_hand"].to_numpy(float), L


# ------------------------------------------------------------------ fit (2025 only)
def stage_fit():
    f2 = json.load(open(M5.X.F002_FIT))
    assert F.sha256_file(M5.X.F002_FIT) == F002_FIT_SHA
    prov, pa, T, _, pa6, T6 = load_all()
    kz = prior_strengths(pa6)
    tr = pa_rows(2025, pa6, T, T6)
    Lb, Lp, sh, L = pa_design(tr, kz)
    Y = tr[ECOLS].to_numpy(float)
    if not (np.isfinite(Lb).all() and np.isfinite(Lp).all()):
        raise SystemExit("STOP: non-finite training features")
    theta, conv = fit_softmax(Lb, Lp, sh, Y, Y.mean(0))
    # descriptive 2025 split check (no selection): fit Apr15-Jul15, score Jul16-end
    A = (tr["game_date"] <= "2025-07-15").to_numpy()
    thA, convA = fit_softmax(Lb[A], Lp[A], sh[A], Y[A], Y[A].mean(0))
    QB = probs(thA, Lb[~A], Lp[~A], sh[~A])
    y_hit = tr["hit"].to_numpy()[~A]
    hB = QB[:, :4].sum(1)
    D0 = F.design(tr, f2["k"])
    cols = ["Lb", "Lp", "same_hand"]
    mA = F.fit_lr(D0[cols].to_numpy()[A], tr["hit"].to_numpy()[A])
    p0B = mA.predict_proba(D0[cols].to_numpy()[~A])[:, 1]
    Lh = L[~A]
    split = {"multiclass_logloss_E1": float(-np.mean(np.log(np.clip((QB * Y[~A]).sum(1), 1e-300, None)))),
             "multiclass_logloss_league_std": float(-np.mean(np.log((Lh / Lh.sum(1, keepdims=True) * Y[~A]).sum(1)))),
             "hit_logloss_E1": -F.mean_ll(hB, y_hit), "hit_logloss_P0_refit_first_half": -F.mean_ll(p0B, y_hit),
             "hit_logloss_league_std": -F.mean_ll(Lh[:, :4].sum(1) / Lh.sum(1), y_hit),
             "convergence_first_half": convA, "n_score": int((~A).sum())}
    frozen = {"label": "FC-MLB-006 frozen 2025 fit (8-class PA softmax)", "categories": CATS, "event_map": EVENT_MAP,
              "prior_strengths_mom_2025": kz, "theta": theta_dict(theta), "convergence": conv, "n_pa_2025": int(len(tr)),
              "train_event_freq": dict(zip(CATS, map(float, Y.mean(0)))), "split_check_2025_descriptive": split,
              "raw_provenance": prov, "baseline_fit_sha256": F002_FIT_SHA}
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "FROZEN_FIT_006.json")
    json.dump(frozen, open(path, "w"), indent=1)
    print(json.dumps({k: frozen[k] for k in ("prior_strengths_mom_2025", "theta", "convergence", "split_check_2025_descriptive")},
                     indent=1), F.sha256_file(path))


# ------------------------------------------------------------------ evaluation helpers
def build_population(season, pa, T, bg, gp):
    """Identical to FC-MLB-002/004/005 repaired evaluation population and mixture inputs."""
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
    return ev, info, side_excl


def predict_baseline(ev, T, k2, coef, pp):
    """CH0 (production path) and P0 (frozen CH1a), byte-for-byte the FC-MLB-002/004 construction."""
    comp = lambda r, c: r[f"b_{c}"] / r["b_one"]                                          # noqa: E731
    mod0 = [pp.p_at_least_hits(1, pp.pa_outcome_distribution(singles_rate=comp(r, "single"), double_rate=comp(r, "double"),
                                                              triple_rate=comp(r, "triple"), hr_rate=comp(r, "home_run")),
                               r["proj_pa"]) for _, r in ev.iterrows()]
    ev["CH0"] = 0.5 * ev["lg_h1"] + 0.5 * np.array(mod0)
    sw = (ev["switch"] == 1).to_numpy()
    cols = ["Lb", "Lp", "same_hand"]
    Dsp = F.design(ev, k2, "p_", sh_override=np.where(sw, 0.0, (ev["stand"] == ev["p_throws_sp"]).astype(float)))
    Dpen = F.design(ev, k2, "r_")
    side = {}
    for st_ in "LR":
        e2 = ev.drop(columns=[c_ for c_ in ev.columns if c_.startswith("r_")]).assign(stand=st_)
        e2 = e2.merge(F.asof(T["r"], ["fld_team", "stand"], T["pcols"], e2, "r_"),
                      on=["season", "fld_team", "stand", "game_date"], how="left")
        side[st_] = F.design(e2, k2, "r_", sh_override=0.0)
    s_rhp = (ev["c_rel_rhp"] / ev["c_rel_all"]).to_numpy()
    z = lambda D: coef["intercept"] + sum(coef[x] * D[x] for x in cols)                   # noqa: E731
    pen = np.where(sw, s_rhp * F.expit(z(side["L"])) + (1 - s_rhp) * F.expit(z(side["R"])), F.expit(z(Dpen)))
    ev["p_pa_P0"] = ev["w_sp"] * F.expit(z(Dsp)) + (1 - ev["w_sp"]) * pen
    ev["P0_raw"] = [pp.p_at_least_hits(1, {0: 1 - p, 1: p}, n) for p, n in zip(ev["p_pa_P0"], ev["proj_pa"])]
    ev["P0"] = 0.5 * ev["lg_h1"] + 0.5 * ev["P0_raw"]
    return ev


def event_components(ev, T6, kz, theta, zero_pitcher=False):
    """Per-PA event distributions vs starter and bullpen; returns (h, q_sp, q_pen) with h the mixed hit prob."""
    sw = (ev["switch"] == 1).to_numpy()
    e = attach6(ev, T6, "opp_sp")
    assert (e[["game_pk", "batter"]].to_numpy() == ev[["game_pk", "batter"]].to_numpy()).all()
    L = league_rates(e)
    Lb = talent(e, "b6_", kz["batter"], L)
    zp = lambda X_: np.zeros_like(X_) if zero_pitcher else X_                              # noqa: E731
    sh_sp = np.where(sw, 0.0, (ev["stand"] == ev["p_throws_sp"]).astype(float))
    q_sp = probs(theta, Lb, zp(talent(e, "p6_", kz["pitcher"], L)), sh_sp)
    sh_pen = np.where(ev["stand"] == "L", ev["c_rel_same_L"] / ev["c_rel_n_L"], ev["c_rel_same_R"] / ev["c_rel_n_R"])
    q_pen = probs(theta, Lb, zp(talent(e, "r6_", kz["pitcher"], L)), sh_pen)
    s_rhp = (ev["c_rel_rhp"] / ev["c_rel_all"]).to_numpy()
    q_side = {}
    for st_ in "LR":                          # switch hitter: vs RHP reliever bats L, vs LHP reliever bats R
        e2 = ev.drop(columns=[c_ for c_ in ev.columns if c_.startswith("r6_")]).assign(stand=st_)
        e2 = attach6(e2, T6, "opp_sp")
        q_side[st_] = probs(theta, Lb, zp(talent(e2, "r6_", kz["pitcher"], L)), np.zeros(len(ev)))
    q_pen = np.where(sw[:, None], s_rhp[:, None] * q_side["L"] + (1 - s_rhp[:, None]) * q_side["R"], q_pen)
    w = ev["w_sp"].to_numpy()[:, None]
    q_mix = w * q_sp + (1 - w) * q_pen
    return q_mix[:, :4].sum(1), q_sp, q_pen, q_mix, Lb


def aggregate(h, proj_pa, lg_h1, pp):
    raw = np.array([pp.p_at_least_hits(1, {0: 1 - p, 1: p}, n) for p, n in zip(h, proj_pa)])
    return raw, 0.5 * np.asarray(lg_h1) + 0.5 * raw


def fidelity_gate(ev):
    return M5.fidelity_gate(ev.rename(columns={"P0": "D0"}))


def verdict_of(d):
    lo, hi = d["ci95_pp"]
    return ("IMPROVES" if d["diff_pp"] >= 1.0 and lo > 0 else "WORSE" if hi < 0 else
            "NO_GAIN" if lo <= 0 <= hi and abs(d["diff_pp"]) < 0.5 else "INCONCLUSIVE")


def selection_detail(ev, a, b, K):
    sa, sb = F.topk_select(ev, a, K), F.topk_select(ev, b, K)
    ka = set(zip(sa["game_pk"], sa["batter"]))
    kb = set(zip(sb["game_pk"], sb["batter"]))
    added = sa[[x not in kb for x in zip(sa["game_pk"], sa["batter"])]]
    removed = sb[[x not in ka for x in zip(sb["game_pk"], sb["batter"])]]

    def conc(s):
        vc = s["batter"].value_counts()
        return {"distinct_batters": int(vc.size), "distinct_games": int(s["game_pk"].nunique()),
                "max_selections_one_batter": int(vc.max()), "top10_batters_share": float(vc.head(10).sum() / len(s))}
    return {"overlap_share": float(len(ka & kb) / len(ka)), "added_n": int(len(added)),
            "added_hit_rate": float(added["h1"].mean()) if len(added) else None,
            "removed_n": int(len(removed)), "removed_hit_rate": float(removed["h1"].mean()) if len(removed) else None,
            "concentration": {a: conc(sa), b: conc(sb)}}


def load_props_pinned(dates, repo="/home/user/Full-Count"):
    """F.load_props, but at the PINNED main commit (criteria §1), not the moving origin/main."""
    rows = []
    for d in dates:
        try:
            raw = subprocess.check_output(["git", "-C", repo, "show", f"{PROPS_SHA}:data/props/props_{d}.json"],
                                          stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            continue
        for s_ in json.loads(raw).get("snapshots", []):
            for x in s_.get("rows", []):
                if x.get("stat") == "hits" and x.get("needs") == 1 and not x.get("in_play") \
                        and x.get("taken_at") and x.get("start_time") and x.get("event_id") and x.get("game"):
                    rows.append({"game_date": d, "event_id": int(x["event_id"]), "game": x["game"],
                                 "player_norm": F.norm(x["player"]), "start_time": x["start_time"],
                                 "taken_at": x["taken_at"], "american": x["american"]})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ evaluation (ONE run)
def stage_evaluate(season=2026):
    out_json = os.path.join(OUT, "RESULTS.json")
    if os.path.exists(out_json):
        raise SystemExit("RESULTS.json exists: the frozen evaluation runs ONCE")
    f2 = json.load(open(M5.X.F002_FIT))
    assert F.sha256_file(M5.X.F002_FIT) == F002_FIT_SHA
    fz_path = os.path.join(OUT, "FROZEN_FIT_006.json")
    fz = json.load(open(fz_path))
    kz, theta = fz["prior_strengths_mom_2025"], theta_from(fz["theta"])
    sys.path.insert(0, F.CHAMP)
    import prop_probability as pp          # noqa: E402
    import generate_picks as gp            # noqa: E402
    champ_head = subprocess.check_output(["git", "-C", F.CHAMP, "rev-parse", "HEAD"]).decode().strip()
    assert champ_head == F.CHAMP_SHA, champ_head
    assert F.sha256_file(os.path.join(F.DATA, "people_v2.json")) == PEOPLE_SHA, "people_v2.json changed"
    prov, pa, T, bg, pa6, T6 = load_all()
    ev, info, side_excl = build_population(season, pa, T, bg, gp)
    assert F.sha256_file(os.path.join(F.DATA, "people_v2.json")) == PEOPLE_SHA, "people_v2.json changed"
    ev = predict_baseline(ev, T, f2["k"], f2["coef"]["CH1a"], pp)
    gate = fidelity_gate(ev)                                           # aborts on failure, before any challenger metric
    h, q_sp, q_pen, q_mix, Lb = event_components(ev, T6, kz, theta)
    ev["h_E1"] = h
    ev["E1_raw"], ev["E1"] = aggregate(h, ev["proj_pa"], ev["lg_h1"], pp)
    h_np, *_ = event_components(ev, T6, kz, theta, zero_pitcher=True)
    ev["E1_nopitcher_diag"] = aggregate(h_np, ev["proj_pa"], ev["lg_h1"], pp)[1]
    for j, c in enumerate(CATS):
        ev[f"q_{c}"] = q_mix[:, j]
    home = M5.home_teams(pa)
    ev["batter_home"] = (ev["fld_team"] != ev["game_pk"].map(home)).astype(int)
    names = ["CH0", "P0", "E1"]
    pairs = [("E1", "P0"), ("P0", "CH0"), ("E1", "CH0")]
    res = {"label": "DEVELOPMENT_EVIDENCE: 2026 outcomes previously studied (FC-MLB-002/004/005); not confirmatory.",
           "criteria": "engineering/ops/TASKS/FC-MLB-006.md @ claude/full-count-ops-state 22a0bc1a53 (sha256 4a63ef82...)",
           "frozen_fit_006_sha256": F.sha256_file(fz_path), "baseline_fit_sha256": F002_FIT_SHA,
           "fidelity_gate": gate, "raw_provenance": prov, "props_pinned_main": PROPS_SHA,
           "population": {"player_games": int(len(ev)), "dates": int(ev["game_date"].nunique()),
                          "first": ev["game_date"].min(), "last": ev["game_date"].max(),
                          "h1_base_rate": float(ev["h1"].mean()), "side_excluded": int(len(side_excl)),
                          "switch_player_games": int(ev["switch"].sum())}}
    # ---- primary + secondary
    prim = {}
    for K in F.TOPK:
        row = {"counts": F.topk_counts(ev, K), "rates": {n: float(F.topk_rates(ev, n, K).mean()) for n in names}}
        for a_, b_ in pairs:
            row[f"{a_}_minus_{b_}"] = F.paired(ev, a_, b_, K)
        row["selection_E1_vs_P0"] = selection_detail(ev, "E1", "P0", K)
        row["verdict_rule_E1_vs_P0"] = verdict_of(row["E1_minus_P0"])
        prim[f"K{K}"] = row
    res["primary"] = prim
    res["verdict_K10_E1_vs_P0"] = prim["K10"]["verdict_rule_E1_vs_P0"]
    # ---- stability (K10)
    month = ev["game_date"].str[:7]
    bf = ev["s_bf"].fillna(0)
    groups = [("apr15_jun30", ev["game_date"] <= f"{season}-06-30"), ("jul01_end", ev["game_date"] > f"{season}-06-30")]
    groups += [(m_, month == m_) for m_ in sorted(month.unique())]
    groups += [("sp_bf_lt100", bf < 100), ("sp_bf_100_299", (bf >= 100) & (bf < 300)),
               ("sp_bf_300_499", (bf >= 300) & (bf < 500)), ("sp_bf_ge500", bf >= 500),
               ("batter_away", ev["batter_home"] == 0), ("batter_home", ev["batter_home"] == 1)]
    stab = {}
    for lab, mask in groups:
        sub = ev[mask]
        stab[lab] = {"dates": int(sub["game_date"].nunique()), "rows": int(len(sub)),
                     **{f"{a_}_minus_{b_}_pp": F.paired(sub, a_, b_, 10)["diff_pp"] for a_, b_ in pairs},
                     **{f"ll_gain_{a_}_vs_{b_}": F.mean_ll(sub[a_], sub["h1"]) - F.mean_ll(sub[b_], sub["h1"])
                        for a_, b_ in pairs}}
    res["stability_K10"] = stab
    # ---- world-model diagnostics
    wm = {}
    allq = np.vstack([q_sp, q_pen])
    wm["invariants"] = {"max_abs_sum_minus_1": float(np.abs(allq.sum(1) - 1).max()), "any_nonfinite": bool(~np.isfinite(allq).all()),
                        "min_q": dict(zip(CATS, map(float, allq.min(0)))), "max_q": dict(zip(CATS, map(float, allq.max(0)))),
                        "h_E1_range": [float(h.min()), float(h.max())], "E1_raw_range": [float(ev["E1_raw"].min()), float(ev["E1_raw"].max())]}
    r26 = pa_rows(season, pa6, T, T6)
    Lb26, Lp26, sh26, L26 = pa_design(r26, kz)
    Q26 = probs(theta, Lb26, Lp26, sh26)
    Y26 = r26[ECOLS].to_numpy(float)
    Lstd = L26 / L26.sum(1, keepdims=True)
    y26 = r26["hit"].to_numpy()
    D26 = F.design(r26, f2["k"])
    c1a = f2["coef"]["CH1a"]
    p0_pa = F.expit(c1a["intercept"] + sum(c1a[x] * D26[x] for x in ("Lb", "Lp", "same_hand")))
    Q26_np = probs(theta, Lb26, np.zeros_like(Lp26), sh26)
    wm["pa_level_2026_actual_pitcher"] = {
        "n_pa": int(len(r26)),
        "multiclass_logloss": {"E1": float(-np.mean(np.log((Q26 * Y26).sum(1)))),
                               "E1_nopitcher_diag": float(-np.mean(np.log((Q26_np * Y26).sum(1)))),
                               "league_season_to_date": float(-np.mean(np.log((Lstd * Y26).sum(1))))},
        "hit_logloss": {"E1": -F.mean_ll(Q26[:, :4].sum(1), y26), "P0_pa_model": -F.mean_ll(np.asarray(p0_pa), y26),
                        "E1_nopitcher_diag": -F.mean_ll(Q26_np[:, :4].sum(1), y26),
                        "league_season_to_date": -F.mean_ll(Lstd[:, :4].sum(1), y26)},
        "k_logloss": {"E1": -F.mean_ll(Q26[:, CATS.index("K")], Y26[:, CATS.index("K")]),
                      "league_season_to_date": -F.mean_ll(Lstd[:, CATS.index("K")], Y26[:, CATS.index("K")])},
        "per_category_mean_pred_vs_actual": {c: [float(Q26[:, j].mean()), float(Y26[:, j].mean())] for j, c in enumerate(CATS)}}
    cal = {}
    for lab, p_, y_ in (("hit", Q26[:, :4].sum(1), y26), ("K", Q26[:, CATS.index("K")], Y26[:, CATS.index("K")])):
        dec = pd.qcut(p_, 10, labels=False, duplicates="drop")
        cal[lab] = pd.DataFrame({"p": p_, "y": y_, "d": dec}).groupby("d").agg(pred=("p", "mean"), act=("y", "mean")).round(4).to_dict("records")
    wm["pa_level_calibration_deciles"] = cal
    # opponent information: share of hit-logit variance from the pitcher channel (starter, player-game rows)
    lh = lambda q: np.log(q[:, :4].sum(1) / (1 - q[:, :4].sum(1)))                     # noqa: E731
    e_ = attach6(ev, T6, "opp_sp")
    Lg = league_rates(e_)
    Lp_sp = talent(e_, "p6_", kz["pitcher"], Lg)
    sh_sp = np.where(ev["switch"] == 1, 0.0, (ev["stand"] == ev["p_throws_sp"]).astype(float))
    full = lh(probs(theta, Lb, Lp_sp, sh_sp))
    nop = lh(probs(theta, Lb, np.zeros_like(Lp_sp), sh_sp))
    nob = lh(probs(theta, np.zeros_like(Lb), Lp_sp, sh_sp))
    wm["opponent_information"] = {
        "var_hit_logit_starter": float(np.var(full)),
        "share_var_from_pitcher_channel": float(np.var(full - nop) / np.var(full)),
        "share_var_from_batter_channel": float(np.var(full - nob) / np.var(full)),
        "corr_E1_vs_E1_nopitcher_diag": float(np.corrcoef(ev["E1"], ev["E1_nopitcher_diag"])[0, 1]),
        "K10_rate_E1_nopitcher_diag": float(F.topk_rates(ev, "E1_nopitcher_diag", 10).mean()),
        "note": "diagnostic only; not a candidate model"}
    wm["rank"] = {"spearman_E1_P0": float(ev[["E1", "P0"]].rank().corr().iloc[0, 1]),
                  "spearman_E1_CH0": float(ev[["E1", "CH0"]].rank().corr().iloc[0, 1]),
                  "spearman_P0_CH0": float(ev[["P0", "CH0"]].rank().corr().iloc[0, 1])}
    pq = {}
    for n in ("CH0", "P0", "P0_raw", "E1", "E1_raw"):
        p_ = ev[n].to_numpy()
        pq[n] = {"logloss": -F.mean_ll(p_, ev["h1"].to_numpy()), "brier": float(np.mean((p_ - ev["h1"]) ** 2)),
                 "mean_pred": float(p_.mean())}
        dec = pd.qcut(ev[n], 10, labels=False, duplicates="drop")
        pq[n]["calibration_deciles"] = ev.groupby(dec).agg(pred=(n, "mean"), act=("h1", "mean"), n=("h1", "size")).round(4).to_dict("records")
    wm["player_game_probability_quality"] = pq
    wm["actual_h1_rate"] = float(ev["h1"].mean())
    res["world_model"] = wm
    # ---- market subset (secondary, descriptive)
    ev["player_norm"] = ev["batter"].map(lambda i: F.norm((info.get(int(i)) or {}).get("name", "")))
    props = load_props_pinned(sorted(ev["game_date"].unique()))
    sched = F.schedule_games(sorted(props["game_date"].unique())) if len(props) else pd.DataFrame()
    m, ident = F.match_market(props, sched, ev, pp)
    mres = {"props_source": f"data/props @ pinned main {PROPS_SHA}", "identity": ident,
            "schedule_identity_sha256": F.sha256_file(os.path.join(F.DATA, "schedule_identity.json")),
            "devig": "one-sided FanDuel price, production assumed hold (pp.devig): APPROXIMATE", "rows": int(len(m))}
    if len(m):
        mres["dates"] = int(m["game_date"].nunique())
        mres["logloss"] = {n: -F.mean_ll(m[n].to_numpy(), m["h1"].to_numpy()) for n in ("mkt_fair", *names)}
        for K in (3, 5):
            row = {"counts": F.topk_counts(m, K), "rates": {n: float(F.topk_rates(m, n, K).mean()) for n in ("mkt_fair", *names)}}
            for a_, b_ in (("E1", "P0"), ("E1", "mkt_fair"), ("P0", "mkt_fair")):
                row[f"{a_}_minus_{b_}"] = F.paired(m, a_, b_, K)
            mres[f"topK{K}"] = row
    res["market"] = mres
    keep = ["game_date", "game_pk", "batter", "bat_side", "switch", "stand", "p_throws_sp", "slot", "opp_sp", "fld_team",
            "batter_home", "s_bf", "proj_pa", "w_sp", "lg_h1", "h1", "CH0", "P0", "P0_raw", "p_pa_P0", "h_E1",
            "E1_raw", "E1", "E1_nopitcher_diag", *[f"q_{c}" for c in CATS]]
    pred = os.path.join(OUT, f"predictions_{season}.csv.gz")
    ev[keep].to_csv(pred, index=False, compression="gzip")
    res["artifacts"] = {"predictions_sha256": F.sha256_file(pred)}
    json.dump(res, open(out_json, "w"), indent=1, default=float)
    print(json.dumps({k_: res[k_] for k_ in ("population", "fidelity_gate", "verdict_K10_E1_vs_P0")}, indent=1, default=float))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("fit", "evaluate"))
    a = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    stage_fit() if a.stage == "fit" else stage_evaluate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
