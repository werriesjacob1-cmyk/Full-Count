#!/usr/bin/env python3
"""FC-MLB-002: batter x pitcher interaction challenger for Hits 1+ (DEVELOPMENT evidence).

Acceptance criteria: engineering/ops/TASKS/FC-MLB-002.md on claude/full-count-ops-state, frozen at the claim
commit cb1a338b28 (file sha256 5779cfe9...). Research only. The champion is computed with FULL COUNT's own
production functions (prop_probability, generate_picks.project_batter_pa) imported unchanged from a checkout
of main 8b68985234.

Point-in-time rule: every feature for a game on date D is a season-to-date sum over games with date < D
(as-of merge, allow_exact_matches=False), so same-day games -- including doubleheader game 1 -- never leak.

  python3 fc_mlb_002.py pa        Statcast pitch CSVs -> PA table + slim pitch table (parquet)
  python3 fc_mlb_002.py fit       shrinkage (time split inside 2025) + PA-level logistic fits on 2025; frozen
  python3 fc_mlb_002.py evaluate  ONE frozen 2026 evaluation: CH0 vs CH0b/CH1a/CH1, equal top-K per date
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata

import numpy as np
import pandas as pd

DATA = os.environ.get("FC002_DATA", "/tmp/claude-0/mlbdata")
CHAMP = os.environ.get("FC002_CHAMP", "/tmp/claude-0/champ_main")
CHAMP_SHA = "8b689852342bd49014b86ad637e64002ad6f47b4"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")

HIT_TB = {"single": 1, "double": 2, "triple": 3, "home_run": 4}
NON_PA = re.compile(r"^(caught_stealing|pickoff|stolen_base|wild_pitch|passed_ball|runner_double_play|"
                    r"other_advance|game_advisory|truncated_pa|ejection|defensive_indiff)")
FAMILY = {**{t: "FB" for t in ("FF", "SI", "FC", "FA")},
          **{t: "BR" for t in ("SL", "ST", "SV", "CU", "KC", "CS")},
          **{t: "OS" for t in ("CH", "FS", "FO", "SC", "KN", "EP")}}
FAMS = ("FB", "BR", "OS")
COLS = ["game_date", "game_pk", "game_type", "at_bat_number", "pitch_number", "batter", "pitcher", "events",
        "stand", "p_throws", "inning_topbot", "home_team", "away_team", "pitch_type"]
START = {2025: "2025-04-15", 2026: "2026-04-15"}
MIN_PA = 30                      # champion's batter_pa_composition min_pa
K_GRID = dict(kb=[100, 200, 400], kp=[150, 300, 600], kf=[40, 100, 250], ku=[100, 400])
SP_PRIOR_STARTS = 3              # w_sp: starter BF/start shrunk toward league mean with 3 pseudo-starts
TOPK = (5, 10, 20)
BOOT = 2000
MODELS = {"CH0b": ["Lb"], "CH1a": ["Lb", "Lp", "same_hand"], "CH1": ["Lb", "Lp", "same_hand", "M"]}


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def expit(x):
    return 1 / (1 + np.exp(-np.asarray(x, float)))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------------ stage: pa
def stage_pa():
    parts = []
    for f in sorted(glob.glob(os.path.join(DATA, "statcast", "*.csv.gz"))):
        d = pd.read_csv(f, usecols=lambda c: c in COLS, low_memory=False)
        if len(d):
            parts.append(d[d["game_type"] == "R"])
    p = pd.concat(parts, ignore_index=True)
    p["season"] = p["game_date"].str[:4].astype(int)
    p["bat_team"] = np.where(p["inning_topbot"] == "Top", p["away_team"], p["home_team"])
    p["fld_team"] = np.where(p["inning_topbot"] == "Top", p["home_team"], p["away_team"])
    p["fam"] = p["pitch_type"].map(FAMILY)
    last = p.sort_values(["game_pk", "at_bat_number", "pitch_number"]).groupby(
        ["game_pk", "at_bat_number"], as_index=False).tail(1)
    pa = last[last["events"].notna() & ~last["events"].astype(str).str.match(NON_PA)].copy()
    pa["hit"] = pa["events"].isin(list(HIT_TB)).astype(int)
    pa["tb"] = pa["events"].map(HIT_TB).fillna(0).astype(int)
    for k in HIT_TB:
        pa[k] = (pa["events"] == k).astype(int)
    first = pa.sort_values("at_bat_number").groupby(["game_pk", "bat_team"], as_index=False).first()
    pa = pa.merge(first[["game_pk", "bat_team", "pitcher"]].rename(columns={"pitcher": "opp_sp"}),
                  on=["game_pk", "bat_team"], how="left")
    pa["vs_starter"] = (pa["pitcher"] == pa["opp_sp"]).astype(int)
    order = pa.sort_values("at_bat_number").drop_duplicates(["game_pk", "bat_team", "batter"])[
        ["game_pk", "bat_team", "batter"]].copy()
    order["slot"] = order.groupby(["game_pk", "bat_team"]).cumcount() + 1
    pa = pa.merge(order, on=["game_pk", "bat_team", "batter"], how="left")
    pa["same_hand"] = (pa["stand"] == pa["p_throws"]).astype(int)
    pa["one"] = 1
    keep = ["season", "game_date", "game_pk", "at_bat_number", "batter", "pitcher", "stand", "p_throws", "bat_team",
            "fld_team", "events", "fam", "hit", "tb", *HIT_TB, "opp_sp", "vs_starter", "slot", "same_hand", "one"]
    pa[keep].to_parquet(os.path.join(DATA, "pa.parquet"))
    p = p.merge(pa[["game_pk", "at_bat_number", "vs_starter"]], on=["game_pk", "at_bat_number"], how="left")
    p[["season", "game_date", "pitcher", "stand", "fam", "fld_team", "vs_starter"]].to_parquet(
        os.path.join(DATA, "pitches_slim.parquet"))
    print(json.dumps({"pitches": int(len(p)), "pa": int(len(pa)), "by_season": pa.groupby("season").size().to_dict(),
                      "dates": [pa["game_date"].min(), pa["game_date"].max()]}))


# ------------------------------------------------------------------ point-in-time tables
def through(daily, keys, cols):
    """Season-to-date cumulative sums THROUGH each date (one row per season/keys/date)."""
    d = daily.groupby(["season", *keys, "game_date"], as_index=False)[cols].sum().sort_values(
        ["season", *keys, "game_date"])
    d[cols] = d.groupby(["season", *keys], sort=False)[cols].cumsum()
    d["game_date"] = pd.to_datetime(d["game_date"])
    return d


def asof(table, keys, cols, targets, prefix):
    """Values as of the last date strictly before each target's game_date (0 if none)."""
    t = targets[["season", *keys, "game_date"]].drop_duplicates().copy()
    t["game_date"] = pd.to_datetime(t["game_date"])
    m = pd.merge_asof(t.sort_values("game_date"), table.sort_values("game_date"), on="game_date",
                      by=["season", *keys], allow_exact_matches=False)
    m[cols] = m[cols].fillna(0)
    m = m.rename(columns={c: prefix + c for c in cols})
    m["game_date"] = m["game_date"].dt.strftime("%Y-%m-%d")
    return m


def tables(pa, pit):
    for f in FAMS:
        pa[f"pa_{f}"] = (pa["fam"] == f).astype(int)
        pa[f"h_{f}"] = pa[f"pa_{f}"] * pa["hit"]
        pit[f"n_{f}"] = (pit["fam"] == f).astype(int)
        for h in "LR":
            pit[f"n_{f}_{h}"] = ((pit["fam"] == f) & (pit["stand"] == h)).astype(int)
    T = {}
    T["bcols"] = ["one", "hit", *HIT_TB, *[f"pa_{f}" for f in FAMS], *[f"h_{f}" for f in FAMS]]
    T["b"] = through(pa, ["batter"], T["bcols"])
    T["pcols"] = ["one", "hit", *[f"n_{f}" for f in FAMS]]
    pd_ = pd.concat([pa[["season", "pitcher", "stand", "game_date", "one", "hit"]],
                     pit[pit["fam"].notna()][["season", "pitcher", "stand", "game_date", *[f"n_{f}" for f in FAMS]]]])
    T["p"] = through(pd_.fillna(0), ["pitcher", "stand"], T["pcols"])
    rp = pd.concat([pa[pa["vs_starter"] == 0][["season", "fld_team", "stand", "game_date", "one", "hit"]],
                    pit[(pit["vs_starter"] == 0) & pit["fam"].notna()][
                        ["season", "fld_team", "stand", "game_date", *[f"n_{f}" for f in FAMS]]]])
    T["r"] = through(rp.fillna(0), ["fld_team", "stand"], T["pcols"])
    st = pa[pa["vs_starter"] == 1].groupby(["season", "pitcher", "game_date", "game_pk"], as_index=False)["one"].sum()
    st = st.rename(columns={"one": "bf"}).assign(starts=1)
    T["s"] = through(st, ["pitcher"], ["bf", "starts"])
    bg = batter_games(pa)
    lg = pd.concat([pa[["season", "game_date", "one", "hit", *[f"pa_{f}" for f in FAMS], *[f"h_{f}" for f in FAMS]]],
                    pit[pit["fam"].notna()][["season", "game_date", *[f"n_{f}_{h}" for f in FAMS for h in "LR"]]],
                    bg[bg["slot"] <= 9][["season", "game_date", "h1"]].assign(bgn=1),
                    pa.drop_duplicates(["game_pk", "bat_team"])[["season", "game_date"]].assign(team_games=1),
                    st[["season", "game_date", "bf", "starts"]].rename(columns={"bf": "sp_bf", "starts": "sp_starts"}),
                    pa[pa["vs_starter"] == 0].assign(
                        **{f"rel_n_{h}": lambda x, h=h: (x["stand"] == h).astype(int) for h in "LR"},
                        **{f"rel_same_{h}": lambda x, h=h: ((x["stand"] == h) & (x["same_hand"] == 1)).astype(int)
                           for h in "LR"})[["season", "game_date", *[f"rel_{t}_{h}" for t in ("n", "same") for h in "LR"]]]])
    lg = lg.fillna(0)
    lg["k"] = 0
    T["lcols"] = [c for c in lg.columns if c not in ("season", "game_date", "k")]
    T["l"] = through(lg, ["k"], T["lcols"])
    return T, bg


def batter_games(pa):
    g = pa.groupby(["season", "game_date", "game_pk", "bat_team", "fld_team", "batter", "stand"], as_index=False).agg(
        slot=("slot", "first"), opp_sp=("opp_sp", "first"), hits=("hit", "sum"), tb=("tb", "sum"),
        n_pa=("one", "sum"), p_throws_sp=("p_throws", "first"))
    g["h1"] = (g["hits"] >= 1).astype(int)
    g["h2"] = (g["hits"] >= 2).astype(int)
    g["tb2"] = (g["tb"] >= 2).astype(int)
    return g


def attach(rows, T, pitcher_col="pitcher"):
    r = rows.copy()
    r["k"] = 0
    r = r.merge(asof(T["l"], ["k"], T["lcols"], r, "c_"), on=["season", "k", "game_date"], how="left")
    r = r.merge(asof(T["b"], ["batter"], T["bcols"], r, "b_"), on=["season", "batter", "game_date"], how="left")
    pk = r[["season", pitcher_col, "stand", "game_date"]].rename(columns={pitcher_col: "pitcher"})
    pv = asof(T["p"], ["pitcher", "stand"], T["pcols"], pk, "p_").rename(columns={"pitcher": pitcher_col})
    r = r.merge(pv, on=["season", pitcher_col, "stand", "game_date"], how="left")
    r = r.merge(asof(T["r"], ["fld_team", "stand"], T["pcols"], r, "r_"),
                on=["season", "fld_team", "stand", "game_date"], how="left")
    return r


def design(r, k, src="p_"):
    """Shrunk predictors. src='p_' = the pitcher in r[p_*]; src='r_' = the team bullpen aggregate."""
    lam = r["c_hit"] / r["c_one"]
    Lb = logit((r["b_hit"] + k["kb"] * lam) / (r["b_one"] + k["kb"]))
    Lp = logit((r[f"{src}hit"] + k["kp"] * lam) / (r[f"{src}one"] + k["kp"]))
    ntot = sum(r[f"{src}n_{f}"] for f in FAMS)
    hand_tot = sum(np.where(r["stand"] == "L", r[f"c_n_{g}_L"], r[f"c_n_{g}_R"]) for g in FAMS)
    M = np.zeros(len(r))
    for f in FAMS:
        lam_f = r[f"c_h_{f}"] / r[f"c_pa_{f}"]
        e_bf = expit(Lb + logit(lam_f) - logit(lam))                         # no-interaction expectation
        r_bf = (r[f"b_h_{f}"] + k["kf"] * e_bf) / (r[f"b_pa_{f}"] + k["kf"])  # batter on family f, shrunk to it
        lg_u = np.where(r["stand"] == "L", r[f"c_n_{f}_L"], r[f"c_n_{f}_R"]) / hand_tot
        u = (r[f"{src}n_{f}"] + k["ku"] * lg_u) / (ntot + k["ku"])           # pitcher usage vs this hand, shrunk
        M = M + u * (logit(r_bf) - logit(e_bf))
    if "same_hand" in r:                      # training rows: the actual pitcher's hand
        sh = r["same_hand"].to_numpy()
    elif src == "p_":                         # evaluation, starter: his known hand
        sh = (r["stand"] == r["p_throws_sp"]).astype(int).to_numpy()
    else:                                     # evaluation, bullpen: league same-hand share of relief PAs (as of D-1)
        sh = np.where(r["stand"] == "L", r["c_rel_same_L"] / r["c_rel_n_L"], r["c_rel_same_R"] / r["c_rel_n_R"])
    return pd.DataFrame({"Lb": np.asarray(Lb), "Lp": np.asarray(Lp), "M": np.asarray(M), "same_hand": sh})


def fit_lr(X, y):
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(C=1e6, max_iter=2000).fit(X, y)


def mean_ll(p, y):
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return float(np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def load():
    pa = pd.read_parquet(os.path.join(DATA, "pa.parquet"))
    pit = pd.read_parquet(os.path.join(DATA, "pitches_slim.parquet"))
    return pa, pit


# ------------------------------------------------------------------ stage: fit (2025 only)
def stage_fit():
    pa, pit = load()
    T, _ = tables(pa, pit)
    tr = pa[(pa["season"] == 2025) & (pa["game_date"] >= START[2025])].copy()
    tr = attach(tr, T)
    tr = tr[tr["b_one"] >= MIN_PA].reset_index(drop=True)
    y = tr["hit"].to_numpy()
    A = (tr["game_date"] <= "2025-07-15").to_numpy()
    grid = []
    for kb in K_GRID["kb"]:
        for kp in K_GRID["kp"]:
            for kf in K_GRID["kf"]:
                for ku in K_GRID["ku"]:
                    k = dict(kb=kb, kp=kp, kf=kf, ku=ku)
                    X = design(tr, k)[MODELS["CH1"]].to_numpy()
                    m = fit_lr(X[A], y[A])
                    grid.append((mean_ll(m.predict_proba(X[~A])[:, 1], y[~A]), k))
    grid.sort(key=lambda g: -g[0])
    k = grid[0][1]
    D = design(tr, k)
    coef, split = {}, {}
    for name, cols in MODELS.items():
        m = fit_lr(D[cols].to_numpy(), y)
        coef[name] = {"intercept": float(m.intercept_[0]), **{c: float(v) for c, v in zip(cols, m.coef_[0])}}
        mA = fit_lr(D[cols].to_numpy()[A], y[A])
        split[name] = {"holdout_ll_jul16_sep": mean_ll(mA.predict_proba(D[cols].to_numpy()[~A])[:, 1], y[~A]),
                       "coef_first_half": {"intercept": float(mA.intercept_[0]),
                                           **{c: float(v) for c, v in zip(cols, mA.coef_[0])}}}
    frozen = {"label": "FC-MLB-002 frozen 2025 fit", "k": k, "coef": coef, "n_pa_2025": int(len(tr)),
              "grid_top5": [(round(g[0], 6), g[1]) for g in grid[:5]], "split_check_2025": split,
              "base_rate_2025": float(y.mean())}
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "FROZEN_FIT_2025.json")
    json.dump(frozen, open(path, "w"), indent=1)
    print(json.dumps({"k": k, "coef": coef, "split": split, "sha256": sha256_file(path)}, indent=1))


# ------------------------------------------------------------------ market (FanDuel Over 0.5 Hits, pregame)
def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'’]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def people_names(ids):
    path = os.path.join(DATA, "people.json")
    cache = json.load(open(path)) if os.path.exists(path) else {}
    need = [i for i in ids if str(i) not in cache]
    import requests
    for i in range(0, len(need), 150):
        chunk = need[i:i + 150]
        r = requests.get("https://statsapi.mlb.com/api/v1/people",
                         params={"personIds": ",".join(map(str, chunk))}, timeout=60)
        for pp_ in r.json().get("people", []):
            cache[str(pp_["id"])] = pp_["fullName"]
    json.dump(cache, open(path, "w"))
    return {int(k): v for k, v in cache.items()}


def market_rows(pp, dates, repo="/home/user/Full-Count"):
    rows = []
    sha = subprocess.check_output(["git", "-C", repo, "rev-parse", "origin/main"]).decode().strip()
    for d in dates:
        try:
            raw = subprocess.check_output(["git", "-C", repo, "show", f"{sha}:data/props/props_{d}.json"],
                                          stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            continue
        js = json.loads(raw)
        for s in js.get("snapshots", []):
            for x in s.get("rows", []):
                if x.get("stat") == "hits" and x.get("needs") == 1 and not x.get("in_play") \
                        and x.get("taken_at") and x.get("start_time") and x["taken_at"] < x["start_time"].replace("Z", "+00:00"):
                    rows.append({"game_date": d, "player_norm": norm(x["player"]), "start_time": x["start_time"],
                                 "taken_at": x["taken_at"], "american": x["american"]})
    if not rows:
        return pd.DataFrame(), sha
    m = pd.DataFrame(rows).sort_values("taken_at").groupby(["game_date", "player_norm", "start_time"], as_index=False).last()
    m = m[~m.duplicated(["game_date", "player_norm"], keep=False)]          # doubleheaders: drop (ambiguous)
    m["mkt_implied"] = [pp.implied_probability(a) for a in m["american"]]
    m["mkt_fair"] = [pp.devig(i) for i in m["mkt_implied"]]
    return m, sha


# ------------------------------------------------------------------ stage: evaluate (ONE frozen run on 2026)
def boot_ci(daily_diff, seed=7):
    rng = np.random.default_rng(seed)
    v = np.asarray(daily_diff, float)
    bs = [rng.choice(v, len(v), replace=True).mean() for _ in range(BOOT)]
    return float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))


def topk_rates(df, col, K):
    s = df.sort_values(["game_date", col, "batter"], ascending=[True, False, True]).groupby("game_date").head(K)
    return s.groupby("game_date")["h1"].mean()


def stage_selfcheck():
    """Bug check on the TRAINING season (2025, in-sample): same code path, never the frozen result."""
    return stage_evaluate(season=2025, out_name="SELFCHECK_2025_INSAMPLE.json", market=False)


def stage_evaluate(season=2026, out_name="RESULTS.json", market=True):
    out_json = os.path.join(OUT, out_name)
    if os.path.exists(out_json) and out_name == "RESULTS.json":
        raise SystemExit("RESULTS.json exists: the frozen evaluation runs ONCE (criteria §10)")
    frozen_path = os.path.join(OUT, "FROZEN_FIT_2025.json")
    frozen = json.load(open(frozen_path))
    k = frozen["k"]
    sys.path.insert(0, CHAMP)
    import prop_probability as pp          # noqa: E402  champion functions, unchanged
    import generate_picks as gp            # noqa: E402
    champ_head = subprocess.check_output(["git", "-C", CHAMP, "rev-parse", "HEAD"]).decode().strip()
    assert champ_head == CHAMP_SHA, champ_head
    pa, pit = load()
    T, bg = tables(pa, pit)
    ev = bg[(bg["season"] == season) & (bg["game_date"] >= START[season]) & (bg["slot"] <= 9)].copy()
    ev = attach(ev, T, pitcher_col="opp_sp")
    ev = ev[ev["b_one"] >= MIN_PA].reset_index(drop=True)
    sp = asof(T["s"], ["pitcher"], ["bf", "starts"], ev.rename(columns={"opp_sp": "pitcher"}), "s_").rename(
        columns={"pitcher": "opp_sp"})
    ev = ev.merge(sp, on=["season", "opp_sp", "game_date"], how="left")
    lg_bf = ev["c_sp_bf"] / ev["c_sp_starts"]
    team_pa = ev["c_one"] / ev["c_team_games"]
    ev["w_sp"] = np.minimum(1.0, ((ev["s_bf"] + SP_PRIOR_STARTS * lg_bf) / (ev["s_starts"] + SP_PRIOR_STARTS)) / team_pa)
    ev["proj_pa"] = [gp.project_batter_pa(int(s), None) for s in ev["slot"]]
    ev["lg_h1"] = ev["c_h1"] / ev["c_bgn"]
    # ---- CH0: production hits path, unchanged functions
    comp = lambda r, c: r[f"b_{c}"] / r["b_one"]                                         # noqa: E731
    ev["p_pa_CH0"] = (ev["b_hit"] / ev["b_one"])
    mod0, mod0_h2, mod0_tb2 = [], [], []
    for _, r in ev.iterrows():
        dist = pp.pa_outcome_distribution(singles_rate=comp(r, "single"), double_rate=comp(r, "double"),
                                          triple_rate=comp(r, "triple"), hr_rate=comp(r, "home_run"))
        mod0.append(pp.p_at_least_hits(1, dist, r["proj_pa"]))
        mod0_h2.append(pp.p_at_least_hits(2, dist, r["proj_pa"]))
        mod0_tb2.append(pp.p_at_least_total_bases(2, dist, r["proj_pa"]))
    ev["CH0"] = 0.5 * ev["lg_h1"] + 0.5 * np.array(mod0)
    ev["CH0_h2"], ev["CH0_tb2"] = mod0_h2, mod0_tb2
    # ---- challengers: replace only the per-PA hit probability
    Dsp, Dpen = design(ev, k, "p_"), design(ev, k, "r_")
    for name, cols in MODELS.items():
        c = frozen["coef"][name]
        z = lambda D: c["intercept"] + sum(c[x] * D[x] for x in cols)                     # noqa: E731
        p_pa = ev["w_sp"] * expit(z(Dsp)) + (1 - ev["w_sp"]) * expit(z(Dpen))
        ev[f"p_pa_{name}"] = p_pa
        modelled = [pp.p_at_least_hits(1, {0: 1 - p, 1: p}, n) for p, n in zip(p_pa, ev["proj_pa"])]
        ev[name] = 0.5 * ev["lg_h1"] + 0.5 * np.array(modelled)
        if name == "CH1":   # diagnostics: scale the champion's hit composition to CH1's per-PA hit prob
            h2, tb2 = [], []
            for (_, r), p in zip(ev.iterrows(), p_pa):
                base = r["b_hit"] / r["b_one"]
                s = p / base if base > 0 else 1.0
                dist = pp.pa_outcome_distribution(singles_rate=comp(r, "single") * s, double_rate=comp(r, "double") * s,
                                                  triple_rate=comp(r, "triple") * s, hr_rate=comp(r, "home_run") * s)
                h2.append(pp.p_at_least_hits(2, dist, r["proj_pa"]))
                tb2.append(pp.p_at_least_total_bases(2, dist, r["proj_pa"]))
            ev["CH1_h2"], ev["CH1_tb2"] = h2, tb2
    names = ["CH0", *MODELS]
    res = {"label": ("DEVELOPMENT_EVIDENCE (2026 outcomes previously visible to the project; not confirmatory)"
                     if season == 2026 else "SELFCHECK on 2025 training season, IN-SAMPLE: bug check only, not evidence"),
           "criteria": "engineering/ops/TASKS/FC-MLB-002.md @ claude/full-count-ops-state cb1a338b28 (sha256 5779cfe9...)",
           "champion_code": CHAMP_SHA, "frozen_fit_sha256": sha256_file(frozen_path), "k": k,
           "population": {"batter_games": int(len(ev)), "dates": int(ev["game_date"].nunique()),
                          "first": ev["game_date"].min(), "last": ev["game_date"].max(),
                          "h1_base_rate": float(ev["h1"].mean())}}
    # ---- primary: equal top-K per date
    prim = {}
    for K in TOPK:
        base = topk_rates(ev, "CH0", K)
        row = {"CH0_rate": float(base.mean())}
        for name in MODELS:
            r = topk_rates(ev, name, K)
            diff = (r - base).dropna()
            lo, hi = boot_ci(diff.to_numpy())
            row[name] = {"rate": float(r.mean()), "diff_pp": 100 * float(diff.mean()), "ci95_pp": [100 * lo, 100 * hi],
                         "picks_each": int(K * len(diff))}
        prim[f"K{K}"] = row
    d = prim["K10"]["CH1"]
    verdict = ("IMPROVES" if d["diff_pp"] >= 2.0 and d["ci95_pp"][0] > 0 else
               "NO_GAIN" if d["ci95_pp"][0] <= 0 <= d["ci95_pp"][1] and abs(d["diff_pp"]) < 1.0 else "INCONCLUSIVE")
    res["primary"] = prim
    res["verdict_K10_CH1_vs_CH0"] = verdict
    # ---- stability: halves
    halves = {}
    for lab, mask in (("apr15_jun30", ev["game_date"] <= "2026-06-30"), ("jul01_end", ev["game_date"] > "2026-06-30")):
        sub = ev[mask]
        b0, b1 = topk_rates(sub, "CH0", 10), topk_rates(sub, "CH1", 10)
        halves[lab] = 100 * float((b1 - b0).mean())
    res["stability_K10_diff_pp_by_half"] = halves
    # ---- secondary: probability quality
    sec = {}
    for name in names:
        p = ev[name].to_numpy()
        sec[name] = {"logloss": -mean_ll(p, ev["h1"].to_numpy()), "brier": float(np.mean((p - ev["h1"]) ** 2)),
                     "mean_pred": float(p.mean())}
    for tgt in ("h2", "tb2"):
        for name in ("CH0", "CH1"):
            p = ev[f"{name}_{tgt}"].to_numpy()
            sec[f"{name}_{tgt}"] = {"logloss": -mean_ll(p, ev[tgt].to_numpy()), "mean_pred": float(p.mean()),
                                    "actual": float(ev[tgt].mean())}
    ev["dec"] = pd.qcut(ev["CH1"], 10, labels=False, duplicates="drop")
    sec["calibration_CH1_deciles"] = ev.groupby("dec").agg(pred=("CH1", "mean"), act=("h1", "mean"), n=("h1", "size")).round(4).to_dict("records")
    ev["dec0"] = pd.qcut(ev["CH0"], 10, labels=False, duplicates="drop")
    sec["calibration_CH0_deciles"] = ev.groupby("dec0").agg(pred=("CH0", "mean"), act=("h1", "mean"), n=("h1", "size")).round(4).to_dict("records")
    sub = {}
    for lab, mask in (("LHB", ev["stand"] == "L"), ("RHB", ev["stand"] == "R"),
                      ("same_hand_sp", ev["stand"] == ev["p_throws_sp"]), ("opp_hand_sp", ev["stand"] != ev["p_throws_sp"])):
        s = ev[mask]
        sub[lab] = {"n": int(len(s)), "ll_gain_CH1_vs_CH0": float(mean_ll(s["CH1"], s["h1"]) - mean_ll(s["CH0"], s["h1"]))}
    sec["subgroups"] = sub
    sec["corr_CH0_CH1"] = float(np.corrcoef(ev["CH0"], ev["CH1"])[0, 1])
    sec["topK10_overlap_share"] = float(np.mean([
        len(set(g.nlargest(10, "CH0")["batter"]) & set(g.nlargest(10, "CH1")["batter"])) / 10
        for _, g in ev.groupby("game_date")]))
    res["secondary"] = sec
    if not market:
        json.dump(res, open(out_json, "w"), indent=1, default=float)
        print(json.dumps({k_: res[k_] for k_ in ("population", "primary", "verdict_K10_CH1_vs_CH0")}, indent=1, default=float))
        return
    # ---- market subset
    names_map = people_names(sorted(ev["batter"].unique().tolist()))
    ev["player_norm"] = ev["batter"].map(lambda i: norm(names_map.get(int(i), "")))
    ev_dh = ev[~ev.duplicated(["game_date", "batter"], keep=False)]
    mk, props_sha = market_rows(pp, sorted(ev["game_date"].unique()))
    mres = {"props_source": f"data/props @ origin/main {props_sha}", "rows_with_price": 0}
    if len(mk):
        m = ev_dh.merge(mk[["game_date", "player_norm", "mkt_fair", "mkt_implied", "american", "taken_at"]],
                        on=["game_date", "player_norm"], how="inner")
        mres["rows_with_price"] = int(len(m))
        mres["dates"] = int(m["game_date"].nunique())
        y = m["h1"].to_numpy()
        mres["logloss"] = {n: -mean_ll(m[n].to_numpy(), y) for n in ("mkt_fair", "CH0", "CH1")}
        mres["mean"] = {n: float(m[n].mean()) for n in ("mkt_fair", "CH0", "CH1")} | {"actual": float(y.mean())}
        for n in ("CH0", "CH1"):
            X = np.column_stack([logit(m["mkt_fair"]), logit(m[n]) - logit(m["mkt_fair"])])
            fr = fit_lr(X, y)
            rng = np.random.default_rng(11)
            bs = []
            dd = m["game_date"].unique()
            for _ in range(500):
                pick = rng.choice(dd, len(dd), replace=True)
                mm = pd.concat([m[m["game_date"] == x] for x in pick])
                Xb = np.column_stack([logit(mm["mkt_fair"]), logit(mm[n]) - logit(mm["mkt_fair"])])
                bs.append(fit_lr(Xb, mm["h1"].to_numpy()).coef_[0][1])
            mres[f"disagreement_coef_{n}"] = {"coef": float(fr.coef_[0][1]), "ci95": [float(np.quantile(bs, .025)),
                                                                                      float(np.quantile(bs, .975))],
                                              "market_coef": float(fr.coef_[0][0])}
            m[f"dis_{n}"] = m[n] - m["mkt_fair"]
        for K in (3, 5):
            b0, b1, bm = topk_rates(m, "CH0", K), topk_rates(m, "CH1", K), topk_rates(m, "mkt_fair", K)
            mres[f"topK{K}"] = {"CH0": float(b0.mean()), "CH1": float(b1.mean()), "market": float(bm.mean()),
                                "CH1_minus_CH0_pp": 100 * float((b1 - b0).mean()),
                                "CH1_minus_market_pp": 100 * float((b1 - bm).mean())}
        m["dis_dec"] = pd.qcut(m["dis_CH1"], 5, labels=False, duplicates="drop")
        mres["CH1_disagreement_quintiles"] = m.groupby("dis_dec").agg(
            dis=("dis_CH1", "mean"), mkt=("mkt_fair", "mean"), ch1=("CH1", "mean"), act=("h1", "mean"),
            n=("h1", "size")).round(4).to_dict("records")
    res["market"] = mres
    # ---- artifacts
    keep = ["game_date", "game_pk", "batter", "stand", "p_throws_sp", "slot", "opp_sp", "proj_pa", "w_sp", "lg_h1",
            "h1", "h2", "tb2", *names, "p_pa_CH0", *[f"p_pa_{n}" for n in MODELS]]
    pred = os.path.join(OUT, f"predictions_{season}.csv.gz")
    ev[keep].to_csv(pred, index=False, compression="gzip")
    res["artifacts"] = {"predictions_sha256": sha256_file(pred),
                        "statcast_manifest_sha256": sha256_file(os.path.join(DATA, "statcast", "MANIFEST.jsonl"))}
    json.dump(res, open(out_json, "w"), indent=1, default=float)
    print(json.dumps({k_: res[k_] for k_ in ("population", "primary", "verdict_K10_CH1_vs_CH0",
                                               "stability_K10_diff_pp_by_half")}, indent=1, default=float))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("pa", "fit", "selfcheck", "evaluate"))
    a = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    {"pa": stage_pa, "fit": stage_fit, "selfcheck": stage_selfcheck, "evaluate": stage_evaluate}[a.stage]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
