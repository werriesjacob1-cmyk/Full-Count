"""Synthetic, nonconfirmatory FC-OPS-001 attack and compatibility fixtures."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

import odds_fanduel as fd
from research.fc_ops_001.offer_contract import (
    FIRST_INNING_TYPE, join_champion_decision, join_s1_candidate, parse_archived_page,
)

OBS = "2027-04-01T16:10:00Z"
CUTOFF = "2027-04-01T16:30:00Z"
START = "2027-04-01T17:00:00Z"


def runner(name, selection, odds=-110, *, handicap=None, status="ACTIVE"):
    value = {"runnerName": name, "selectionId": selection, "runnerStatus": status,
             "winRunnerOdds": {"americanDisplayOdds": {"americanOddsInt": odds}}}
    if handicap is not None:
        value["handicap"] = handicap
    return value


def market(mt="PITCHER_A_TOTAL_STRIKEOUTS", runners=None, **kw):
    value = {"marketId": "M1", "marketType": mt, "eventId": "E1", "marketStatus": "OPEN",
             "inPlay": False, "runners": runners or [runner("Alice Over", "S1", handicap=5.5),
                                                  runner("Alice Under", "S2", handicap=5.5)]}
    value.update(kw)
    return value


def parse(markets=None, **kw):
    obj = {"attachments": {"markets": {str(i): m for i, m in enumerate(markets or [market()])}}}
    raw = json.dumps(obj, sort_keys=True).encode()
    args = {"expected_sha256": hashlib.sha256(raw).hexdigest(),
            "source_request": "event-page?eventId=E1&tab=pitcher-props",
            "source_host": "synthetic.example.invalid", "sportsbook_event_id": "E1",
            "game_pk": "G1", "event_start": START, "observed_at": OBS,
            "decision_at": CUTOFF, "player_ids": {"alice": "P1", "bob": "P2"}}
    args.update(kw)
    return parse_archived_page(raw, **args)


def decision():
    return {"canonical_id": "fc2:G1:player-P1:strikeouts:6:over", "game_pk": "G1",
            "player_id": "P1", "team": "T1", "market": "strikeouts", "needs": 6, "wager_side": "over",
            "line": 5.5, "market_odds": -110, "recommendation_status": "top_pick",
            "new_publication_candidate": True}


class BaselineProductionDefects(unittest.TestCase):
    def test_closed_inactive_one_sided_quote_was_accepted(self):
        bad = market("PLAYER_TO_RECORD_A_HIT", [runner("Alice", "S1", -120, status="INACTIVE")],
                     marketStatus="CLOSED")
        with patch.object(fd, "_market_pages", return_value=([bad], True, [])):
            self.assertEqual(fd._event_props("E1")[0]["american"], -120)

    def test_cross_player_cross_line_strikeout_pair_was_accepted(self):
        bad = market(runners=[runner("Alice Over", "S1", handicap=5.5),
                              runner("Bob Under", "S2", handicap=6.5)])
        self.assertEqual(fd._parse_two_sided(bad), ("Alice", 6.5, -110, -110))

    def test_cross_player_cross_line_pitcher_outs_pair_was_accepted(self):
        bad = market("PITCHER_A_OUTS_RECORDED_SB",
                     [runner("Alice Over 15.5", "S1"), runner("Bob Under 16.5", "S2")])
        discovery = fd._GameDiscovery(fd.EVENTS_DISCOVERED, (("E1", "Away @ Home", START),))
        with patch.object(fd, "_discover_games", return_value=discovery), \
             patch.object(fd, "_market_pages", return_value=([bad], True, [])):
            result = fd.fetch_pitcher_outs()
        self.assertEqual(result["bob"]["line"], 16.5)
        self.assertEqual(result["bob"]["over"], -110)  # actually Alice's price

    def test_closed_nrfi_pair_was_accepted(self):
        bad = market(FIRST_INNING_TYPE, [runner("Over", "S1"), runner("Under", "S2")],
                     marketStatus="SUSPENDED")
        discovery = fd._GameDiscovery(fd.EVENTS_DISCOVERED, (("E1", "Away @ Home", START),))
        with patch.object(fd, "_discover_games", return_value=discovery), \
             patch.object(fd, "_market_pages", return_value=([bad], True, [])):
            self.assertIn("Away @ Home", fd.fetch_first_inning_totals())


class ResearchParserTests(unittest.TestCase):
    def test_valid_paired_market_preserves_exact_identity_and_no_vig(self):
        offers, rejects = parse()
        self.assertFalse(rejects)
        self.assertEqual({o["side"] for o in offers}, {"over", "under"})
        self.assertTrue(all(o["player_id"] == "P1" and o["line"] == 5.5 for o in offers))
        self.assertTrue(all(o["raw_response_sha256"] and o["source_request"] for o in offers))
        self.assertAlmostEqual(sum(o["fair_novig"] for o in offers), 1)

    def test_valid_pitcher_outs_and_nrfi(self):
        outs = market("PITCHER_A_OUTS_RECORDED_SB",
                      [runner("Alice Over 15.5", "S1"), runner("Alice Under 15.5", "S2")])
        self.assertEqual(len(parse([outs])[0]), 2)
        fi = market(FIRST_INNING_TYPE, [runner("Over", "S1"), runner("Under", "S2")])
        offers, rejected = parse([fi], source_request="event-page?eventId=E1&tab=innings")
        self.assertFalse(rejected)
        self.assertEqual({o["market"] for o in offers}, {"nrfi_combined"})
        self.assertEqual({o["player_id"] for o in offers}, {None})

    def test_cross_player_line_duplicate_and_missing_runner_rejected(self):
        mutants = [
            market(runners=[runner("Alice Over", "S1", handicap=5.5), runner("Bob Under", "S2", handicap=5.5)]),
            market(runners=[runner("Alice Over", "S1", handicap=5.5), runner("Alice Under", "S2", handicap=6.5)]),
            market(runners=[runner("Alice Over", "S1", handicap=5.5), runner("Alice Over", "S2", handicap=5.5)]),
            market(runners=[runner("Alice Over", "S1", handicap=5.5)]),
        ]
        for item in mutants:
            with self.subTest(item=item["runners"]):
                offers, rejected = parse([item])
                self.assertFalse(offers)
                self.assertEqual(rejected[0]["reason"], "INCOHERENT_TWO_SIDED_PAIR")

    def test_status_and_inplay_fail_closed(self):
        variants = [(market(marketStatus="CLOSED"), "MARKET_NOT_OPEN"),
                    (market(marketStatus="SUSPENDED"), "MARKET_NOT_OPEN"),
                    (market(marketStatus=None), "MARKET_STATUS_UNVERIFIED"),
                    (market(inPlay=True), "IN_PLAY_OR_UNVERIFIED"),
                    (market(inPlay=None), "IN_PLAY_OR_UNVERIFIED"),
                    (market(runners=[runner("Alice Over", "S1", handicap=5.5, status="INACTIVE"),
                                     runner("Alice Under", "S2", handicap=5.5)]), "RUNNER_NOT_ACTIVE"),
                    (market(runners=[runner("Alice Over", "S1", handicap=5.5, status=None),
                                     runner("Alice Under", "S2", handicap=5.5)]), "RUNNER_STATUS_UNVERIFIED")]
        for item, reason in variants:
            with self.subTest(reason=reason):
                offers, rejected = parse([item])
                self.assertFalse(offers)
                self.assertEqual(rejected[0]["reason"], reason)

    def test_bad_odds_selection_identity_and_unresolved_player_rejected(self):
        variants = [market(runners=[runner("Alice Over", None, handicap=5.5),
                                   runner("Alice Under", "S2", handicap=5.5)]),
                    market(runners=[runner("Alice Over", "S1", -90, handicap=5.5),
                                    runner("Alice Under", "S2", handicap=5.5)]),
                    market(runners=[runner("Alice Over", "S1", handicap=5.5),
                                    runner("Alice Under", "S1", handicap=5.5)])]
        for item in variants:
            self.assertFalse(parse([item])[0])
        self.assertEqual(parse(player_ids={})[1][0]["reason"], "PLAYER_ID_UNVERIFIED")
        self.assertEqual(parse(player_ids={"alice": ""})[1][0]["reason"], "PLAYER_ID_UNVERIFIED")

    def test_duplicate_one_sided_names_rejected(self):
        m = market("PLAYER_TO_RECORD_A_HIT", [runner("Alice", "S1"), runner("Alice", "S2")])
        self.assertEqual(parse([m])[1][0]["reason"], "DUPLICATE_OR_MISSING_PLAYER")

    def test_hash_event_request_and_time_boundaries(self):
        cases = [(dict(expected_sha256="0" * 64), "RAW_RESPONSE_HASH_MISMATCH"),
                 (dict(source_request="event-page?eventId=E2&tab=popular"), "SOURCE_REQUEST_EVENT_MISMATCH"),
                 (dict(observed_at=None), "MISSING_OR_INVALID_TIME"),
                 (dict(observed_at=START), "IN_PLAY_OR_POST_START"),
                 (dict(observed_at="2027-04-01T16:31:00Z"), "FUTURE_OR_STALE_OBSERVATION"),
                 (dict(observed_at="2027-04-01T15:00:00Z"), "FUTURE_OR_STALE_OBSERVATION")]
        for options, reason in cases:
            with self.subTest(options=options):
                offers, rejected = parse(**options)
                self.assertFalse(offers)
                self.assertEqual(rejected[0]["reason"], reason)
        self.assertEqual(parse([market(eventId="E2")])[1][0]["reason"], "MARKET_EVENT_MISMATCH")

    def test_whole_number_two_sided_line_requires_push_contract(self):
        m = market(runners=[runner("Alice Over", "S1", handicap=5),
                            runner("Alice Under", "S2", handicap=5)])
        self.assertEqual(parse([m])[1][0]["reason"], "UNSUPPORTED_PUSH_LINE")


class CrossPrContractTests(unittest.TestCase):
    def test_exact_champion_join_from_s1_shape(self):
        offer = next(o for o in parse()[0] if o["side"] == "over")
        s1_offer = {"sportsbook": "fanduel", "sportsbook_event_id": offer["sportsbook_event_id"],
                    "market_id": offer["market_id"], "market_type": offer["market_type"],
                    "selection_id": offer["selection_id"], "raw_response_sha256": offer["raw_response_sha256"],
                    "market": "strikeouts", "side": "over", "line": 5.5, "needs": 6,
                    "american_odds": -110, "observation_completed_at": OBS,
                    "availability_verified": True, "request_path": offer["source_request"]}
        s1 = {"candidate_id": decision()["canonical_id"], "game_pk": "G1", "player_id": "P1",
              "team": "T1", "stat": "strikeouts", "side": "over", "needs": 6, "line": 5.5,
              "candidate_odds": -110, "offer_status": "AUTHENTICATED", "offer": s1_offer}
        self.assertEqual(join_s1_candidate(decision(), s1, cutoff=CUTOFF,
                                           source_chain_verified=True), "MATCHED_RESEARCH_ONLY")
        for mutation, expected in [({"candidate_id": "other"}, "DECISION_ID_MISMATCH"),
                                   ({"candidate_odds": -120}, "POLICY_PRICE_MISMATCH")]:
            bad = dict(s1, **mutation)
            self.assertEqual(join_s1_candidate(decision(), bad, cutoff=CUTOFF,
                                               source_chain_verified=True), expected)
        self.assertEqual(join_s1_candidate(decision(), s1, cutoff=CUTOFF,
                                           source_chain_verified=False), "SOURCE_NOT_VERIFIED")
        self.assertEqual(join_s1_candidate(decision(), dict(s1, team="T2"), cutoff=CUTOFF,
                                           source_chain_verified=True), "S1_CANDIDATE_IDENTITY_MISMATCH")

    def test_join_identity_price_timing_and_eligibility_mutants(self):
        offer = next(o for o in parse()[0] if o["side"] == "over")
        self.assertEqual(join_champion_decision(decision(), offer, cutoff=CUTOFF,
                                                verified_source=True), "TEAM_MISMATCH")
        offer = dict(offer, team="T1")
        variants = [(dict(game_pk="G2"), "GAME_MISMATCH"),
                    (dict(player_id="P2"), "PLAYER_MISMATCH"),
                    (dict(team="T2"), "TEAM_MISMATCH"),
                    (dict(sportsbook="other"), "SPORTSBOOK_MISMATCH"),
                    (dict(market="hits"), "MARKET_OR_SIDE_MISMATCH"),
                    (dict(side="under"), "MARKET_OR_SIDE_MISMATCH"),
                    (dict(line=6.5), "THRESHOLD_MISMATCH"),
                    (dict(american_odds=-120), "PRICE_CHANGED_UNREVALIDATED"),
                    (dict(availability="UNVERIFIED"), "OFFER_AVAILABILITY_UNVERIFIED"),
                    (dict(observed_at="2027-04-01T16:31:00Z"), "OFFER_TIME_INVALID")]
        for mutation, expected in variants:
            with self.subTest(mutation=mutation):
                self.assertEqual(join_champion_decision(decision(), dict(offer, **mutation),
                                                        cutoff=CUTOFF, verified_source=True), expected)
        bad = dict(decision(), line=6.5)
        self.assertEqual(join_champion_decision(bad, offer, cutoff=CUTOFF,
                                                verified_source=True), "THRESHOLD_MISMATCH")
        withheld = dict(decision(), new_publication_candidate=False)
        self.assertEqual(join_champion_decision(withheld, offer, cutoff=CUTOFF,
                                                verified_source=True), "NOT_NEW_PUBLICATION_CANDIDATE")


if __name__ == "__main__":
    unittest.main()
