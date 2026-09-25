"""Synthetic arithmetic/contract tests; no fixture is represented as a real quote."""
import copy
import unittest
from pathlib import Path
from unittest.mock import patch
from nfl.research.price_aware_offers import evaluate_offer, settle_record, frozen_distribution, write_evidence

T = "2026-09-22T20:00:00Z"
H = "a" * 64

def inputs():
    c = dict(source="fanduel_nfl", shape="primary", market="receptions", event_id="e",
             gsis_id="p", binding_status="BOUND", market_id="m", line=1,
             over_odds=150, under_odds=-180, over_selection_id="o", under_selection_id="u",
             captured_at=T, event_open_date="2026-09-24T00:00:00Z",
             availability_status="NOT_LISTED_INACTIVE", decision_status="SHADOW_ONLY")
    c.update(canonical_game_id="2026_03_ATL_GB", canonical_kickoff=c["event_open_date"], market_availability="AVAILABLE")
    c.update(team="GB", audience_jurisdiction="NJ",
             quote_timestamp=T, quote_timestamp_status="SOURCE_FIELD_VERIFIED",
             quote_evidence=dict(timestamp=T, source_sha256=H, market_id="m",
                                 source_field="market.priceUpdatedAt"),
             current_role_status="VERIFIED",
             current_role_evidence=dict(canonical_game_id=c["canonical_game_id"],
                                        gsis_id="p", team="GB", source_sha256=H,
                                        source_id="synthetic_role_fixture", role_basis="projected_routes",
                                        available_at=T),
             sportsbook_rule=dict(
        status="CERTIFIED",book="fanduel_nfl",market="receptions",event_id="e",
        url="https://www.fanduel.com/fanduel-sportsbook-house-rules-nj",
        source_sha256=H,observed_at=T, jurisdiction="NJ",
        settlement_stat="receptions", void_if_no_game_snap=True))
    c["authoritative_b0_status"]="JOINED"
    d = dict(model_version="SYNTHETIC", event_id="e", gsis_id="p", pmf=[.2,.3,.5],
             conditioning="PLAYED", feature_cutoff=T,
             source_available_at=T, generated_at=T, history_sha256=H)
    d["canonical_game_id"] = c["canonical_game_id"]
    # Explicit trailing zeros keep all offered thresholds inside known support.
    d["pmf"] += [0.0] * 10
    return c,d

def run(c,d,**kw):
    return evaluate_offer(c,d,as_of=kw.pop("as_of",T),raw_source_sha256=H,**kw)

class PriceTests(unittest.TestCase):
    def test_push_price_math_and_input_immutability(self):
        c,d=inputs(); before=copy.deepcopy((c,d)); r=run(c,d)
        self.assertEqual((c,d),before)
        self.assertEqual(r["decision_status"],"SHADOW_ONLY")
        p=r["prices"][0]
        self.assertAlmostEqual(p["win"],.5); self.assertAlmostEqual(p["push"],.3)
        self.assertAlmostEqual(p["expected_net_units"],.55)
        self.assertAlmostEqual(p["win_conditional_on_no_push"],5/7)

    def test_at_least_includes_boundary_no_push(self):
        c,d=inputs(); c.update(shape="alt_ladder",market="receptions_alt",threshold=1,yes_odds=150,selection_id="a")
        c["sportsbook_rule"]["market"]="receptions_alt"
        p=run(c,d)["prices"][0]
        self.assertAlmostEqual(p["win"],.8); self.assertEqual(p["push"],0)

    def test_primary_half_line_equals_alt_integer(self):
        c,d=inputs(); c["line"]=1.5
        a=run(c,d)["prices"][0]["win"]
        c.update(shape="alt_ladder",market="receptions_alt",threshold=2,yes_odds=150,selection_id="a")
        c["sportsbook_rule"]["market"]="receptions_alt"
        self.assertEqual(a,run(c,d)["prices"][0]["win"])

    def test_ladder_monotonic(self):
        c,d=inputs(); probs=[]
        for n in (1,2,3):
            c.update(shape="alt_ladder",market="receptions_alt",threshold=n,yes_odds=150,selection_id="a")
            c["sportsbook_rule"]["market"]="receptions_alt"
            probs.append(run(c,d)["prices"][0]["win"])
        self.assertEqual(probs,sorted(probs,reverse=True))

    def test_unknown_and_upstream_quarantine_never_promoted(self):
        for changes in ({"availability_status":"UNKNOWN_GAME_COVERAGE"},{"decision_status":"QUARANTINED"}, {"availability_status":"ELIGIBLE"}):
            c,d=inputs(); c.update(changes)
            self.assertEqual(run(c,d)["decision_status"],"QUARANTINED")

    def test_invalid_offer_contracts_fail_closed(self):
        for changes in ({"binding_status":"UNKNOWN"},{"gsis_id":"wrong"},{"over_odds":True},
                        {"over_odds":150.5},{"over_selection_id":"None"},{"line":1.25},
                        {"captured_at":"2026-09-22T20:00:01Z"},
                        {"captured_at":"2026-09-21T20:00:00Z"},
                        {"event_open_date":T},{"source":"other"}):
            c,d=inputs(); c.update(changes)
            with self.subTest(changes=changes):
                self.assertEqual(run(c,d)["decision_status"],"NO_PLAY")
                self.assertEqual(run(c,d)["prices"],[])

    def test_additional_fail_closed_states(self):
        for changes in ({"over_selection_id":"u"}, {"market_availability":"SUSPENDED"},
                        {"market_availability":"SOURCE_FAILED"}, {"over_odds":None},
                        {"canonical_game_id":"2026_03_GB_ATL"}, {"canonical_kickoff":T},
                        {"market":"passing_yards"}, {"quote_timestamp":"2026-09-21T20:00:00Z"}):
            c,d=inputs(); c.update(changes)
            self.assertEqual(run(c,d)["decision_status"],"NO_PLAY",changes)

    def test_uncertified_book_or_unknown_quote_quarantines(self):
        for changes in ({"sportsbook_rule":None},{"quote_timestamp":None},
                        {"current_role_status":"UNKNOWN_GAME_COVERAGE"},
                        {"sportsbook_rule":{"status":"CERTIFIED","book":"other"}}):
            c,d=inputs(); c.update(changes)
            self.assertEqual(run(c,d)["decision_status"],"QUARANTINED",changes)

    def test_status_flags_alone_cannot_clear_three_evidence_gates(self):
        c,d=inputs()
        for key,reason in (("quote_evidence","QUOTE_PROVENANCE_UNVERIFIED"),
                           ("current_role_evidence","CURRENT_ROLE_EVIDENCE_MISSING"),
                           ("audience_jurisdiction","BOOK_ACTION_RULES_EVIDENCE_MISSING_OR_MISMATCHED")):
            changed=copy.deepcopy(c); changed.pop(key)
            with self.subTest(key=key):
                result=run(changed,d)
                self.assertEqual(result["decision_status"],"QUARANTINED")
                self.assertIn(reason,result["reasons"])
                self.assertFalse(result["bettable"])
        changed=copy.deepcopy(c); changed["quote_evidence"]["source_sha256"]="b"*64
        self.assertIn("QUOTE_PROVENANCE_UNVERIFIED",run(changed,d)["reasons"])
        changed=copy.deepcopy(c); changed["current_role_evidence"]["available_at"]="2026-09-23T00:00:00Z"
        self.assertIn("CURRENT_ROLE_EVIDENCE_MISSING",run(changed,d)["reasons"])
        changed=copy.deepcopy(c); changed["sportsbook_rule"]["void_if_no_game_snap"]=False
        self.assertIn("BOOK_ACTION_RULES_EVIDENCE_MISSING_OR_MISMATCHED",run(changed,d)["reasons"])

    def test_atomic_create_only_and_failed_write(self):
        td=Path.cwd()/"engineering"/"nfl_price_aware_20260923"
        path=td/"atomic_writer_test.json"
        failed=td/"atomic_writer_failure_test.json"
        path.unlink(missing_ok=True); failed.unlink(missing_ok=True)
        try:
            write_evidence(path,{"sealed":1})
            before=path.read_bytes()
            with self.assertRaises(FileExistsError): write_evidence(path,{"sealed":2})
            self.assertEqual(path.read_bytes(),before)
            with patch("nfl.research.price_aware_offers.json.dump",side_effect=OSError("disk")):
                with self.assertRaises(OSError): write_evidence(failed,{})
            self.assertFalse(failed.exists())
        finally:
            path.unlink(missing_ok=True); failed.unlink(missing_ok=True)

    def test_bad_model_and_future_vintage(self):
        for changes in ({"pmf":[.2,.2]},{"pmf":[-.1,1.1]},{"gsis_id":"wrong"},
                        {"generated_at":"2026-09-23T00:00:00Z"},
                        {"source_available_at":"2026-09-23T00:00:00Z"},
                        {"conditioning":"INCLUDING_DNP"}):
            c,d=inputs(); d.update(changes)
            self.assertEqual(run(c,d)["decision_status"],"NO_PLAY")

    def test_nonfinite_evidence_rejected(self):
        c,d=inputs(); d["pmf"]=[float("nan")]
        with self.assertRaises(ValueError): run(c,d)

    def test_frozen_nb_normalized_no_tail_folding(self):
        d=frozen_distribution(projection=3.6,event_id="e",gsis_id="p",feature_cutoff=T,
                              canonical_game_id="2026_03_ATL_GB",
                              source_available_at=T,generated_at=T,history_sha256=H)
        self.assertAlmostEqual(sum(d["pmf"]),1,places=10)
        self.assertGreater(d["pmf"][0],0)

    def test_settlement_preserves_prediction(self):
        c,d=inputs(); r=run(c,d); before=copy.deepcopy(r)
        o=dict(event_id="e",gsis_id="p",authority="OFFICIAL_FINAL",source_sha256=H,
               observed_at="2026-09-24T04:00:00Z",played=True,receptions=1,
               total_snaps=10,participation_source_sha256=H,
               canonical_game_id=c["canonical_game_id"],stat="receptions")
        self.assertTrue(all(s["status"]=="PUSH" for s in settle_record(r,o)["settlements"]))
        o["played"]=False; o["receptions"]=0; o["total_snaps"]=0
        self.assertTrue(all(s["status"]=="VOID_DNP" for s in settle_record(r,o)["settlements"]))
        o["played"]=True; o["total_snaps"]=1
        self.assertEqual([s["status"] for s in settle_record(r,o)["settlements"]],
                         ["MISS","HIT"])
        self.assertEqual(r,before)

    def test_bad_outcome_or_tampered_seal(self):
        c,d=inputs(); r=run(c,d)
        o=dict(event_id="e",gsis_id="p",authority="OFFICIAL_FINAL",source_sha256=H,
               observed_at="2026-09-24T04:00:00Z",played=True,receptions=2,
               total_snaps=10,participation_source_sha256=H,
               canonical_game_id=c["canonical_game_id"],stat="receptions")
        for changes in ({"gsis_id":"wrong"},{"authority":"LIVE"},{"played":None},{"receptions":2.5},{"observed_at":T},
                        {"played":False}, {"total_snaps":None},
                        {"total_snaps":0}, {"canonical_game_id":"wrong"}, {"stat":"passing_yards"}):
            with self.assertRaises(ValueError): settle_record(r,dict(o,**changes))
        r["candidate"]["over_odds"]=200
        with self.assertRaises(ValueError): settle_record(r,o)

if __name__ == "__main__": unittest.main()

