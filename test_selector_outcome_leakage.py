#!/usr/bin/env python3
"""A selection policy must not be able to read the outcome it is predicting.

Demonstrated before it was fixed, on 2026-08-27: a policy ranking by "actual"
scored a 1.000 realized hit rate at equal N against a champion's 0.450, and
backtest/equal_volume.py accepted it silently. Equal volume, deterministic
ranking and the outcome policy were all satisfied -- none of them constrains
WHICH FIELDS a ranker may read. That result would have looked like the largest
improvement in this project's history and would have measured nothing.

These tests keep the information boundary enforced by construction.
"""
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest.equal_volume import (EligiblePopulation, SelectionPolicy, OutcomePolicy,
                                   EqualVolumeExperiment, OutcomeLeakage, RankingRow,
                                   RankingView, OUTCOME_FIELD_DENYLIST, rank_by)

ID = ("date", "game_pk", "player_id", "prop_type", "line")
PASS = FAIL = 0


def ok(m):
    global PASS
    PASS += 1
    print(f"  ok   {m}")


def bad(m):
    global FAIL
    FAIL += 1
    print(f"  FAIL {m}")


def check(m, got, want):
    ok(m) if got == want else bad(f"{m} (want {want!r}, got {got!r})")


def population(n=200, seed=7):
    random.seed(seed)
    rows = [{"date": f"2024-04-{1 + i % 20:02d}", "game_pk": 700000 + i,
             "player_id": 500000 + i, "prop_type": "hits", "line": 0.5,
             "hit_probability": random.random(),
             "actual": 1 if random.random() < 0.5 else 0} for i in range(n)]
    return EligiblePopulation(rows, definition="test", definition_version="1",
                              evidence_regime="canonical_historical_model_data",
                              dataset_identity={"t": "t"})


def policy(name, key):
    return SelectionPolicy(name, "1", lambda p: [tuple(r[k] for k in ID)
                                                 for r in sorted(p.rows, key=key)])


def _oracle_population():
    """A population whose outcome perfectly separates, so a leak is obvious."""
    rows = [{"date": "2024-04-01", "game_pk": 100 + i // 4,
             "player_id": 1000 + i, "prop_type": "hits", "line": 0.5,
             "outcome": i % 3 == 0, "actual": float(i % 3 == 0),
             "score": float(i), "predicted_prob": i / 60.0}
            for i in range(60)]
    return EligiblePopulation(
        rows, definition="oracle fixture", definition_version="1.0.0",
        evidence_regime="test", dataset_identity={})



def main():
    pop = population()

    print("== the oracle attack ==")
    champ = policy("champion", lambda r: -r["hit_probability"])
    oracle = policy("oracle", lambda r: -r["actual"])
    exp = EqualVolumeExperiment(
        population=pop, champion=champ, challenger=oracle, volume=40,
        outcome_policy=OutcomePolicy(mode="count_as_miss", outcome_field="actual"),
        notes="leak")
    try:
        exp.run(bootstrap_iterations=20)
        bad("a policy ranking by realized outcome was ACCEPTED")
    except OutcomeLeakage as exc:
        ok("policy ranking by realized outcome is blocked")
        check("the error names the offending field", "'actual'" in str(exc), True)

    print("== legitimate policies are unaffected ==")
    alt = policy("legit", lambda r: (r["hit_probability"], r["game_pk"]))
    res = EqualVolumeExperiment(
        population=pop, champion=champ, challenger=alt, volume=40,
        outcome_policy=OutcomePolicy(mode="count_as_miss", outcome_field="actual"),
        notes="ok").run(bootstrap_iterations=20)
    check("champion still scored", isinstance(res["champion"]["hit_rate"], float), True)
    check("challenger still scored", isinstance(res["challenger"]["hit_rate"], float), True)
    check("equal volume still enforced",
          res["champion"]["selected_n"], res["challenger"]["selected_n"])

    print("== every access path is masked, not just __getitem__ ==")
    r = RankingRow(pop.rows[0])
    for f in ("actual", "actual_pa", "outcome", "graded", "settlement_state"):
        try:
            r[f]; bad(f"{f} readable via []")
        except OutcomeLeakage:
            ok(f"[{f!r}] blocked")
    for f in ("actual", "outcome_anything"):
        try:
            r.get(f); bad(f"{f} readable via .get()")
        except OutcomeLeakage:
            ok(f".get({f!r}) blocked")
    check("outcome hidden from keys()", "actual" in r.keys(), False)
    check("outcome hidden from 'in'", "actual" in r, False)
    check("outcome hidden from items()", any(k == "actual" for k, _ in r.items()), False)
    check("iteration skips outcomes", "actual" in list(iter(r)), False)

    print("== identity and model fields remain readable ==")
    for f in ID:
        check(f"identity field {f} readable", r[f] is not None, True)
    check("hit_probability readable", isinstance(r["hit_probability"], float), True)

    print("== outcomes are still available to the GRADER, just not the ranker ==")
    check("raw population row keeps its outcome", "actual" in pop.rows[0], True)
    check("view masks the same row", "actual" in RankingView(pop).rows[0], False)
    check("grading produced real hit rates", 0.0 <= res["champion"]["hit_rate"] <= 1.0, True)

    print("== the denylist covers the canonical post-event vocabulary ==")
    for f in ("actual", "outcome", "graded", "grade", "result", "won", "settlement_state"):
        check(f"denylist contains {f}", f in OUTCOME_FIELD_DENYLIST, True)


    print("== C-LEVEL BYPASS PATHS (the first fix missed these) ==")
    # Overriding dict's accessors was NOT enough. dict.setdefault and
    # dict.pop never consult __getitem__, so an oracle ranking on
    # r.setdefault("actual", 0) read the outcome straight through the mask
    # and scored 1.000 against the champion's 0.300. The fix strips the
    # fields rather than guarding them, so nothing is there to return.
    r = RankingRow({"player_id": 1, "score": 60.0, "actual": 1,
                    "outcome": 1, "hit": 1})
    check("outcome fields are not stored at all",
          sorted(dict.keys(r)), ["player_id", "score"])
    check("row reports what it withheld",
          sorted(r.masked_fields), ["actual", "hit", "outcome"])
    for how, fn in (("setdefault", lambda: r.setdefault("actual", 0)),
                    ("pop", lambda: r.pop("actual", 0)),
                    ("[]", lambda: r["actual"]),
                    ("get", lambda: r.get("actual"))):
        try:
            fn()
            bad(f"{how} read the outcome")
        except OutcomeLeakage:
            ok(f"{how} raises OutcomeLeakage")

    # No override can intercept an unbound dict call. Absence can.
    check("unbound dict.get finds nothing", dict.get(r, "actual"), None)
    check("unbound dict.setdefault finds nothing",
          dict.setdefault(r, "actual", 0), 0)
    check("unbound dict.pop finds nothing", dict.pop(r, "actual", 0), 0)
    try:
        dict.__getitem__(r, "actual")
        bad("unbound dict.__getitem__ read the outcome")
    except KeyError:
        ok("unbound dict.__getitem__ raises KeyError (field absent)")

    for label, made in (("dict(r)", dict(r)), ("{**r}", {**r}),
                        ("r.copy()", r.copy()), ("r | {}", r | {})):
        check(f"{label} carries no outcome",
              any(k in made for k in ("actual", "outcome", "hit")), False)

    print("== the end-to-end oracle attack, via every reader ==")
    pop = _oracle_population()
    champ = SelectionPolicy("score", "1", rank_by(lambda x: x["score"]))
    for how, fn in (("setdefault", lambda x: x.setdefault("actual", 0)),
                    ("pop", lambda x: x.pop("actual", 0)),
                    ("[]", lambda x: x["actual"]),
                    ("get", lambda x: x.get("actual"))):
        exp = EqualVolumeExperiment(
            population=pop, champion=champ,
            challenger=SelectionPolicy(f"oracle_{how}", "1", rank_by(fn)),
            volume=5)
        try:
            res = exp.run(bootstrap_iterations=10)
            bad(f"oracle via {how} scored {res['challenger']['hit_rate']}")
        except OutcomeLeakage:
            ok(f"oracle via {how} is blocked")

    print()
    print(f"passed: {PASS}   failed: {FAIL}")
    return 0 if FAIL == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
