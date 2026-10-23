"""Synthetic, nonconfirmatory same-run replay and FC-OPS-002 attacks."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from research.fc_ops_001.offer_contract import parse_archived_page
from research.fc_ops_002.bridge import (
    BridgeError, SYNTHETIC, build_ledger, canonical_bytes, evaluate_external_grades, frozen_probability,
    sha256, verify_ledger, write_once,
)

DATE = "2031-05-01"  # far-future synthetic date; no live or historical market assertion
OBS = DATE + "T16:10:00Z"
PREPARED = DATE + "T16:20:00Z"
CUTOFF = DATE + "T16:30:00Z"
START = DATE + "T17:00:00Z"
REQUEST = "event-page?eventId=E1&tab=batter-props"
A = "fc2:G1:player-P1:hits:1:over"
B = "fc2:G1:player-P2:total_bases:2:over"
OFFER_KEYS = ("raw_response_sha256", "sportsbook_event_id", "market_id", "market_type", "selection_id",
              "player_norm", "market", "side", "line", "needs", "american_odds")


def seal(body, field):
    body[field] = sha256(body)
    return body


def runner(name, sid, odds):
    return {"runnerName": name, "runnerStatus": "ACTIVE", "selectionId": sid,
            "winRunnerOdds": {"americanDisplayOdds": {"americanOddsInt": odds}}}


def market(mid, mt, player, sid, odds):
    return {"marketId": mid, "marketType": mt, "eventId": "E1", "marketStatus": "OPEN",
            "inPlay": False, "runners": [runner(player, sid, odds)]}


def fixture():
    root = canonical_bytes({"attachments": {"events": {"E1": {"eventId": "E1", "name": "Away @ Home",
                                                      "openDate": START},
                                                  "FUT": {"eventId": "FUT", "name": "World Series Winner",
                                                          "openDate": DATE + "T00:00:00Z"}}}})
    page = canonical_bytes({"attachments": {"markets": {
        "h": market("M1", "PLAYER_TO_RECORD_A_HIT", "Alice", "S1", -200),
        "tb": market("M2", "TO_RECORD_2+_TOTAL_BASES", "Bob", "S2", 150)}}})
    page_sha, root_sha = sha256(page), sha256(root)
    archive = {page_sha: page, root_sha: root}
    bindings = {"E1": {"game_pk": "G1", "sportsbook_start": START, "event_start": START,
                       "player_ids": {"alice": "P1", "bob": "P2"},
                       "teams_by_player_id": {"P1": "AWY", "P2": "HOM"}}}
    parsed, rejected = parse_archived_page(page, expected_sha256=page_sha, source_request=REQUEST,
                                           source_host="synthetic.example.invalid", sportsbook_event_id="E1",
                                           game_pk="G1", event_start=START, observed_at=OBS,
                                           decision_at=CUTOFF, player_ids=bindings["E1"]["player_ids"])
    assert len(parsed) == 2 and not rejected
    by_market = {o["market"]: o for o in parsed}

    def candidate(cid, player, pid, team, stat, needs, odds, p, top):
        o = by_market[stat]
        s1_offer = {k: o[k] for k in ("sportsbook", "sportsbook_event_id", "market_id", "market_type",
                                      "selection_id", "market", "side", "line", "needs", "american_odds",
                                      "raw_response_sha256", "break_even_raw", "fair_novig")}
        s1_offer.update(player_name=player, request_path=REQUEST, observation_completed_at=OBS,
                        availability_verified=True, availability_unverified_fields=[])
        s1_offer["offer_id"] = sha256({k: o[k] for k in OFFER_KEYS})
        return {"candidate_id": cid, "game_pk": "G1", "player_id": pid, "player_name": player,
                "team": team, "stat": stat, "side": "over", "needs": needs, "line": o["line"],
                "p": p, "reliability": "A", "qc_status": "kept", "lineup_assumed": False,
                "recommendation_status": "top_pick" if top else "lean", "candidate_odds": odds,
                "offer_status": "AUTHENTICATED", "offer": s1_offer,
                "alternative_policy_eligible": True}

    ca = candidate(A, "Alice", "P1", "AWY", "hits", 1, -200, .72, True)
    cb = candidate(B, "Bob", "P2", "HOM", "total_bases", 2, 150, .55, False)
    capture = {"status": "COMPLETE", "capture_started_at": DATE + "T16:00:00Z",
               "capture_completed_at": DATE + "T16:15:00Z",
               "root": {"status": "OK", "source": "fanduel_sportsbook_api",
                        "request": "content-managed-page?page=CUSTOM&customPageId=mlb&_ak=synthetic",
                        "started_at": DATE + "T16:00:00Z", "completed_at": DATE + "T16:01:00Z",
                        "raw_sha256": root_sha},
               "events": [{"event_id": "E1", "event_name": "Away @ Home", "open_date": START,
                           "status": "COMPLETE", "requests": [{"status": "OK", "source": "fanduel_sportsbook_api",
                            "started_at": DATE + "T16:05:00Z", "raw_sha256": page_sha,
                            "request": REQUEST, "host": "synthetic.example.invalid", "completed_at": OBS}]}]}
    seal(capture, "capture_sha256")
    s1 = seal({"manifest_version": "fc-acc-001-s1-manifest-2",
               "unit": {"date": DATE, "window": "DAY", "decision_at": CUTOFF},
               "inputs": {"capture": capture}, "unit_games": ["G1"], "game_issues": {},
               "event_mapping_issues": {}, "unit_status": "COMPARABLE", "candidates": [ca, cb]},
              "manifest_sha256")
    publication_manifest = {"artifact_id": "synthetic-pages-artifact", "candidates": [{"canonical_id": A}]}
    champion = seal({"schema_version": "fc-acc-009-dashboard-decision-1", "research_only": True,
                     "publication_status": "INTENDED_NOT_EXPOSURE_PROVEN",
                     "run": {"github_run_id": "SYNTHETIC-RUN", "prepared_at": PREPARED},
                     "counts": {"new_publication_policy_N": 1, "staged_publication_candidates": 1},
                     "decision_ids": {"new_publication_candidates": [A],
                                      "staged_manifest_candidates": [A], "withheld_after_preparation": []},
                     "decision_rows": [{"canonical_id": A, "game_pk": "G1", "player_id": "P1", "team": "AWY",
                                        "market": "hits", "needs": 1, "wager_side": "over", "line": .5,
                                        "market_odds": -200, "hit_probability": .72,
                                        "recommendation_status": "top_pick", "new_publication_candidate": True,
                                        "already_public": False}],
                     "observed_games": ["G1"], "coverage_status": "UNVERIFIED",
                     "preliminary_manifest": {"candidates": [{"canonical_id": A}]},
                     "manifest_sha256": sha256(publication_manifest),
                     "manifest_artifact_id": publication_manifest["artifact_id"]}, "record_sha256")
    model = {"total_bases": {"keys": ["base_logit"], "median": [0.0],
                             "missing_indicator_for": [], "mu": [0.0], "sd": [1.0],
                             "coef": [2.2, 0.0], "coef_order": ["intercept", "base_logit"]}}
    feature_source = {"candidate_id": B, "observed_at": OBS, "values": {"base_logit": .2}}
    feature_raw = canonical_bytes(feature_source)
    archive[sha256(feature_raw)] = feature_raw
    feature = {B: {**feature_source, "status": "COMPLETE", "source_sha256": sha256(feature_raw)}}
    policy_source = {"candidate_id": B, "observed_at": OBS, "baseline_p": .55,
                     "baseline_ci_lower": .4, "lineup_confirmed": True, "data_fresh": True,
                     "sample_n": 20, "reliability": "A"}
    policy_raw = canonical_bytes(policy_source)
    archive[sha256(policy_raw)] = policy_raw
    policy = {B: {**policy_source, "source_sha256": sha256(policy_raw)}}
    return {"champion_export": champion, "publication_manifest": publication_manifest,
            "s1_manifest": s1, "raw_archive": archive,
            "event_bindings": bindings, "frozen_model_bytes": canonical_bytes(model),
            "feature_snapshots": feature, "policy_snapshots": policy, "evidence_class": SYNTHETIC}


def reseal(args):
    capture = args["s1_manifest"]["inputs"]["capture"]
    capture.pop("capture_sha256", None)
    seal(capture, "capture_sha256")
    for key, field in (("champion_export", "record_sha256"), ("s1_manifest", "manifest_sha256")):
        body = args[key]
        body.pop(field, None)
        seal(body, field)
    return args


def change_page(args, mutator):
    s1 = args["s1_manifest"]
    old = s1["candidates"][0]["offer"]["raw_response_sha256"]
    page = json.loads(args["raw_archive"].pop(old))
    mutator(page)
    raw = canonical_bytes(page)
    new = sha256(raw)
    args["raw_archive"][new] = raw
    req = s1["inputs"]["capture"]["events"][0]["requests"][0]
    req["raw_sha256"] = new
    for c in s1["candidates"]:
        offer = c["offer"]
        offer["raw_response_sha256"] = new
        original_identity = {k: offer[k] for k in OFFER_KEYS if k != "player_norm"}
        original_identity["player_norm"] = c["player_name"].lower()
        offer["offer_id"] = sha256(original_identity)
    return reseal(args)


class ReplayTests(unittest.TestCase):
    def test_complete_synthetic_replay_is_byte_identical_and_write_once(self):
        args = fixture()
        a = build_ledger(**args)
        b = build_ledger(**copy.deepcopy(args))
        self.assertEqual(canonical_bytes(a), canonical_bytes(b))
        self.assertTrue(verify_ledger(a))
        self.assertEqual(a["champion_N"], 1)
        self.assertEqual(a["unit_status"], "STRUCTURALLY_MATCHED")
        self.assertEqual(a["arms"], {"ACTUAL_INTENDED_CHAMPION": [A],
                                     "FROZEN_TB_CHALLENGER": [B], "MARKET_ONLY": [A]})
        self.assertEqual(len(a["eligible_universe"]), 2)
        self.assertFalse(a["prospective_certified"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            self.assertEqual(write_once(path, a), "CREATED")
            self.assertEqual(write_once(path, b), "ALREADY_IDENTICAL")
            changed = copy.deepcopy(a)
            changed["champion_N"] = 2
            changed.pop("ledger_sha256")
            seal(changed, "ledger_sha256")
            with self.assertRaisesRegex(BridgeError, "WRITE_ONCE_CONFLICT"):
                write_once(path, changed)

    def test_frozen_scoring_is_literal_and_missing_indicator_works(self):
        m = {"keys": ["x", "y"], "median": [2, 3], "missing_indicator_for": ["y"],
             "mu": [1, 1, 0], "sd": [2, 2, 1], "coef": [0, 1, 1, -1],
             "coef_order": ["intercept", "x", "y", "y__missing"]}
        expected = 1 / (1 + __import__("math").exp(-.5))
        self.assertAlmostEqual(frozen_probability(m, {"x": 2}), expected)
        with self.assertRaisesRegex(BridgeError, "FEATURE_VALUE_INVALID"):
            frozen_probability(m, {"x": float("nan")})

    def test_corrupt_seals_and_model_identity_fail_closed(self):
        for mutate, reason in [
            (lambda x: x["champion_export"]["counts"].update(new_publication_policy_N=9), "BAD_RECORD_SHA256"),
            (lambda x: x["s1_manifest"]["unit"].update(date="2031-05-02"), "BAD_MANIFEST_SHA256"),
            (lambda x: x.update(evidence_class="PROSPECTIVE_AUTHENTICATED"), "PROSPECTIVE_CERTIFICATION_UNSUPPORTED"),
        ]:
            with self.subTest(reason=reason):
                args = fixture()
                mutate(args)
                with self.assertRaisesRegex(BridgeError, reason):
                    build_ledger(**args)

    def test_publication_manifest_ids_are_bound_but_exposure_unproven(self):
        args = fixture()
        out = build_ledger(**args)
        self.assertEqual(out["publication_binding"]["staged_candidate_ids"], [A])
        self.assertEqual(out["exposure"], "STAGED_MANIFEST_NOT_EXPOSURE_PROVEN")
        args["publication_manifest"]["candidates"] = []
        with self.assertRaisesRegex(BridgeError, "PUBLICATION_MANIFEST_MISMATCH"):
            build_ledger(**args)

    def test_root_census_and_missing_event_mapping(self):
        args = fixture()
        args["event_bindings"] = {}
        with self.assertRaisesRegex(BridgeError, "ROOT_EVENT_BINDING_MISSING"):
            build_ledger(**args)
        args = fixture()
        args["s1_manifest"]["unit_games"] = ["G1", "G2"]
        reseal(args)
        self.assertIn("INCOMPLETE_GAME_BINDINGS", build_ledger(**args)["non_comparable_reasons"])

    def test_closed_inactive_suspended_and_cross_player_line_fail(self):
        changes = [
            (lambda p: p["attachments"]["markets"]["h"].update(marketStatus="CLOSED"), "MARKET_NOT_OPEN"),
            (lambda p: p["attachments"]["markets"]["h"]["runners"][0].update(runnerStatus="INACTIVE"), "RUNNER_NOT_ACTIVE"),
            (lambda p: p["attachments"]["markets"]["tb"].update(marketStatus="SUSPENDED"), "MARKET_NOT_OPEN"),
            # The archived response hash is part of every offer identity. This
            # mutation is stopped at provenance before the name mismatch.
            (lambda p: p["attachments"]["markets"]["tb"]["runners"][0].update(runnerName="Alice"), "S1_OFFER_PROVENANCE_INVALID"),
        ]
        for change, why in changes:
            with self.subTest(why=why):
                args = change_page(fixture(), change)
                out = build_ledger(**args)
                self.assertTrue(any(why in x["reason"] for x in out["rejected_opportunities"]))
                self.assertEqual(out["unit_status"], "NON_COMPARABLE" if why in ("MARKET_NOT_OPEN", "RUNNER_NOT_ACTIVE")
                                 and out["arms"]["ACTUAL_INTENDED_CHAMPION"] == [] else "STRUCTURALLY_MATCHED")

    def test_changed_line_price_player_and_source_hash_reject(self):
        for change, why in [
            (lambda x: x["s1_manifest"]["candidates"][0]["offer"].update(line=1.5), "S1_OFFER_IDENTITY_MISMATCH"),
            (lambda x: x["s1_manifest"]["candidates"][0]["offer"].update(american_odds=-300), "S1_OFFER_IDENTITY_MISMATCH"),
            (lambda x: x["s1_manifest"]["candidates"][0].update(player_id="P9"), "PLAYER_IDENTITY_MISMATCH"),
            (lambda x: x["s1_manifest"]["candidates"][0]["offer"].update(raw_response_sha256=None), "CAPTURE_REQUEST_MISSING_OR_DUPLICATE"),
        ]:
            with self.subTest(why=why):
                args = fixture()
                change(args)
                reseal(args)
                out = build_ledger(**args)
                self.assertTrue(any(why in x["reason"] for x in out["rejected_opportunities"]))
                self.assertEqual(out["champion_N"], 1)
                self.assertEqual(out["arms"]["ACTUAL_INTENDED_CHAMPION"], [])

    def test_stale_late_and_missing_observation_reject(self):
        for ts in (DATE + "T15:00:00Z", DATE + "T16:31:00Z", None):
            with self.subTest(ts=ts):
                args = fixture()
                capture = args["s1_manifest"]["inputs"]["capture"]
                capture["events"][0]["requests"][0]["completed_at"] = ts
                if ts == DATE + "T15:00:00Z":
                    capture["capture_started_at"] = DATE + "T14:45:00Z"
                    capture["root"]["started_at"] = DATE + "T14:45:00Z"
                    capture["root"]["completed_at"] = DATE + "T14:50:00Z"
                    capture["events"][0]["requests"][0]["started_at"] = DATE + "T14:55:00Z"
                    capture["capture_completed_at"] = DATE + "T15:05:00Z"
                for c in args["s1_manifest"]["candidates"]:
                    c["offer"]["observation_completed_at"] = ts
                reseal(args)
                if ts == DATE + "T15:00:00Z":
                    out = build_ledger(**args)
                    self.assertEqual(out["unit_status"], "NON_COMPARABLE")
                    self.assertEqual(out["arms"]["ACTUAL_INTENDED_CHAMPION"], [])
                else:
                    with self.assertRaises(BridgeError):
                        build_ledger(**args)

    def test_unconfirmed_lineup_starter_change_and_late_features(self):
        args = fixture()
        args["s1_manifest"]["candidates"][1]["lineup_assumed"] = True
        reseal(args)
        out = build_ledger(**args)
        self.assertEqual(out["arms"]["FROZEN_TB_CHALLENGER"], [A])
        self.assertIn({"candidate_id": B, "reason": "PREGAME_ELIGIBILITY_UNVERIFIED"}, out["rejected_opportunities"])
        args = fixture()
        args["s1_manifest"]["game_issues"] = {"G1": "STARTER_CHANGED"}
        reseal(args)
        self.assertIn("GAME_OR_EVENT_MAPPING_ISSUE", build_ledger(**args)["non_comparable_reasons"])
        args = fixture()
        args["feature_snapshots"][B]["observed_at"] = DATE + "T16:31:00Z"
        feature_source = {k: args["feature_snapshots"][B][k] for k in ("candidate_id", "observed_at", "values")}
        feature_raw = canonical_bytes(feature_source)
        args["raw_archive"][sha256(feature_raw)] = feature_raw
        args["feature_snapshots"][B]["source_sha256"] = sha256(feature_raw)
        self.assertIn({"candidate_id": B, "reason": "LATE_FEATURE_SNAPSHOT"},
                      build_ledger(**args)["rejected_opportunities"])

    def test_zero_pick_and_unavailable_tb_never_manufacture_volume(self):
        args = fixture()
        ch = args["champion_export"]
        ch["counts"]["new_publication_policy_N"] = 0
        ch["counts"]["staged_publication_candidates"] = 0
        ch["decision_ids"]["new_publication_candidates"] = []
        ch["decision_ids"]["staged_manifest_candidates"] = []
        ch["preliminary_manifest"]["candidates"] = []
        args["publication_manifest"]["candidates"] = []
        ch["manifest_sha256"] = sha256(args["publication_manifest"])
        ch["decision_rows"][0]["recommendation_status"] = "lean"
        ch["decision_rows"][0]["new_publication_candidate"] = False
        reseal(args)
        out = build_ledger(**args)
        self.assertEqual(out["unit_status"], "ZERO_CHAMPION")
        self.assertTrue(all(not v for v in out["arms"].values()))
        args = fixture()
        args["s1_manifest"]["candidates"][1].update(offer_status="MISSING_QUOTE", offer=None)
        reseal(args)
        out = build_ledger(**args)
        self.assertEqual(out["arms"]["FROZEN_TB_CHALLENGER"], [A])
        self.assertTrue(any(r["reason"] == "MISSING_QUOTE" for r in out["rejected_opportunities"]))

    def test_insufficient_count_duplicate_and_partial_capture(self):
        args = fixture()
        ch = args["champion_export"]
        ch["counts"]["new_publication_policy_N"] = 2
        ch["decision_ids"]["new_publication_candidates"].append(B)
        ch["decision_ids"]["withheld_after_preparation"] = [B]
        ch["preliminary_manifest"]["candidates"].append({"canonical_id": B})
        ch["decision_rows"].append({"canonical_id": B, "game_pk": "G1", "player_id": "P2", "team": "HOM",
                                    "market": "total_bases", "needs": 2, "wager_side": "over", "line": 1.5,
                                    "market_odds": 150, "recommendation_status": "top_pick",
                                    "new_publication_candidate": True, "already_public": False})
        args["s1_manifest"]["candidates"][1].update(offer_status="MISSING_QUOTE", offer=None)
        reseal(args)
        out = build_ledger(**args)
        self.assertEqual(out["champion_N"], 2)
        self.assertEqual(out["shortfalls"]["ACTUAL_INTENDED_CHAMPION"], 1)
        self.assertEqual(out["unit_status"], "NON_COMPARABLE")
        args = fixture()
        args["s1_manifest"]["candidates"].append(copy.deepcopy(args["s1_manifest"]["candidates"][0]))
        reseal(args)
        with self.assertRaisesRegex(BridgeError, "DUPLICATE_S1_CANDIDATE"):
            build_ledger(**args)
        args = fixture()
        args["s1_manifest"]["inputs"]["capture"]["status"] = "INCOMPLETE"
        reseal(args)
        self.assertIn("INCOMPLETE_CAPTURE", build_ledger(**args)["non_comparable_reasons"])

    def test_book_and_push_rules_are_not_silently_substituted(self):
        args = fixture()
        args["s1_manifest"]["candidates"][0]["offer"]["sportsbook"] = "other_book"
        reseal(args)
        self.assertEqual(build_ledger(**args)["arms"]["ACTUAL_INTENDED_CHAMPION"], [])
        args = change_page(fixture(), lambda p: p["attachments"]["markets"]["tb"].update(
            marketType="PITCHER_A_TOTAL_STRIKEOUTS",
            runners=[runner("Bob Over", "S2", 150) | {"handicap": 2.0},
                     runner("Bob Under", "S3", -180) | {"handicap": 2.0}]))
        out = build_ledger(**args)
        self.assertTrue(any("UNSUPPORTED_PUSH_LINE" in r["reason"] for r in out["rejected_opportunities"]))

    def test_missing_features_do_not_use_postgame_substitution(self):
        args = fixture()
        args["feature_snapshots"].clear()
        out = build_ledger(**args)
        self.assertIn({"candidate_id": B, "reason": "FROZEN_FEATURES_MISSING"}, out["rejected_opportunities"])
        self.assertEqual(out["arms"]["FROZEN_TB_CHALLENGER"], [A])
        self.assertFalse(out["prospective_certified"])

    def test_external_grade_link_keeps_settlement_and_roi_separate(self):
        ledger = build_ledger(**fixture())
        rows = {r["candidate_id"]: r for r in ledger["eligible_universe"]}
        archive, refs = {}, {}
        for cid, status in ((A, "LOSS"), (B, "WIN")):
            r = rows[cid]
            payload = {k: r[k] for k in ("candidate_id", "game_pk", "player_id", "market",
                                          "side", "line", "needs", "offer_id")}
            payload.update(status=status, settled_at=DATE + "T23:00:00Z", grader_version="synthetic-1")
            raw = canonical_bytes(payload)
            refs[cid], archive[sha256(raw)] = sha256(raw), raw
        linked = evaluate_external_grades(ledger, archive, refs)
        self.assertFalse(linked["grade_truth_independently_verified"])
        self.assertEqual(linked["arms"]["ACTUAL_INTENDED_CHAMPION"]["flat_stake_net"], -1)
        self.assertEqual(linked["arms"]["FROZEN_TB_CHALLENGER"]["flat_stake_net"], 1.5)
        del refs[B]
        self.assertIsNone(evaluate_external_grades(ledger, archive, refs)["arms"]["FROZEN_TB_CHALLENGER"]["flat_stake_roi"])
        grade = json.loads(archive[linked["grades"][A]["grade_sha256"]])
        grade["player_id"] = "P9"
        bad = canonical_bytes(grade)
        with self.assertRaisesRegex(BridgeError, "GRADE_IDENTITY_MISMATCH"):
            evaluate_external_grades(ledger, {sha256(bad): bad}, {A: sha256(bad)})
        grade["player_id"] = "P1"
        grade["status"] = "VOID"
        bad = canonical_bytes(grade)
        with self.assertRaisesRegex(BridgeError, "VOID_PUSH_RULE_UNVERIFIED"):
            evaluate_external_grades(ledger, {sha256(bad): bad}, {A: sha256(bad)})


    def test_alternative_policy_evidence_is_mandatory(self):
        args = fixture()
        args["policy_snapshots"].clear()
        out = build_ledger(**args)
        self.assertIn({"candidate_id": B, "reason": "ALTERNATIVE_POLICY_EVIDENCE_MISSING"},
                      out["rejected_opportunities"])
        self.assertEqual(out["arms"]["FROZEN_TB_CHALLENGER"], [A])
        args = fixture()
        args["frozen_model_bytes"] = canonical_bytes({"total_bases": {
            "keys": ["base_logit"], "median": [0], "missing_indicator_for": [],
            "mu": [0], "sd": [1], "coef": [0, 0],
            "coef_order": ["intercept", "base_logit"]}})
        out = build_ledger(**args)
        self.assertIn({"candidate_id": B, "reason": "ALTERNATIVE_TOP_PICK_FLOOR_FAIL"},
                      out["rejected_opportunities"])
        args = fixture()
        args["policy_snapshots"][B]["lineup_confirmed"] = False
        out = build_ledger(**args)
        self.assertIn({"candidate_id": B, "reason": "ALTERNATIVE_POLICY_SOURCE_MISMATCH"},
                      out["rejected_opportunities"])


if __name__ == "__main__":
    unittest.main()
