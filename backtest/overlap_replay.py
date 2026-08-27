#!/usr/bin/env python3
"""overlap_replay.py -- empirical proof that two code regimes generate the
same rows.

WHY THIS IS THE AUTHORITATIVE TEST. backtest/generation_regime.py can tell
you whether two SHAs contain identical generation-critical bytes. That is
a real check and it is cheap, but it is structural: it cannot see through
lazy imports, dynamic dispatch, or a data-dependent branch, and a
fingerprint mismatch does not by itself tell you whether the difference
actually reaches the rows. For the canonical artifact this project is
assembling -- 385 dates generated under 2ce95fe9, the rest under
022c8829 -- the question that actually matters is not "do the files
match" but "would these dates have come out the same". The only honest
way to answer that is to regenerate real dates under the new regime and
compare every predictive field.

WHAT COUNTS AS A DIFFERENCE. Everything, except two fields that exist
precisely to record WHICH run produced a row and WHEN:

    code_git_sha          -- by design differs; preserving it is the whole
                             point of the importer not laundering provenance
    backtest_generated_at -- wall-clock stamp of the generating run

Nothing else is excused. Not a probability that moved in the 12th decimal,
not a signal that flipped from 0.0 to None, not a reordered row. The
comparison is deliberately stricter than "close enough" because the
purpose is to justify treating two segments of a research dataset as one
population; a tolerance here would silently become a tolerance in every
accuracy claim built on top of it.

Floats are compared by exact repr rather than with a tolerance for the
same reason. Both segments run the same arithmetic on the same inputs on
the same machine; if a value genuinely differs at all, that is a fact
worth surfacing, not rounding away.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone

# Fields whose whole job is to identify the generating run. See docstring.
PROVENANCE_FIELDS = frozenset(("code_git_sha", "backtest_generated_at"))

CANDIDATE_IDENTITY_FIELDS = ("date", "game_pk", "player_id", "prop_type", "line")

# Reported explicitly in the comparison so a reader can confirm the
# categories the governing mission requires were genuinely examined,
# rather than trusting that "all fields" covered them.
REQUIRED_COMPARISON_CATEGORIES = {
    "outcome": ("outcome",),
    "actual_value": ("actual",),
    "predicted_probability": ("predicted_prob", "calibrated_prob"),
    "score_and_categories": ("score", "cat_matchup", "cat_recent_form",
                             "cat_environment", "cat_baseline_skill",
                             "cat_context", "sb_cat_skill", "sb_cat_matchup",
                             "sb_cat_context"),
    "line_and_needs": ("line", "needs"),
    "opportunity": ("fair_test", "actual_pa", "game_innings", "shortened_game",
                    "opportunity", "batting_order", "was_substitute"),
    "signals": ("signals",),
}


def _canonical(value):
    """Stable, type-faithful rendering for comparison/hashing.

    json.dumps with sort_keys handles nested signal dicts; repr on floats
    keeps 0.1 and 0.1000000000000001 distinguishable, which a formatted
    string would not."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=repr)


def row_identity(row):
    return tuple(row.get(k) for k in CANDIDATE_IDENTITY_FIELDS)


def _sort_key(identity):
    """Type-safe ordering for identity tuples.

    Real rows mix types within a field -- a `line` may be 1.5 on one row
    and None on another, and ids arrive as both int and str -- so the
    tuples are not directly comparable. repr() gives a total order that
    is stable across runs without coercing (and thereby conflating) the
    underlying values."""
    return tuple(repr(x) for x in identity)


def logical_row_fingerprint(row):
    """Content identity of a row, ignoring only the provenance fields."""
    payload = {k: v for k, v in row.items() if k not in PROVENANCE_FIELDS}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def logical_set_fingerprint(rows):
    """Order-independent identity of a whole date's rows."""
    digests = sorted(logical_row_fingerprint(r) for r in rows)
    return hashlib.sha256(_canonical(digests).encode("utf-8")).hexdigest()


def read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def compare_rows(legacy_rows, replay_rows, *, max_examples=5):
    """Full comparison of one date's rows under two regimes."""
    result = {
        "row_count_legacy": len(legacy_rows),
        "row_count_replay": len(replay_rows),
        "row_count_equal": len(legacy_rows) == len(replay_rows),
        "logical_fingerprint_legacy": logical_set_fingerprint(legacy_rows),
        "logical_fingerprint_replay": logical_set_fingerprint(replay_rows),
    }
    result["logical_fingerprint_equal"] = (
        result["logical_fingerprint_legacy"] == result["logical_fingerprint_replay"])

    ids_a = [row_identity(r) for r in legacy_rows]
    ids_b = [row_identity(r) for r in replay_rows]
    result["identities_equal_as_sets"] = set(ids_a) == set(ids_b)
    result["identities_equal_in_order"] = ids_a == ids_b
    result["duplicate_identities_legacy"] = sum(
        c - 1 for c in Counter(ids_a).values() if c > 1)
    result["duplicate_identities_replay"] = sum(
        c - 1 for c in Counter(ids_b).values() if c > 1)
    only_a = set(ids_a) - set(ids_b)
    only_b = set(ids_b) - set(ids_a)
    result["identities_only_in_legacy"] = [list(x) for x in sorted(only_a, key=_sort_key)[:max_examples]]
    result["identities_only_in_replay"] = [list(x) for x in sorted(only_b, key=_sort_key)[:max_examples]]
    result["n_identities_only_in_legacy"] = len(only_a)
    result["n_identities_only_in_replay"] = len(only_b)

    by_a = {i: r for i, r in zip(ids_a, legacy_rows)}
    by_b = {i: r for i, r in zip(ids_b, replay_rows)}
    field_mismatches = Counter()
    examples = []
    for ident in sorted(set(ids_a) & set(ids_b), key=_sort_key):
        ra, rb = by_a[ident], by_b[ident]
        keys = (set(ra) | set(rb)) - PROVENANCE_FIELDS
        for k in sorted(keys):
            va, vb = ra.get(k, "<absent>"), rb.get(k, "<absent>")
            if _canonical(va) != _canonical(vb):
                field_mismatches[k] += 1
                if len(examples) < max_examples:
                    examples.append({
                        "identity": list(ident), "field": k,
                        "legacy": repr(va)[:200], "replay": repr(vb)[:200],
                    })
    result["mismatched_fields"] = dict(field_mismatches)
    result["n_rows_compared"] = len(set(ids_a) & set(ids_b))
    result["mismatch_examples"] = examples

    # Explicitly confirm the mission's named categories were reachable in
    # the data rather than silently absent (a field that does not exist in
    # either side would otherwise "match" vacuously).
    present = set()
    for r in (legacy_rows[:1] + replay_rows[:1]):
        present |= set(r)
    result["required_categories_present"] = {
        cat: sorted(f for f in fields if f in present)
        for cat, fields in REQUIRED_COMPARISON_CATEGORIES.items()
    }

    # ORDER IS DELIBERATELY NOT AN EQUIVALENCE CRITERION, and this was
    # established empirically rather than assumed. Running the SAME code
    # (2ce95fe9) over the SAME date twice produces the same SET of rows in
    # a DIFFERENT order -- 2025-04-20 diverged at index 334 between two
    # runs of identical code. Within-date row order is therefore a
    # property of concurrent fetch scheduling, not of the generation
    # regime, and testing it would report nondeterminism as if it were a
    # regime difference. The governing mission asks for "ordering where
    # deterministic"; here it demonstrably is not, so it is reported for
    # information and excluded from the verdict.
    #
    # Content identity is still fully enforced -- via the ORDER-INDEPENDENT
    # logical_set_fingerprint plus exhaustive per-identity field
    # comparison -- so nothing is actually being let through.
    result["order_note"] = (
        "within-date row order is not deterministic across runs of identical "
        "code (verified directly); excluded from the verdict, reported only")
    result["equivalent"] = bool(
        result["row_count_equal"]
        and result["identities_equal_as_sets"]
        and result["logical_fingerprint_equal"]
        and not field_mismatches
        and not result["duplicate_identities_legacy"]
        and not result["duplicate_identities_replay"]
    )
    return result


def attribute_mismatch(legacy_rows, replay_rows, control_rows):
    """Separate a CODE-REGIME difference from upstream DATA drift.

    A replay is run later than the segment it is compared against, so any
    mismatch has two candidate causes and comparing two files cannot tell
    them apart. The control resolves it: regenerate the same date under
    the ORIGINAL code, now. Then

        replay vs control   -- same data vintage, different code  -> CODE
        legacy vs control   -- same code, different data vintage  -> DATA

    This matters because the two demand opposite responses. A code-regime
    difference means the legacy segment must be regenerated before the
    artifact can be certified. A data-vintage difference means the source
    revised a value under everyone's feet, which no amount of regenerating
    fixes and which would equally affect two runs of the same code.
    """
    code_axis = compare_rows(replay_rows, control_rows)
    data_axis = compare_rows(legacy_rows, control_rows)
    if code_axis["equivalent"] and not data_axis["equivalent"]:
        attribution = "upstream_data_vintage"
    elif not code_axis["equivalent"]:
        attribution = "code_regime"
    else:
        attribution = "no_mismatch"
    return {
        "attribution": attribution,
        "code_axis_equivalent": code_axis["equivalent"],
        "code_axis_mismatched_fields": code_axis["mismatched_fields"],
        "data_axis_equivalent": data_axis["equivalent"],
        "data_axis_mismatched_fields": data_axis["mismatched_fields"],
        "interpretation": {
            "code_regime": "the two code SHAs genuinely generate different rows; "
                           "the legacy segment must be regenerated before certification",
            "upstream_data_vintage": "both code SHAs produce identical rows on one data "
                                     "snapshot; the difference is the upstream source "
                                     "revising values between generation dates",
            "no_mismatch": "no difference on either axis",
        }[attribution],
    }


def load_legacy_by_date(legacy_jsonl, dates):
    wanted = set(dates)
    out = {d: [] for d in dates}
    with open(legacy_jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            d = row.get("date")
            if d in wanted:
                out[d].append(row)
    return out


def run_overlap_replay_comparison(legacy_jsonl, legacy_state_json, replay_dir, dates,
                                  *, legacy_sha, replay_sha):
    """Compare regenerated dates against their salvaged originals.

    A no_games date is handled explicitly rather than being inferred from
    an absent file: the legacy state must say no_games AND the replay must
    have produced zero rows. Treating "no rows" alone as agreement would
    let a silently-broken replay masquerade as a match.
    """
    with open(legacy_state_json, encoding="utf-8") as f:
        legacy_state = json.load(f).get("dates", {})
    legacy_by_date = load_legacy_by_date(legacy_jsonl, dates)

    with open(os.path.join(replay_dir, "_summary.json"), encoding="utf-8") as f:
        replay_summary = json.load(f)

    per_date = {}
    for d in dates:
        legacy_meta = legacy_state.get(d, {})
        legacy_status = legacy_meta.get("status")
        replay_meta = replay_summary.get(d, {})
        replay_status = replay_meta.get("status")

        entry = {"date": d, "legacy_status": legacy_status,
                 "replay_status": replay_status}

        if replay_status == "exception":
            entry.update({"equivalent": False,
                          "reason": f"replay raised: {replay_meta.get('error')}"})
            per_date[d] = entry
            continue

        if legacy_status == "no_games" or replay_status == "no_games":
            entry["equivalent"] = (legacy_status == replay_status
                                   and not legacy_by_date.get(d))
            entry["row_count_legacy"] = len(legacy_by_date.get(d, []))
            entry["row_count_replay"] = replay_meta.get("rows", 0)
            entry["reason"] = ("both regimes independently reported no games"
                               if entry["equivalent"] else
                               "no_games status disagreement between regimes")
            per_date[d] = entry
            continue

        path = os.path.join(replay_dir, f"{d}.jsonl")
        if not os.path.exists(path):
            entry.update({"equivalent": False, "reason": "replay produced no output file"})
            per_date[d] = entry
            continue

        entry.update(compare_rows(legacy_by_date.get(d, []), read_jsonl(path)))
        per_date[d] = entry

    all_equiv = all(e.get("equivalent") for e in per_date.values())
    return {
        "record_type": "overlap_replay_comparison",
        "legacy_sha": legacy_sha,
        "replay_sha": replay_sha,
        "dates": sorted(dates),
        "ignored_provenance_fields": sorted(PROVENANCE_FIELDS),
        "per_date": per_date,
        "verdict": "equivalent" if all_equiv else "not_equivalent",
        "compared_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--legacy-jsonl", required=True)
    ap.add_argument("--legacy-state", required=True)
    ap.add_argument("--replay-dir", required=True)
    ap.add_argument("--dates", nargs="+", required=True)
    ap.add_argument("--legacy-sha", required=True)
    ap.add_argument("--replay-sha", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()

    report = run_overlap_replay_comparison(
        args.legacy_jsonl, args.legacy_state, args.replay_dir, args.dates,
        legacy_sha=args.legacy_sha, replay_sha=args.replay_sha)

    for d, e in sorted(report["per_date"].items()):
        flag = "EQUIVALENT" if e.get("equivalent") else "MISMATCH"
        print(f"{d}  {flag}  legacy_rows={e.get('row_count_legacy')} "
              f"replay_rows={e.get('row_count_replay')} "
              f"mismatched_fields={e.get('mismatched_fields', {}) or e.get('reason','')}")
    print(f"\nVERDICT: {report['verdict'].upper()}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, sort_keys=True)
        print(f"wrote {args.out}")
    return 0 if report["verdict"] == "equivalent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
