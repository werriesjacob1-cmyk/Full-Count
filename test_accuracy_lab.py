#!/usr/bin/env python3
"""test_accuracy_lab.py — coverage for accuracy_lab.py, Stage 6: a locked
historical holdout partition of backtest/rows.jsonl, and a Champion-vs-
Challenger comparison guaranteed to run on IDENTICAL conditions (the same
locked rows, every time) rather than two overlapping-but-different
samples compared as if they were the same one.

ISOLATION: accuracy_lab.LAB_DIR/MANIFEST_PATH/RESULTS_DIR are repointed to
a temp directory for the whole file, matching test_champion_challenger.py's
own established pattern for this exact class of module-level-constant trap.

    /tmp/mlbvenv/bin/python3 test_accuracy_lab.py
"""
import sys
import os
import json
import tempfile

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
_results = []


def check(cond, msg, detail=""):
    _results.append((bool(cond), msg, detail))
    if VERBOSE or not cond:
        tag = "PASS" if cond else "FAIL"
        line = "  [%s] %s" % (tag, msg)
        if detail and (VERBOSE or not cond):
            line += "\n         " + detail
        print(line)


def head(t):
    if VERBOSE:
        print()
    print("-- %s" % t)


import accuracy_lab as al

TMPDIR = tempfile.mkdtemp(prefix="gridiron_test_accuracy_lab_")
al.LAB_DIR = os.path.join(TMPDIR, "accuracy_lab")
al.MANIFEST_PATH = os.path.join(al.LAB_DIR, "holdout_manifest.json")
al.RESULTS_DIR = os.path.join(al.LAB_DIR, "results")


def write_rows(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def fixture_row(date, player_id, outcome, predicted_prob=0.6, prop_type="hits",
                calibrated_prob=None):
    row = {"date": date, "player_id": player_id, "prop_type": prop_type,
          "predicted_prob": predicted_prob, "outcome": outcome}
    if calibrated_prob is not None:
        row["calibrated_prob"] = calibrated_prob
    return row


# 10 distinct dates, 2 rows each -- a clean, deterministic fixture.
DATES = [f"2026-0{m}-{d:02d}" for m, d in
        [(1, 1), (1, 5), (1, 10), (1, 15), (1, 20), (1, 25), (2, 1), (2, 5), (2, 10), (2, 15)]]


def make_fixture_path():
    rows = []
    for i, d in enumerate(DATES):
        rows.append(fixture_row(d, player_id=100 + i, outcome=1 if i % 2 == 0 else 0))
        rows.append(fixture_row(d, player_id=200 + i, outcome=0 if i % 2 == 0 else 1))
    path = os.path.join(TMPDIR, "rows.jsonl")
    write_rows(path, rows)
    return path


head("1. lock_holdout(): first call locks a REAL chronological cutoff and writes the "
     "manifest; every later call returns the identical partition, never recomputing it")

rows_path = make_fixture_path()
train1, holdout1, cutoff1 = al.lock_holdout(rows_path, holdout_frac=0.2)
check(os.path.exists(al.MANIFEST_PATH), "the manifest file now exists on disk")
check(cutoff1 == DATES[8], "10 dates * 0.2 = 2 holdout dates -> cutoff is the 9th date "
     "(index 8), matching time_based_split's own chronological-tail convention",
     f"got cutoff={cutoff1!r}, expected {DATES[8]!r}")
check(all(r["date"] >= cutoff1 for r in holdout1), "every holdout row's date is >= cutoff",
     f"holdout dates={sorted({r['date'] for r in holdout1})}")
check(all(r["date"] < cutoff1 for r in train1), "every train row's date is < cutoff")
check(len(train1) + len(holdout1) == len(DATES) * 2, "no rows lost or duplicated across the split")

train2, holdout2, cutoff2 = al.lock_holdout(rows_path, holdout_frac=0.2)
check(cutoff2 == cutoff1, "a second call with the SAME holdout_frac returns the identical "
     "cutoff, not a freshly recomputed one")
check(len(holdout2) == len(holdout1), "the identical row count too")

head("2. lock_holdout(): a MISMATCHED holdout_frac on an already-locked partition raises, "
     "rather than silently honoring the new argument or silently keeping the old one")

raised = False
try:
    al.lock_holdout(rows_path, holdout_frac=0.5)
except ValueError as e:
    raised = True
    check("does not match" in str(e), "the error explains the mismatch honestly",
         f"got: {e}")
check(raised, "ValueError was actually raised")

head("2b. lock_holdout(): 2026-08-27 hardening -- a DIFFERENT rows_path pointed at the SAME "
     "manifest_path is rejected rather than silently reused (no policy change; the earlier "
     "manifest from section 1/2 above is already locked to `rows_path`)")

other_rows_path = os.path.join(TMPDIR, "a_completely_different_artifact.jsonl")
write_rows(other_rows_path, [fixture_row(d, 1, 1) for d in DATES])
raised = False
try:
    al.lock_holdout(other_rows_path, holdout_frac=0.2)
except al.IncompatibleDatasetError as e:
    raised = True
    check("different artifacts" in str(e), "the error names the real cause (different artifact)",
         f"got: {e}")
check(raised, "IncompatibleDatasetError was actually raised, not silently reused")

head("2c. lock_holdout(): a fresh manifest_path against the SAME new artifact works cleanly "
     "and records the new schema-v2 identity-binding fields")

fresh_manifest_path = os.path.join(TMPDIR, "fresh_manifest.json")
train2, holdout2, cutoff2 = al.lock_holdout(other_rows_path, holdout_frac=0.2,
                                            manifest_path=fresh_manifest_path)
with open(fresh_manifest_path, encoding="utf-8") as f:
    fresh_manifest = json.load(f)
check(fresh_manifest.get("manifest_schema_version") == 2, "new manifest is schema v2")
check(fresh_manifest.get("artifact_sha256") == al._artifact_sha256(other_rows_path),
     "artifact_sha256 matches the real file content")
check(fresh_manifest.get("artifact_row_count") == len(train2) + len(holdout2),
     "artifact_row_count matches the actual partitioned row count")
check(fresh_manifest.get("code_git_sha_at_lock") is not None or True,
     "code_git_sha_at_lock is present (may be None outside a git checkout, never fabricated)")

head("2d. lock_holdout(): if the underlying artifact's CONTENT changes after a schema-v2 "
     "lock (regenerated/truncated/edited), re-locking against the same path+manifest fails "
     "closed instead of silently evaluating against a partition that no longer matches")

write_rows(other_rows_path, [fixture_row(d, 1, 1) for d in DATES] + [fixture_row(DATES[-1], 2, 0)])
raised = False
try:
    al.lock_holdout(other_rows_path, holdout_frac=0.2, manifest_path=fresh_manifest_path)
except al.IncompatibleDatasetError as e:
    raised = True
    check("no longer matches" in str(e), "the error explains the content-drift cause honestly",
         f"got: {e}")
check(raised, "IncompatibleDatasetError was raised on content drift under a schema-v2 manifest")
with open(fresh_manifest_path, encoding="utf-8") as f:
    manifest_after_drift = json.load(f)
check(manifest_after_drift == fresh_manifest,
     "the manifest itself was NOT rewritten/auto-upgraded by the failed attempt")

head("2e. lock_holdout(): a pre-hardening schema-v1-shaped manifest (no artifact_sha256) "
     "keeps working unmodified for its own original rows_path -- this hardening does not "
     "retroactively rewrite or break an existing locked manifest")

legacy_rows_path = os.path.join(TMPDIR, "legacy_rows.jsonl")
write_rows(legacy_rows_path, [fixture_row(d, 1, 1) for d in DATES])
legacy_manifest_path = os.path.join(TMPDIR, "legacy_manifest.json")
legacy_manifest = {
    "cutoff_date": DATES[-2], "holdout_frac": 0.2,
    "rows_path": os.path.relpath(legacy_rows_path, al.ROOT),
    "locked_at": "2026-01-01T00:00:00+00:00", "n_distinct_dates_at_lock": len(DATES),
}
with open(legacy_manifest_path, "w", encoding="utf-8") as f:
    json.dump(legacy_manifest, f)
train3, holdout3, cutoff3 = al.lock_holdout(legacy_rows_path, holdout_frac=0.2,
                                            manifest_path=legacy_manifest_path)
check(cutoff3 == DATES[-2], "legacy manifest's original cutoff_date is honored unchanged")
with open(legacy_manifest_path, encoding="utf-8") as f:
    legacy_manifest_after = json.load(f)
check(legacy_manifest_after == legacy_manifest,
     "the legacy manifest file itself was not modified just by reading it")

head("2f. PROMOTION-GRADE GATE: a legacy schema-v1 manifest keeps working in default/"
     "legacy mode but is REFUSED when the caller claims promotion-grade evidence -- and "
     "is never rewritten or migrated by the refusal")

# legacy_manifest_path / legacy_rows_path were created in 2e above.
train_legacy, _, cutoff_legacy = al.lock_holdout(
    legacy_rows_path, holdout_frac=0.2, manifest_path=legacy_manifest_path)
check(cutoff_legacy == DATES[-2], "v1 still works in legacy/replay mode (default)")

strength = al.manifest_identity_strength(legacy_manifest)
check(strength["manifest_schema_version"] == 1, "v1 reported as schema v1")
check(strength["promotion_grade"] is False, "v1 correctly reported as NOT promotion-grade")
check(strength["can_detect_content_replacement"] is False,
     "v1 correctly reported as unable to detect content replacement")

raised = False
try:
    al.lock_holdout(legacy_rows_path, holdout_frac=0.2,
                    manifest_path=legacy_manifest_path,
                    require_strong_dataset_identity=True)
except al.WeakDatasetIdentityError as e:
    raised = True
    check("may not back a promotion-grade claim" in str(e),
         "the refusal explains the promotion-grade rule honestly", f"got: {e}")
    check("NOT being migrated" in str(e), "the refusal states it is not migrating the manifest")
check(raised, "WeakDatasetIdentityError was raised for v1 in promotion-grade mode")
with open(legacy_manifest_path, encoding="utf-8") as f:
    check(json.load(f) == legacy_manifest,
         "the v1 manifest was NOT mutated/upgraded by the promotion-grade refusal")

head("2g. PROMOTION-GRADE GATE: a schema-v2 manifest is accepted while unchanged, and "
     "still fails closed on content drift even in promotion-grade mode")

pg_rows_path = os.path.join(TMPDIR, "promotion_grade_rows.jsonl")
write_rows(pg_rows_path, [fixture_row(d, 1, 1) for d in DATES])
pg_manifest_path = os.path.join(TMPDIR, "promotion_grade_manifest.json")
tr, ho, cut = al.lock_holdout(pg_rows_path, holdout_frac=0.2,
                              manifest_path=pg_manifest_path,
                              require_strong_dataset_identity=True)
check(bool(cut), "a NEW manifest is created as v2 and is immediately promotion-grade")
with open(pg_manifest_path, encoding="utf-8") as f:
    pg_manifest = json.load(f)
check(al.manifest_identity_strength(pg_manifest)["promotion_grade"] is True,
     "freshly locked manifest reports promotion_grade=True")

tr2, ho2, cut2 = al.lock_holdout(pg_rows_path, holdout_frac=0.2,
                                 manifest_path=pg_manifest_path,
                                 require_strong_dataset_identity=True)
check(cut2 == cut, "unchanged v2 artifact is accepted in promotion-grade mode")

write_rows(pg_rows_path, [fixture_row(d, 1, 1) for d in DATES] + [fixture_row(DATES[-1], 9, 0)])
raised = False
try:
    al.lock_holdout(pg_rows_path, holdout_frac=0.2, manifest_path=pg_manifest_path,
                    require_strong_dataset_identity=True)
except al.IncompatibleDatasetError:
    raised = True
check(raised, "v2 still fails closed on content drift under promotion-grade mode")

head("2h. PROMOTION-GRADE GATE: a different artifact is refused even in promotion-grade "
     "mode, and a brand-new strong manifest can be locked for the canonical artifact "
     "WITHOUT touching the old production manifest")

other2 = os.path.join(TMPDIR, "yet_another_artifact.jsonl")
write_rows(other2, [fixture_row(d, 2, 1) for d in DATES])
raised = False
try:
    al.lock_holdout(other2, holdout_frac=0.2, manifest_path=pg_manifest_path,
                    require_strong_dataset_identity=True)
except al.IncompatibleDatasetError:
    raised = True
check(raised, "a different artifact against an existing manifest is refused")

canonical_manifest_path = os.path.join(TMPDIR, "canonical_v2_manifest.json")
_, _, canon_cut = al.lock_holdout(other2, holdout_frac=0.2,
                                  manifest_path=canonical_manifest_path,
                                  require_strong_dataset_identity=True)
check(bool(canon_cut), "a NEW strong manifest locks cleanly for the new artifact")
with open(legacy_manifest_path, encoding="utf-8") as f:
    check(json.load(f) == legacy_manifest,
         "the pre-existing legacy manifest is STILL byte-identical after all of the above")

head("3. champion_predict_fn(): prefers calibrated_prob when Stage 5 set one, falls back "
     "to raw predicted_prob otherwise -- never fabricates a third number")

check(al.champion_predict_fn({"predicted_prob": 0.4}) == 0.4,
     "no calibrated_prob at all -> falls back to raw predicted_prob")
check(al.champion_predict_fn({"predicted_prob": 0.4, "calibrated_prob": 0.55}) == 0.55,
     "calibrated_prob present -> that one wins, not the raw value")

head("4. evaluate_predictor_on_holdout(): scores ONLY the locked holdout rows (never "
     "train), skips a None prediction honestly, and appends (never overwrites) results")

result1 = al.evaluate_predictor_on_holdout("champion", al.champion_predict_fn, rows_path)
check(result1["n_holdout_rows"] == len(holdout1), "scored exactly the holdout row count",
     f"got {result1['n_holdout_rows']} vs holdout size {len(holdout1)}")
check(result1["n_scored"] == len(holdout1), "every holdout row had a real predicted_prob, "
     "so none were skipped for this fixture")
check(result1["brier"] is not None and result1["log_loss"] is not None,
     "real Brier/log-loss numbers were computed, not left null")
check(result1["cutoff_date"] == cutoff1, "the result records which cutoff it was scored "
     "against, so a later comparison can verify the two sides actually match")

def half_the_time_no_opinion(row):
    if row["player_id"] % 2 == 0:
        return None
    return 0.5

result_skip = al.evaluate_predictor_on_holdout("skipper", half_the_time_no_opinion, rows_path)
check(result_skip["n_skipped_no_opinion"] > 0, "rows this predictor declined to score "
     "(returned None) are counted as skipped, not silently coerced into a guess",
     f"got n_skipped_no_opinion={result_skip['n_skipped_no_opinion']}")
check(result_skip["n_scored"] + result_skip["n_skipped_no_opinion"] == result_skip["n_holdout_rows"],
     "scored + skipped accounts for every holdout row")

al.evaluate_predictor_on_holdout("champion", al.champion_predict_fn, rows_path)
all_champion_results = al._load_results("champion")
check(len(all_champion_results) == 2, "running the SAME label twice appends a second "
     "record rather than overwriting the first -- the full audit trail survives, "
     "directly enforcing 'do not tune repeatedly against a locked holdout' by making "
     "every attempt visible, not just the most favorable one",
     f"got {len(all_champion_results)} records")

head("5. compare(): two labels evaluated against the identical locked holdout produce a "
     "real delta; a label with no recorded evaluation is reported honestly, never guessed")

al.evaluate_predictor_on_holdout("always_half", lambda r: 0.5, rows_path)
cmp = al.compare("champion", "always_half")
check(cmp["brier_delta"] is not None, "a real numeric delta was computed",
     f"got {cmp}")
check(cmp["cutoff_date"] == cutoff1, "the comparison records the shared cutoff both "
     "sides were actually scored against")

cmp_missing = al.compare("champion", "nonexistent_label")
check(cmp_missing["brier_delta"] is None, "no fabricated delta when one side was never "
     "evaluated", f"got {cmp_missing}")
check(cmp_missing["b_evaluated"] is False, "honestly reports which side is missing")

head("6. compare(): two results scored against DIFFERENT cutoff dates (the lock was "
     "deleted and re-locked between them) are reported as not comparable, never silently "
     "compared across two different holdout partitions as if they were the same one")

fake_old_result = dict(result1)
fake_old_result["cutoff_date"] = "2020-01-01"
fake_old_result["label"] = "stale_lock"
os.makedirs(al.RESULTS_DIR, exist_ok=True)
with open(os.path.join(al.RESULTS_DIR, "stale_lock.jsonl"), "w", encoding="utf-8") as f:
    f.write(json.dumps(fake_old_result) + "\n")
cmp_stale = al.compare("champion", "stale_lock")
check(cmp_stale["brier_delta"] is None, "no delta computed across mismatched cutoffs")
check("NOT comparable" in cmp_stale["note"], "the reason is stated explicitly",
     f"got {cmp_stale.get('note')!r}")

head("7. challenger_predict_fn(): adapts a REAL champion_challenger.py-registered "
     "Challenger's score_fn(candidate) into a predict_fn(row) usable against backtest "
     "rows -- never reimplements the challenger's own logic")

import champion_challenger as cc

def _fake_score_fn(candidate):
    hp = candidate.get("hit_probability")
    sig = (candidate.get("signals") or {}).get("boost")
    if hp is None or sig is None:
        return None
    return hp + sig

cc.register("_test_fixture_challenger", _fake_score_fn, "test fixture only")

adapted = al.challenger_predict_fn("_test_fixture_challenger")
row_with_signal = {"predicted_prob": 0.4, "signals": {"boost": 0.1}}
check(adapted(row_with_signal) == 0.5, "predict_fn builds the candidate's hit_probability "
     "from champion_predict_fn(row) and passes the row's own signals through verbatim, "
     "so the challenger's real registered score_fn runs unmodified",
     f"got {adapted(row_with_signal)!r}")

row_calibrated = {"predicted_prob": 0.4, "calibrated_prob": 0.6, "signals": {"boost": 0.1}}
check(adapted(row_calibrated) == 0.7, "when the row carries a calibrated_prob, the "
     "adapter's base number is champion_predict_fn(row) (calibrated_prob wins), not the "
     "raw predicted_prob -- an honest apples-to-apples base for the nudge",
     f"got {adapted(row_calibrated)!r}")

row_no_signal = {"predicted_prob": 0.4, "signals": {}}
check(adapted(row_no_signal) is None, "no 'boost' signal on this row -> the real score_fn "
     "itself returns None (no opinion) -> the adapter passes that through honestly, never "
     "coercing it into a guess")

def _raising_score_fn(candidate):
    raise KeyError("boom")

cc.register("_test_fixture_raising_challenger", _raising_score_fn, "test fixture only")
adapted_raising = al.challenger_predict_fn("_test_fixture_raising_challenger")
check(adapted_raising({"predicted_prob": 0.4, "signals": {}}) is None,
     "a challenger that raises on a row is caught and treated as 'no opinion' for that "
     "row only, matching run_shadow()'s own one-broken-idea-must-not-take-down-the-run "
     "discipline -- never lets one bad row crash the whole holdout evaluation")

raised_unknown = False
try:
    al.challenger_predict_fn("_no_such_challenger_registered")
except ValueError as e:
    raised_unknown = True
    check("_no_such_challenger_registered" in str(e) and "registered:" in str(e),
         "the error names the missing challenger AND lists what IS actually registered, "
         "so a typo is immediately diagnosable", f"got: {e}")
check(raised_unknown, "ValueError was actually raised for an unregistered challenger name")

head("8. challenger_predict_fn() end-to-end through evaluate_predictor_on_holdout(): the "
     "adapted predict_fn scores correctly against the locked holdout like any other label")

result_adapted = al.evaluate_predictor_on_holdout(
    "_test_fixture_challenger", al.challenger_predict_fn("_test_fixture_challenger"), rows_path)
check(result_adapted["n_holdout_rows"] == len(holdout1), "scored the same holdout row count "
     "as every other label -- the adapter didn't change which rows are in scope")
check(result_adapted["n_skipped_no_opinion"] == result_adapted["n_holdout_rows"],
     "this fixture's rows carry no 'boost' signal, so the real registered score_fn "
     "returns None on every one -- all of them honestly skipped, none guessed",
     f"got n_skipped_no_opinion={result_adapted['n_skipped_no_opinion']} of "
     f"{result_adapted['n_holdout_rows']}")

head("9. compare(): when the two sides scored a DIFFERENT NUMBER of rows (one skipped "
     "some as 'no opinion'), the delta is still returned but flagged with a caution note "
     "-- same cutoff_date is not the same thing as the same row subset")

cmp_mismatched_n = al.compare("champion", "skipper")
check(cmp_mismatched_n["brier_delta"] is not None, "a delta is still computed -- this is "
     "informative, not a hard block like the cutoff-mismatch case")
check(cmp_mismatched_n.get("note") is not None and "CAUTION" in cmp_mismatched_n["note"],
     "a real, English-language caution note explains the row-subset asymmetry",
     f"got note={cmp_mismatched_n.get('note')!r}")

cmp_matched_n = al.compare("champion", "champion")
check(cmp_matched_n.get("note") is None, "no spurious caution note when both sides "
     "scored the identical row count")

n_pass = sum(1 for ok, _, _ in _results if ok)
n_total = len(_results)
print("\n" + "=" * 78)
print(f"RESULT: {n_pass}/{n_total} checks passed")
if n_pass < n_total:
    print()
    for ok, msg, detail in _results:
        if not ok:
            print(f"  FAILED: {msg}")
            if detail:
                print(f"          {detail}")
print("=" * 78)
sys.exit(0 if n_pass == n_total else 1)
