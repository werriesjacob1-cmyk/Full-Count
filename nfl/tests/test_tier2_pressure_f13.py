"""F13 source, identity, cutoff and predictive-consumer contracts."""
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from nfl.research.tier2 import pressure_f13 as f13


class F13Tests(unittest.TestCase):
    def setUp(self):
        self.part = (
            "nflverse_game_id,play_id,possession_team,offense_players,offense_positions,was_pressure\n"
            "2024_01_ARI_BUF,41,ARI,00-0031234;00-0039999,QB;WR,TRUE\n"
            "2024_01_ARI_BUF,42,ARI,00-0031234;00-0039999,QB;WR,FALSE\n")
        self.ftn = (
            "nflverse_game_id,nflverse_play_id,n_pass_rushers,n_blitzers\n"
            "2024_01_ARI_BUF,41,4,0\n"
            "2024_01_ARI_BUF,42,4,2\n")
        self.folder = Path(__file__).resolve().parents[2] / "engineering" / "nfl_tier2_pressure_20260925"
        self.folder.mkdir(exist_ok=True)
        self.ppath = self.folder / "test_part_temporary.csv"
        self.fpath = self.folder / "test_ftn_temporary.csv"
        self._write()

    def tearDown(self):
        self.ppath.unlink(missing_ok=True)
        self.fpath.unlink(missing_ok=True)

    def _write(self):
        self.ppath.write_text(self.part, encoding="utf-8", newline="")
        self.fpath.write_text(self.ftn, encoding="utf-8", newline="")

    def _profiles(self):
        p=self.ppath.read_bytes(); t=self.fpath.read_bytes()
        with patch.dict(f13.SOURCES, {
            "participation":{"sha256":hashlib.sha256(p).hexdigest(),"bytes":len(p),
                             "published_at":"2025-09-04T10:24:49Z"},
            "ftn":{"sha256":hashlib.sha256(t).hexdigest(),"bytes":len(t),
                   "published_at":"2025-09-01T01:29:37Z"}}):
            return f13.load_profiles(self.ppath,self.fpath)

    def test_pressure_and_blitz_are_distinct_chart_observations(self):
        profiles=self._profiles()
        qb=profiles["qb"]["00-0031234"]
        self.assertEqual((qb["n"],qb["pressure"],qb["blitz"],qb["pressure_and_blitz"]),
                         (2,1,1,0))
        self.assertEqual(profiles["defense"]["BUF"]["n"],2)
        self.assertEqual(profiles["source_digests"]["ftn"],hashlib.sha256(self.ftn.encode()).hexdigest())

    def test_source_digest_mismatch_fails(self):
        with self.assertRaisesRegex(ValueError,"digest mismatch"):
            f13.verify(self.ppath,{"sha256":"0"*64,"bytes":len(self.ppath.read_bytes())})

    def test_duplicate_game_play_fails(self):
        self.ftn += "2024_01_ARI_BUF,41,4,0\n"
        self._write()
        with self.assertRaisesRegex(ValueError,"duplicate FTN"):
            self._profiles()

    def test_unknown_or_inconsistent_values_are_excluded(self):
        self.ftn=self.ftn.replace("41,4,0","41,4,5")
        self._write()
        result=self._profiles()
        self.assertEqual(result["excluded"]["UNKNOWN_OR_INCONSISTENT_CHART"],1)
        self.assertEqual(result["qb"]["00-0031234"]["n"],1)

    def test_ambiguous_qb_is_not_attributed(self):
        self.part=self.part.replace("QB;WR","QB;QB")
        self._write()
        self.assertEqual(self._profiles()["excluded"]["AMBIGUOUS_QB"],2)

    def test_feature_abstains_on_missing_evidence_and_preserves_baseline(self):
        row={"season":2025,"week":9,"game_id":"2025_09_ARI_BUF","team":"ARI",
             "opponent_team":"BUF","player_id":"00-0031234","b0":240.0}
        profiles=self._profiles()
        missing=f13.feature(row,profiles)
        self.assertEqual(missing["reason"],"INSUFFICIENT_PRIOR_QB_CHARTING")
        prediction=f13.predict({**row,"f13":missing},{"scale_only":1.0,"challenger":[1,2,3]})
        self.assertEqual(prediction["baseline_b0"],240.0)
        self.assertIsNone(prediction["challenger"])
        with self.assertRaisesRegex(ValueError,"opponent identity"):
            f13.feature({**row,"opponent_team":"NYJ"},profiles)

    def test_deterministic_contributions_use_real_feature_inputs(self):
        row={"season":2025,"week":9,"game_id":"2025_09_ARI_BUF","team":"ARI",
             "opponent_team":"BUF","player_id":"00-0031234","b0":240.0}
        profiles=self._profiles()
        with patch.object(f13,"MIN_QB",2),patch.object(f13,"MIN_DEFENSE",2):
            tactical=f13.feature(row,profiles)
        self.assertEqual(tactical["status"],"ACTIVE")
        frozen={"scale_only":.9,"challenger":[.9,1.0,-.5]}
        result=f13.predict({**row,"f13":tactical},frozen)
        self.assertEqual(result["baseline_b0"],240.0)
        self.assertAlmostEqual(result["challenger"],sum(result["contributions"].values()))
        self.assertEqual(result,f13.predict({**row,"f13":tactical},frozen))

    def test_fit_uses_development_weeks_only(self):
        row={"week":3,"b0":200.0,"actual":180.0,
             "f13":{"pressure_delta":.1,"blitz_delta":.2}}
        fitted=f13.fit([row,{**row,"week":4,"b0":240.0,"actual":220.0,
                              "f13":{"pressure_delta":-.1,"blitz_delta":.1}},
                         {**row,"week":5,"b0":180.0,"actual":210.0,
                              "f13":{"pressure_delta":.2,"blitz_delta":-.1}}])
        self.assertEqual(len(fitted["challenger"]),3)
        with self.assertRaisesRegex(ValueError,"development"):
            f13.fit([{**row,"week":9}])


if __name__=="__main__": unittest.main()
