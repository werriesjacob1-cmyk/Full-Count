"""Tier 2 F11 (receiver vs coverage) and F12 (defensive coverage tendencies).

Strictly prior by construction: every profile for target season S is built
only from COMPLETED seasons S-1 (weight 1.0) and S-2 (weight 0.5). No
in-season participation is used, because none exists live (2023+
participation is released after the postseason) -- historical evaluation
therefore sees exactly what a live 2026 prediction can see.

F11 -- per receiver, per man/zone bin, from on-field coverage-labelled
dropbacks: on-field count n, targets t, receptions c, receiving yards y.
    target rate   r = (t + K_RATE * r_pos) / (n + K_RATE)        (per on-field dropback)
    catch rate    q = (c + K_EFF * q_pos) / (t + K_EFF)          (per target)
    yds / target  v = (y + K_EFF * v_pos) / (t + K_EFF)
`_pos` are league position-level rates in the same window. The denominator
is on-field participation, NOT routes run (routes are not observed); the
target-conditional quantities (q, v) are labelled as such.

F12 -- per defense: shrunk man share in the same window,
    m = (man + K_MIX * m_league) / (labelled + K_MIX)
with a verified head-coach change between S-1 and S halving the prior
seasons' weight (more shrinkage). Defensive-coordinator identity is not in
the repo's regime registry (DC intervals not ingested), so DC is UNKNOWN and
coordinator tendencies across teams are NOT modelled.

Coverage families (Cover 0/1/2/3/4/6, 2-man) are reported descriptively
only: the NGS (2018-22) and FTN (2023-25) taxonomies differ and per-family
cells are thin; the consumer uses the man/zone split supported by both.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping

from nfl.research.tier2.coverage_data import UNKNOWN

BINS = ("MAN", "ZONE")
WINDOW = {1: 1.0, 2: 0.5}           # seasons back -> weight
K_RATE = 150.0                      # on-field dropbacks of prior for target rate
K_EFF = 30.0                        # targets of prior for catch rate / yds per target
K_MIX = 200.0                       # labelled dropbacks of prior for a defense's man share
HC_CHANGE_WEIGHT = 0.5              # prior-season weight multiplier after a verified HC change
MIN_PLAYER_ONFIELD = 100.0          # weighted on-field labelled dropbacks to use F11
MIN_DEFENSE_LABELLED = 200.0        # weighted labelled dropbacks to use F12
# Coverage-family extension (pre-declared 2026-09-25 before any family fit or scoring).
# Families shared by the NGS and FTN taxonomies plus OTHER (source-specific labels);
# UNKNOWN is excluded. Each family's receiver rates shrink toward the receiver's own
# shrunk man/zone rate for the family's structure (OTHER: toward his exposure-pooled
# rate); opponent family mixes shrink toward the league family mix of the same window.
FAMILY_PARENT = {"COVER_0": "MAN", "COVER_1": "MAN", "2_MAN": "MAN", "COVER_2": "ZONE",
                 "COVER_3": "ZONE", "COVER_4": "ZONE", "COVER_6": "ZONE", "OTHER": None}
K_FAM = 60.0                        # on-field family dropbacks of prior toward the parent rate


class SeasonTables:
    """Per-season aggregates of the dropback table (coverage-labelled only)."""

    def __init__(self, rows: Iterable[Mapping], positions: Mapping[str, str]):
        self.player = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0, 0.0]))
        self.defense = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
        self.pos = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0, 0.0]))
        self.league = defaultdict(lambda: [0.0, 0.0])
        self.family = defaultdict(lambda: defaultdict(float))
        self.player_fam = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0, 0.0]))
        self.pos_fam = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0, 0.0]))
        self.league_fam = defaultdict(lambda: defaultdict(float))
        for r in rows:
            if r["season_type"] != "REG" or r["man_zone"] == UNKNOWN:
                continue
            s, b = r["season"], r["man_zone"]
            d = self.defense[(s, r["defteam"])][b]
            d[0] += 1
            self.league[s][0 if b == "MAN" else 1] += 1
            self.family[(s, r["defteam"])][r["family"]] += 1
            fam = r["family"] if r["family"] in FAMILY_PARENT else None
            if fam:
                self.league_fam[s][fam] += 1
            for pid in r["offense_players"]:
                pos = positions.get(pid)
                if pos not in ("WR", "TE", "RB"):
                    continue
                cell = self.player[(s, pid)][b]
                pcell = self.pos[(s, pos)][b]
                cell[0] += 1
                pcell[0] += 1
                if r["target"] == pid:
                    cell[1] += 1
                    pcell[1] += 1
                    if r["complete"]:
                        cell[2] += 1
                        cell[3] += r["yards"]
                        pcell[2] += 1
                        pcell[3] += r["yards"]
                if fam:
                    fc, pfc = self.player_fam[(s, pid)][fam], self.pos_fam[(s, pos)][fam]
                    fc[0] += 1
                    pfc[0] += 1
                    if r["target"] == pid:
                        fc[1] += 1
                        pfc[1] += 1
                        if r["complete"]:
                            fc[2] += 1
                            fc[3] += r["yards"]
                            pfc[2] += 1
                            pfc[3] += r["yards"]


def _window(target_season: int, available: set[int]) -> list[tuple[int, float]]:
    return [(target_season - back, w) for back, w in WINDOW.items()
            if (target_season - back) in available]


def receiver_profile(tables: SeasonTables, pid: str, pos: str, target_season: int,
                     seasons_available: set[int]) -> dict:
    """F11 profile or {'status': reason} when unsupported."""
    win = _window(target_season, seasons_available)
    if pos not in ("WR", "TE", "RB") or not win:
        return {"status": "UNSUPPORTED_POSITION_OR_NO_PRIOR_SEASON"}
    agg = {b: [0.0, 0.0, 0.0, 0.0] for b in BINS}
    pag = {b: [0.0, 0.0, 0.0, 0.0] for b in BINS}
    for season, w in win:
        for b in BINS:
            c = tables.player.get((season, pid), {}).get(b)
            pc = tables.pos.get((season, pos), {}).get(b)
            if c:
                for i in range(4):
                    agg[b][i] += w * c[i]
            if pc:
                for i in range(4):
                    pag[b][i] += w * pc[i]
    n_total = agg["MAN"][0] + agg["ZONE"][0]
    if n_total < MIN_PLAYER_ONFIELD:
        return {"status": "INSUFFICIENT_RECEIVER_COVERAGE_SAMPLE", "n_onfield": n_total}
    out = {"status": "OK", "n_onfield": n_total, "window": win, "bins": {}}
    for b in BINS:
        n, t, c, y = agg[b]
        pn, pt, pc_, py = pag[b]
        r_pos = pt / pn if pn else 0.0
        q_pos = pc_ / pt if pt else 0.0
        v_pos = py / pt if pt else 0.0
        out["bins"][b] = {
            "n_onfield": n, "targets": t,
            "target_rate": (t + K_RATE * r_pos) / (n + K_RATE),
            "catch_rate_given_target": (c + K_EFF * q_pos) / (t + K_EFF),
            "yards_per_target": (y + K_EFF * v_pos) / (t + K_EFF),
        }
    out["exposure_man_share"] = agg["MAN"][0] / n_total
    return out


def league_man_share(tables: SeasonTables, win: list[tuple[int, float]]) -> float:
    man = sum(w * tables.league[s][0] for s, w in win)
    tot = sum(w * (tables.league[s][0] + tables.league[s][1]) for s, w in win)
    return man / tot if tot else 0.5


def defense_profile(tables: SeasonTables, defteam: str, target_season: int,
                    seasons_available: set[int], hc_changed: bool | None) -> dict:
    """F12 profile. hc_changed=None means the regime is UNKNOWN (treated like a
    change: prior seasons down-weighted)."""
    win = _window(target_season, seasons_available)
    if not win:
        return {"status": "NO_PRIOR_SEASON"}
    mult = 1.0 if hc_changed is False else HC_CHANGE_WEIGHT
    man = lab = 0.0
    fam = defaultdict(float)
    for season, w in win:
        cells = tables.defense.get((season, defteam), {})
        man += w * mult * cells.get("MAN", [0.0])[0]
        lab += w * mult * (cells.get("MAN", [0.0])[0] + cells.get("ZONE", [0.0])[0])
        for f, n in tables.family.get((season, defteam), {}).items():
            fam[f] += w * n
    m_league = league_man_share(tables, win)
    if lab < MIN_DEFENSE_LABELLED * mult:
        return {"status": "INSUFFICIENT_DEFENSE_COVERAGE_SAMPLE", "labelled": lab}
    tot_f = sum(fam.values()) or 1.0
    return {"status": "OK", "labelled_weighted": lab, "window": win,
            "man_share": (man + K_MIX * m_league) / (lab + K_MIX),
            "league_man_share": m_league,
            "regime": ("HC_UNCHANGED" if hc_changed is False else
                       "HC_CHANGED" if hc_changed else "REGIME_UNKNOWN"),
            "dc_identity": UNKNOWN,
            "family_mix_descriptive": {f: n / tot_f for f, n in sorted(fam.items())}}


def expected(profile: dict, man_share: float, quantity: str) -> float:
    """Expected per-dropback quantity under a man share:
    targets -> r; receptions -> r*q; yards -> r*v."""
    total = 0.0
    for b, weight in (("MAN", man_share), ("ZONE", 1.0 - man_share)):
        cell = profile["bins"][b]
        value = cell["target_rate"]
        if quantity == "receptions":
            value *= cell["catch_rate_given_target"]
        elif quantity == "receiving_yards":
            value *= cell["yards_per_target"]
        total += weight * value
    return total


def position_profile(tables: SeasonTables, pos: str, target_season: int,
                     seasons_available: set[int]) -> dict | None:
    """League position-level rates in the window (F12-alone consumer)."""
    win = _window(target_season, seasons_available)
    if not win or pos not in ("WR", "TE", "RB"):
        return None
    bins = {}
    for b in BINS:
        n = t = c = y = 0.0
        for season, w in win:
            cell = tables.pos.get((season, pos), {}).get(b)
            if cell:
                n += w * cell[0]; t += w * cell[1]; c += w * cell[2]; y += w * cell[3]
        if not n or not t:
            return None
        bins[b] = {"target_rate": t / n, "catch_rate_given_target": c / t,
                   "yards_per_target": y / t}
    return {"status": "OK", "bins": bins}


def _parent_rates(profile: dict, parent: str | None) -> tuple[float, float, float]:
    if parent is not None:
        b = profile["bins"][parent]
        return b["target_rate"], b["catch_rate_given_target"], b["yards_per_target"]
    m = profile["exposure_man_share"]
    bm, bz = profile["bins"]["MAN"], profile["bins"]["ZONE"]
    r = m * bm["target_rate"] + (1 - m) * bz["target_rate"]
    q = (m * bm["target_rate"] * bm["catch_rate_given_target"]
         + (1 - m) * bz["target_rate"] * bz["catch_rate_given_target"]) / r if r else 0.0
    v = (m * bm["target_rate"] * bm["yards_per_target"]
         + (1 - m) * bz["target_rate"] * bz["yards_per_target"]) / r if r else 0.0
    return r, q, v


def receiver_family_profile(tables: SeasonTables, pid: str, profile: dict, target_season: int,
                            seasons_available: set[int]) -> dict:
    """F11 by coverage family. Requires an OK man/zone `profile` (same gates)."""
    if profile.get("status") != "OK":
        return {"status": profile.get("status", "NO_RECEIVER_PROFILE")}
    win = _window(target_season, seasons_available)
    agg = {f: [0.0, 0.0, 0.0, 0.0] for f in FAMILY_PARENT}
    for season, w in win:
        for f in FAMILY_PARENT:
            c = tables.player_fam.get((season, pid), {}).get(f)
            if c:
                for i in range(4):
                    agg[f][i] += w * c[i]
    n_tot = sum(v[0] for v in agg.values())
    if n_tot <= 0:
        return {"status": "NO_FAMILY_LABELLED_EXPOSURE"}
    fams = {}
    for f, parent in FAMILY_PARENT.items():
        n, t, c, y = agg[f]
        r_p, q_p, v_p = _parent_rates(profile, parent)
        fams[f] = {"n_onfield": n, "targets": t,
                   "target_rate": (t + K_FAM * r_p) / (n + K_FAM),
                   "catch_rate_given_target": (c + K_EFF * q_p) / (t + K_EFF),
                   "yards_per_target": (y + K_EFF * v_p) / (t + K_EFF)}
    return {"status": "OK", "families": fams, "faced_mix": {f: agg[f][0] / n_tot for f in FAMILY_PARENT}}


def league_family_mix(tables: SeasonTables, win: list[tuple[int, float]]) -> dict[str, float] | None:
    tot = {f: sum(w * tables.league_fam[s].get(f, 0.0) for s, w in win) for f in FAMILY_PARENT}
    n = sum(tot.values())
    return {f: v / n for f, v in tot.items()} if n else None


def defense_family_mix(tables: SeasonTables, defteam: str, target_season: int,
                       seasons_available: set[int], hc_changed: bool | None) -> dict:
    """F12 by coverage family: shrunk family mix (same HC rule and gate as F12)."""
    win = _window(target_season, seasons_available)
    if not win:
        return {"status": "NO_PRIOR_SEASON"}
    league = league_family_mix(tables, win)
    mult = 1.0 if hc_changed is False else HC_CHANGE_WEIGHT
    cnt = {f: sum(w * mult * tables.family.get((s, defteam), {}).get(f, 0.0) for s, w in win)
           for f in FAMILY_PARENT}
    lab = sum(cnt.values())
    if league is None or lab < MIN_DEFENSE_LABELLED * mult:
        return {"status": "INSUFFICIENT_DEFENSE_COVERAGE_SAMPLE", "labelled": lab}
    return {"status": "OK", "mix": {f: (cnt[f] + K_MIX * league[f]) / (lab + K_MIX) for f in FAMILY_PARENT},
            "league_mix": league, "dc_identity": UNKNOWN}


def position_family_profile(tables: SeasonTables, pos: str, target_season: int,
                            seasons_available: set[int]) -> dict | None:
    win = _window(target_season, seasons_available)
    if not win or pos not in ("WR", "TE", "RB"):
        return None
    fams = {}
    for f in FAMILY_PARENT:
        n = t = c = y = 0.0
        for season, w in win:
            cell = tables.pos_fam.get((season, pos), {}).get(f)
            if cell:
                n += w * cell[0]; t += w * cell[1]; c += w * cell[2]; y += w * cell[3]
        if not n or not t:
            return None
        fams[f] = {"target_rate": t / n, "catch_rate_given_target": c / t, "yards_per_target": y / t}
    return {"status": "OK", "families": fams}


def expected_family(profile: dict, mix: dict[str, float], quantity: str) -> float:
    total = 0.0
    for f, weight in mix.items():
        cell = profile["families"][f]
        value = cell["target_rate"]
        if quantity == "receptions":
            value *= cell["catch_rate_given_target"]
        elif quantity == "receiving_yards":
            value *= cell["yards_per_target"]
        total += weight * value
    return total
