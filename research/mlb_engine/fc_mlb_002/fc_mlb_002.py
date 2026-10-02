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


COMMITTED_MANIFEST = os.path.join(HERE, "STATCAST_MANIFEST.jsonl")
PROVENANCE = "PA_PROVENANCE.json"


class ManifestError(Exception):
    pass


def verify_raw_files(statcast_dir, manifest_path=COMMITTED_MANIFEST):
    """REPAIR of Codex finding 4. Every cached raw file must be listed in the COMMITTED manifest and its
    decompressed bytes must match the manifest's byte count and sha256 (the hash of the exact Savant response
    bytes). Unlisted files, missing files, FAIL entries and conflicting duplicate entries all fail closed.
    Returns {date: raw_bytes} for the verified files (the caller parses these exact bytes)."""
    import gzip
    entries = {}
    with open(manifest_path) as fh:
        lines = fh.read().splitlines()
    for line in lines:
        e = json.loads(line)
        if e["bytes"] == "FAIL":
            raise ManifestError(f"manifest records a failed fetch for {e['date']}")
        prev = entries.get(e["date"])
        if prev and (prev["sha256"], prev["bytes"]) != (e["sha256"], e["bytes"]):
            raise ManifestError(f"conflicting manifest entries for {e['date']}")
        entries[e["date"]] = e
    files = {os.path.basename(f)[:-len(".csv.gz")]: f for f in glob.glob(os.path.join(statcast_dir, "*"))
             if f.endswith(".csv.gz")}
    other = [f for f in os.listdir(statcast_dir) if not f.endswith(".csv.gz") and f != "MANIFEST.jsonl"]
    if other:
        raise ManifestError(f"unexpected files in raw dir: {sorted(other)[:5]}")
    extra, missing = sorted(set(files) - set(entries)), sorted(set(entries) - set(files))
    if extra or missing:
        raise ManifestError(f"raw files not in manifest: {extra[:5]}; manifest entries without file: {missing[:5]}")
    out = {}
    for d, f in sorted(files.items()):
        raw = gzip.open(f).read()
        if len(raw) != entries[d]["bytes"] or hashlib.sha256(raw).hexdigest() != entries[d]["sha256"]:
            raise ManifestError(f"raw bytes for {d} do not match the manifest")
        out[d] = raw
    return out


# ------------------------------------------------------------------ stage: pa
def stage_pa():
    import io
    verified = verify_raw_files(os.path.join(DATA, "statcast"))
    parts = []
    for d, raw in verified.items():
        df = pd.read_csv(io.BytesIO(raw), usecols=lambda c: c in COLS, low_memory=False)
        if len(df):
            parts.append(df[df["game_type"] == "R"])
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
    prov = {"raw_files_verified": len(verified), "manifest_sha256": sha256_file(COMMITTED_MANIFEST),
            "pa_parquet_sha256": sha256_file(os.path.join(DATA, "pa.parquet")),
            "pitches_parquet_sha256": sha256_file(os.path.join(DATA, "pitches_slim.parquet")),
            "pitches": int(len(p)), "pa": int(len(pa))}
    json.dump(prov, open(os.path.join(DATA, PROVENANCE), "w"), indent=1)
    print(json.dumps({**prov, "by_season": pa.groupby("season").size().to_dict(),
                      "dates": [pa["game_date"].min(), pa["game_date"].max()]}))


def verified_provenance():
    """The PA/pitch tables must be exactly the ones built from manifest-verified raw bytes."""
    prov = json.load(open(os.path.join(DATA, PROVENANCE)))
    for key, name in (("pa_parquet_sha256", "pa.parquet"), ("pitches_parquet_sha256", "pitches_slim.parquet")):
        if sha256_file(os.path.join(DATA, name)) != prov[key]:
            raise ManifestError(f"{name} is not the table built from verified raw bytes")
    if prov["manifest_sha256"] != sha256_file(COMMITTED_MANIFEST):
        raise ManifestError("committed manifest changed since the PA build")
    return prov


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
                           for h in "LR"}, rel_rhp=lambda x: (x["p_throws"] == "R").astype(int), rel_all=1)[
                        ["season", "game_date", *[f"rel_{t}_{h}" for t in ("n", "same") for h in "LR"], "rel_rhp", "rel_all"]]])
    lg = lg.fillna(0)
    lg["k"] = 0
    T["lcols"] = [c for c in lg.columns if c not in ("season", "game_date", "k")]
    T["l"] = through(lg, ["k"], T["lcols"])
    return T, bg


def batter_games(pa):
    """ONE settlement row per player-game, keyed (game_pk, batter) -- REPAIR of Codex finding 1.

    The audited version also grouped by `stand`, splitting a switch hitter who batted from both sides into two
    rows with partial-game labels. A Hits prop settles on the WHOLE game, so hits/TB are summed over every PA
    of the player-game. Handedness is NOT taken from realized PAs here; see assign_matchup_side()."""
    g = pa.groupby(["season", "game_date", "game_pk", "bat_team", "fld_team", "batter"], as_index=False).agg(
        slot=("slot", "first"), opp_sp=("opp_sp", "first"), hits=("hit", "sum"), tb=("tb", "sum"),
        n_pa=("one", "sum"))
    if g.duplicated(["game_pk", "batter"]).any():
        raise ValueError("settlement identity violated: duplicate (game_pk, batter)")
    g["h1"] = (g["hits"] >= 1).astype(int)
    g["h2"] = (g["hits"] >= 2).astype(int)
    g["tb2"] = (g["tb"] >= 2).astype(int)
    return g


def pitcher_hands(pa):
    """Throwing hand per pitcher (biographical; the modal value over his PAs)."""
    return pa.groupby("pitcher")["p_throws"].agg(lambda s: s.mode().iat[0]).to_dict()


def assign_matchup_side(bg, bat_side, p_hand):
    """Pregame batting side vs the opposing STARTER for each player-game (no realized-PA information).

    bat_side: MLBAM batSide code per batter (L/R/S, biographical, statsapi). Non-switch hitters bat their own
    side. A switch hitter bats opposite the starter's throwing hand (vs RHP -> L, vs LHP -> R): the standard
    platoon convention, fixed before the game. Rows whose side cannot be determined are EXCLUDED (fail closed)
    and counted by the caller."""
    g = bg.copy()
    g["bat_side"] = g["batter"].map(bat_side)
    g["p_throws_sp"] = g["opp_sp"].map(p_hand)
    opp = g["p_throws_sp"].map({"R": "L", "L": "R"})
    g["stand"] = np.where(g["bat_side"] == "S", opp, g["bat_side"])
    g["switch"] = (g["bat_side"] == "S").astype(int)
    ok = g["stand"].isin(["L", "R"]) & g["p_throws_sp"].isin(["L", "R"])
    return g[ok].copy(), g[~ok].copy()


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


def design(r, k, src="p_", sh_override=None):
    """Shrunk predictors. src='p_' = the pitcher in r[p_*]; src='r_' = the team bullpen aggregate.
    sh_override: explicit same_hand value(s) (used for switch hitters, who never share the pitcher's hand)."""
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
    if sh_override is not None:
        sh = np.broadcast_to(np.asarray(sh_override, float), (len(r),)).copy()
    elif "same_hand" in r:                    # training rows: the actual pitcher's hand
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


def people_info(ids):
    """Biographical identity metadata from statsapi /people (name, batSide, pitchHand). Not outcome data."""
    path = os.path.join(DATA, "people_v2.json")
    cache = json.load(open(path)) if os.path.exists(path) else {}
    need = [i for i in ids if str(i) not in cache]
    import requests
    for i in range(0, len(need), 150):
        chunk = need[i:i + 150]
        r = requests.get("https://statsapi.mlb.com/api/v1/people",
                         params={"personIds": ",".join(map(str, chunk))}, timeout=60)
        for pp_ in r.json().get("people", []):
            cache[str(pp_["id"])] = {"name": pp_["fullName"], "bat_side": (pp_.get("batSide") or {}).get("code"),
                                     "pitch_hand": (pp_.get("pitchHand") or {}).get("code")}
    json.dump(cache, open(path, "w"))
    return {int(k): v for k, v in cache.items()}


def load_props(dates, repo="/home/user/Full-Count"):
    """FanDuel one-sided 'Over 0.5 Hits' rows from data/props at origin/main (pregame, not in play)."""
    rows = []
    sha = subprocess.check_output(["git", "-C", repo, "rev-parse", "origin/main"]).decode().strip()
    for d in dates:
        try:
            raw = subprocess.check_output(["git", "-C", repo, "show", f"{sha}:data/props/props_{d}.json"],
                                          stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            continue
        for s_ in json.loads(raw).get("snapshots", []):
            for x in s_.get("rows", []):
                if x.get("stat") == "hits" and x.get("needs") == 1 and not x.get("in_play") \
                        and x.get("taken_at") and x.get("start_time") and x.get("event_id") and x.get("game"):
                    rows.append({"game_date": d, "event_id": int(x["event_id"]), "game": x["game"],
                                 "player_norm": norm(x["player"]), "start_time": x["start_time"],
                                 "taken_at": x["taken_at"], "american": x["american"]})
    return pd.DataFrame(rows), sha


def schedule_games(dates):
    """statsapi schedule identity (gamePk, official first-pitch time, team names). Not outcome data."""
    path = os.path.join(DATA, "schedule_identity.json")
    cache = json.load(open(path)) if os.path.exists(path) else {}
    import requests
    for d in dates:
        if d not in cache:
            js = requests.get("https://statsapi.mlb.com/api/v1/schedule",
                              params={"sportId": 1, "date": d}, timeout=60).json()
            cache[d] = [{"game_pk": g["gamePk"], "game_date_utc": g["gameDate"],
                         "away": g["teams"]["away"]["team"]["name"], "home": g["teams"]["home"]["team"]["name"]}
                        for dd in js.get("dates", []) for g in dd.get("games", [])]
    json.dump(cache, open(path, "w"))
    return pd.DataFrame([{**g, "sched_date": d} for d in dates for g in cache.get(d, [])])


def _teams(game_str):
    parts = re.sub(r"\s*\([^)]*\)", "", game_str).split(" @ ")
    return (norm(parts[0]), norm(parts[1])) if len(parts) == 2 else (None, None)


def match_market(props, sched, ev, pp):
    """Event -> game_pk by (away, home) team names AND |FanDuel start - official first pitch| <= 90 min; player ->
    batter by normalized name WITHIN that game's eligible player-games. Unmatched/ambiguous are counted and
    excluded; nothing is guessed. Price = last snapshot strictly before min(FanDuel start, official first pitch)."""
    ident = {"method": "event_id -> game_pk via statsapi schedule (teams + start time); name within game"}
    if not len(props):
        return pd.DataFrame(), ident | {"props_rows": 0}
    ev_ = props[["event_id", "game", "start_time", "game_date"]].drop_duplicates("event_id").copy()
    ev_[["away_n", "home_n"]] = ev_["game"].apply(lambda g: pd.Series(_teams(g)))
    sch = sched.assign(away_n=sched["away"].map(norm), home_n=sched["home"].map(norm),
                       t=pd.to_datetime(sched["game_date_utc"], utc=True))
    emap, n_amb, n_none = {}, 0, 0
    for _, e in ev_.iterrows():
        t = pd.to_datetime(e["start_time"], utc=True)
        c = sch[(sch["away_n"] == e["away_n"]) & (sch["home_n"] == e["home_n"]) & ((sch["t"] - t).abs() <= pd.Timedelta("90min"))]
        c = c.drop_duplicates("game_pk")
        if len(c) == 1:
            emap[e["event_id"]] = (int(c["game_pk"].iat[0]), c["t"].iat[0])
        elif len(c) > 1:
            n_amb += 1
        else:
            n_none += 1
    pr = props[props["event_id"].isin(emap)].copy()
    pr["game_pk"] = pr["event_id"].map(lambda i: emap[i][0])
    pr["first_pitch"] = pr["event_id"].map(lambda i: emap[i][1])
    cutoff = pd.concat([pd.to_datetime(pr["start_time"], utc=True), pr["first_pitch"]], axis=1).min(axis=1)
    pr = pr[pd.to_datetime(pr["taken_at"], utc=True) < cutoff]
    pr = pr.sort_values("taken_at").groupby(["game_pk", "player_norm"], as_index=False).last()
    names = ev.groupby(["game_pk", "player_norm"])["batter"].agg(list).reset_index()
    j = pr.merge(names, on=["game_pk", "player_norm"], how="left")
    unmatched = int(j["batter"].isna().sum())
    amb = int(j["batter"].dropna().map(len).gt(1).sum())
    j = j[j["batter"].map(lambda v: isinstance(v, list) and len(v) == 1)].copy()
    j["batter"] = j["batter"].map(lambda v: v[0])
    j["mkt_implied"] = [pp.implied_probability(a) for a in j["american"]]
    j["mkt_fair"] = [pp.devig(i) for i in j["mkt_implied"]]
    m = ev.merge(j[["game_pk", "batter", "mkt_fair", "mkt_implied", "american", "taken_at"]], on=["game_pk", "batter"])
    dh = sch.groupby(["sched_date", "away_n", "home_n"])["game_pk"].nunique()
    dh_pks = set(sch.set_index(["sched_date", "away_n", "home_n"]).loc[dh[dh > 1].index]["game_pk"]) if (dh > 1).any() else set()
    ident |= {"props_rows": int(len(props)), "events": int(len(ev_)), "events_matched": len(emap),
              "events_ambiguous": n_amb, "events_unmatched": n_none,
              "player_prices_in_matched_events": int(len(pr)), "player_unmatched_in_eligible_population": unmatched,
              "player_ambiguous_name_in_game": amb, "matched_player_games": int(len(m)),
              "doubleheader_player_games_matched": int(m["game_pk"].isin(dh_pks).sum()),
              "doubleheader_exclusions": 0}
    return m, ident


# ------------------------------------------------------------------ stage: evaluate (ONE frozen run on 2026)
def boot_ci(daily_diff, seed=7):
    rng = np.random.default_rng(seed)
    v = np.asarray(daily_diff, float)
    bs = [rng.choice(v, len(v), replace=True).mean() for _ in range(BOOT)]
    return float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))


def topk_select(df, col, K):
    """Per date, the top min(K, eligible) player-games by `col` (ties -> lower batter id). REPAIR of finding 3:
    callers report the ACTUAL number selected, never K x dates."""
    if df.duplicated(["game_pk", "batter"]).any():
        raise ValueError("population has duplicate player-games")
    return df.sort_values(["game_date", col, "batter"], ascending=[True, False, True]).groupby("game_date").head(K)


def topk_rates(df, col, K):
    return topk_select(df, col, K).groupby("game_date")["h1"].mean()


def topk_counts(df, K):
    n = df.groupby("game_date").size()
    sel = n.clip(upper=K)
    short = n[n < K]
    return {"selected_rows": int(sel.sum()), "dates": int(len(n)), "dates_short": int(len(short)),
            "shortfalls": {d: int(K - v) for d, v in short.items()}}


def paired(df, a, b, K):
    ra, rb = topk_rates(df, a, K), topk_rates(df, b, K)
    diff = (ra - rb).dropna()
    lo, hi = boot_ci(diff.to_numpy())
    return {"diff_pp": 100 * float(diff.mean()), "ci95_pp": [100 * lo, 100 * hi]}


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
    prov = verified_provenance()
    pa, pit = load()
    T, bg = tables(pa, pit)
    ev = bg[(bg["season"] == season) & (bg["game_date"] >= START[season]) & (bg["slot"] <= 9)].copy()
    # audited (pre-repair) fragmenting rule, reported only to quantify finding 1
    frag = pa[(pa["season"] == season) & (pa["game_date"] >= START[season]) & (pa["slot"] <= 9)].groupby(
        ["game_pk", "batter", "stand"], as_index=False)["hit"].sum()
    dup = frag[frag.duplicated(["game_pk", "batter"], keep=False)]
    conflict = dup.assign(h1=(dup["hit"] >= 1).astype(int)).groupby(["game_pk", "batter"])["h1"].nunique()
    info = people_info(sorted(set(ev["batter"].tolist())))
    ev, side_excl = assign_matchup_side(ev, {k_: v["bat_side"] for k_, v in info.items()}, pitcher_hands(pa))
    ev = attach(ev, T, pitcher_col="opp_sp")
    n_before_minpa = len(ev)
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
    sw = (ev["switch"] == 1).to_numpy()
    Dsp = design(ev, k, "p_", sh_override=np.where(sw, 0.0, (ev["stand"] == ev["p_throws_sp"]).astype(float)))
    Dpen = design(ev, k, "r_")
    side = {}
    for st_ in "LR":                         # switch hitter: vs RHP reliever bats L, vs LHP reliever bats R
        e2 = ev.drop(columns=[c_ for c_ in ev.columns if c_.startswith("r_")]).assign(stand=st_)
        e2 = e2.merge(asof(T["r"], ["fld_team", "stand"], T["pcols"], e2, "r_"),
                      on=["season", "fld_team", "stand", "game_date"], how="left")
        side[st_] = design(e2, k, "r_", sh_override=0.0)
    s_rhp = (ev["c_rel_rhp"] / ev["c_rel_all"]).to_numpy()
    for name, cols in MODELS.items():
        c = frozen["coef"][name]
        z = lambda D: c["intercept"] + sum(c[x] * D[x] for x in cols)                     # noqa: E731
        pen = np.where(sw, s_rhp * expit(z(side["L"])) + (1 - s_rhp) * expit(z(side["R"])), expit(z(Dpen)))
        p_pa = ev["w_sp"] * expit(z(Dsp)) + (1 - ev["w_sp"]) * pen
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
    res["repair"] = {
        "settlement_identity": "(game_pk, batter); hits/TB summed over every PA of the player-game",
        "audited_rule_fragments": {"rows_by_stand": int(len(frag)),
                                   "player_games_split": int(dup[["game_pk", "batter"]].drop_duplicates().shape[0]),
                                   "split_with_conflicting_h1": int((conflict > 1).sum())},
        "matchup_side": "statsapi batSide; switch hitter bats opposite the starter's hand; bullpen side mixed by "
                        "league RHP share of relief PAs as of D-1; undeterminable side excluded",
        "side_excluded_rows": int(len(side_excl)), "switch_hitter_player_games": int(ev["switch"].sum()),
        "rows_before_min_pa": int(n_before_minpa), "raw_provenance": prov}
    # ---- primary: equal top-K per date (actual selected counts)
    prim = {}
    for K in TOPK:
        row = {"counts": topk_counts(ev, K), "CH0_rate": float(topk_rates(ev, "CH0", K).mean())}
        for name in MODELS:
            row[name] = {"rate": float(topk_rates(ev, name, K).mean()), **paired(ev, name, "CH0", K),
                         "selected_rows": row["counts"]["selected_rows"]}
        row["CH1_minus_CH1a"] = paired(ev, "CH1", "CH1a", K)
        prim[f"K{K}"] = row
    d = prim["K10"]["CH1"]
    verdict = ("IMPROVES" if d["diff_pp"] >= 2.0 and d["ci95_pp"][0] > 0 else
               "NO_GAIN" if d["ci95_pp"][0] <= 0 <= d["ci95_pp"][1] and abs(d["diff_pp"]) < 1.0 else "INCONCLUSIVE")
    res["primary"] = prim
    res["verdict_K10_CH1_vs_CH0"] = verdict
    # ---- stability (K=10): halves and months
    stab = {}
    month = ev["game_date"].str[:7]
    groups = [("apr15_jun30", ev["game_date"] <= f"{season}-06-30"), ("jul01_end", ev["game_date"] > f"{season}-06-30")]
    groups += [(m_, month == m_) for m_ in sorted(month.unique())]
    for lab, mask in groups:
        sub_ = ev[mask]
        stab[lab] = {"dates": int(sub_["game_date"].nunique()),
                     "CH1_minus_CH0_pp": paired(sub_, "CH1", "CH0", 10)["diff_pp"],
                     "CH1a_minus_CH0_pp": paired(sub_, "CH1a", "CH0", 10)["diff_pp"]}
    res["stability_K10"] = stab
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
    for col, lab in (("CH1", "calibration_CH1_deciles"), ("CH0", "calibration_CH0_deciles")):
        dec = pd.qcut(ev[col], 10, labels=False, duplicates="drop")
        sec[lab] = ev.groupby(dec).agg(pred=(col, "mean"), act=("h1", "mean"), n=("h1", "size")).round(4).to_dict("records")
    sub = {}
    for lab, mask in (("LHB_side", ev["stand"] == "L"), ("RHB_side", ev["stand"] == "R"), ("switch", ev["switch"] == 1),
                      ("same_hand_sp", (ev["stand"] == ev["p_throws_sp"]) & (ev["switch"] == 0)),
                      ("opp_hand_sp", ev["stand"] != ev["p_throws_sp"])):
        s_ = ev[mask]
        sub[lab] = {"n": int(len(s_)), "ll_gain_CH1_vs_CH0": float(mean_ll(s_["CH1"], s_["h1"]) - mean_ll(s_["CH0"], s_["h1"]))}
    sec["subgroups"] = sub
    sec["corr_CH0_CH1"] = float(np.corrcoef(ev["CH0"], ev["CH1"])[0, 1])
    sec["topK10_overlap_share"] = float(np.mean([
        len(set(g.nlargest(10, "CH0")["batter"]) & set(g.nlargest(10, "CH1")["batter"])) / min(10, len(g))
        for _, g in ev.groupby("game_date")]))
    res["secondary"] = sec
    if not market:
        json.dump(res, open(out_json, "w"), indent=1, default=float)
        print(json.dumps({k_: res[k_] for k_ in ("population", "repair", "primary", "verdict_K10_CH1_vs_CH0")}, indent=1, default=float))
        return
    # ---- market subset: event-identity matching (REPAIR of finding 2)
    ev["player_norm"] = ev["batter"].map(lambda i: norm((info.get(int(i)) or {}).get("name", "")))
    props, props_sha = load_props(sorted(ev["game_date"].unique()))
    sched = schedule_games(sorted(props["game_date"].unique())) if len(props) else pd.DataFrame()
    m, ident = match_market(props, sched, ev, pp)
    mres = {"props_source": f"data/props @ origin/main {props_sha}", "identity": ident,
            "devig": "one-sided FanDuel price, production assumed hold (pp.devig): APPROXIMATE",
            "rows_with_price": int(len(m))}
    if len(m):
        mres["dates"] = int(m["game_date"].nunique())
        y = m["h1"].to_numpy()
        mres["logloss"] = {n: -mean_ll(m[n].to_numpy(), y) for n in ("mkt_fair", "CH0", "CH1a", "CH1")}
        mres["mean"] = {n: float(m[n].mean()) for n in ("mkt_fair", "CH0", "CH1a", "CH1")} | {"actual": float(y.mean())}
        for K in (3, 5):
            row = {"counts": topk_counts(m, K)}
            row.update({n: float(topk_rates(m, n, K).mean()) for n in ("CH0", "CH1a", "CH1", "mkt_fair")})
            for a_, b_ in (("CH1a", "CH0"), ("CH1", "CH0"), ("CH1a", "mkt_fair"), ("CH1", "mkt_fair")):
                row[f"{a_}_minus_{b_}"] = paired(m, a_, b_, K)
            mres[f"topK{K}"] = row
        for n in ("CH0", "CH1"):
            X = np.column_stack([logit(m["mkt_fair"]), logit(m[n]) - logit(m["mkt_fair"])])
            fr = fit_lr(X, y)
            mres[f"disagreement_coef_{n}"] = {"coef": float(fr.coef_[0][1]), "market_coef": float(fr.coef_[0][0]),
                                              "note": "confounded by de-vig compression; descriptive only"}
        m["dis_CH1"] = m["CH1"] - m["mkt_fair"]
        dec = pd.qcut(m["dis_CH1"], 5, labels=False, duplicates="drop")
        mres["CH1_disagreement_quintiles"] = m.groupby(dec).agg(
            dis=("dis_CH1", "mean"), mkt=("mkt_fair", "mean"), ch1=("CH1", "mean"), act=("h1", "mean"),
            n=("h1", "size")).round(4).to_dict("records")
    res["market"] = mres
    # ---- artifacts
    keep = ["game_date", "game_pk", "batter", "bat_side", "switch", "stand", "p_throws_sp", "slot", "opp_sp", "proj_pa", "w_sp", "lg_h1",
            "h1", "h2", "tb2", *names, "p_pa_CH0", *[f"p_pa_{n}" for n in MODELS]]
    pred = os.path.join(OUT, f"predictions_{season}.csv.gz")
    ev[keep].to_csv(pred, index=False, compression="gzip")
    res["artifacts"] = {"predictions_sha256": sha256_file(pred),
                        "statcast_manifest_sha256": sha256_file(os.path.join(DATA, "statcast", "MANIFEST.jsonl"))}
    json.dump(res, open(out_json, "w"), indent=1, default=float)
    print(json.dumps({k_: res[k_] for k_ in ("population", "repair", "verdict_K10_CH1_vs_CH0")}, indent=1, default=float))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("pa", "fit", "selfcheck", "evaluate"))
    a = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    {"pa": stage_pa, "fit": stage_fit, "selfcheck": stage_selfcheck, "evaluate": stage_evaluate}[a.stage]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
