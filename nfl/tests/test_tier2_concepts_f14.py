"""F14 concept source and predictive boundary tests."""
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from nfl.research.tier2 import concepts_f14 as f14


class F14Tests(unittest.TestCase):
    def setUp(self):
        self.folder=Path(__file__).resolve().parents[2]/"engineering"/"nfl_tier2_concepts_20260925"
        self.folder.mkdir(exist_ok=True)
        self.part=self.folder/"test_part_temporary.csv"
        self.ftn=self.folder/"test_ftn_temporary.csv"
        self.part_text=("nflverse_game_id,play_id,possession_team,offense_players,offense_positions\n"
                        "2024_01_ARI_BUF,41,ARI,00-0031234;00-0039999,QB;WR\n"
                        "2024_01_ARI_BUF,42,ARI,00-0031234;00-0039999,QB;WR\n")
        self.ftn_text=("nflverse_game_id,nflverse_play_id,is_play_action,is_motion,is_rpo,is_screen_pass\n"
                       "2024_01_ARI_BUF,41,TRUE,FALSE,FALSE,TRUE\n"
                       "2024_01_ARI_BUF,42,FALSE,TRUE,TRUE,FALSE\n")
        self.write()

    def tearDown(self):
        self.part.unlink(missing_ok=True); self.ftn.unlink(missing_ok=True)

    def write(self):
        self.part.write_text(self.part_text,encoding="utf-8")
        self.ftn.write_text(self.ftn_text,encoding="utf-8")

    def profiles(self):
        a=self.part.read_bytes(); b=self.ftn.read_bytes()
        with patch.dict(f14.SOURCES,{
            "participation":{"sha256":hashlib.sha256(a).hexdigest(),"bytes":len(a),"published_at":"2025-09-04T10:24:49Z"},
            "ftn":{"sha256":hashlib.sha256(b).hexdigest(),"bytes":len(b),"published_at":"2025-09-01T01:29:37Z"}}):
            return f14.load_profiles(self.part,self.ftn)

    def row(self):
        return {"season":2025,"week":9,"game_id":"2025_09_ARI_BUF","team":"ARI",
                "player_id":"00-0039999","b0":3.2}

    def test_direct_flags_and_on_field_identity(self):
        result=self.profiles()
        player=result["player"][("00-0039999","ARI")]
        self.assertEqual(player["n"],2)
        self.assertEqual(tuple(player[name] for name in f14.FLAGS),(1,1,1,1))
        self.assertEqual(result["team"]["ARI"]["n"],2)

    def test_source_digest_and_duplicate_fail(self):
        with self.assertRaisesRegex(ValueError,"digest mismatch"):
            f14.verify(self.part,{"sha256":"0"*64,"bytes":len(self.part.read_bytes())})
        self.ftn_text+="2024_01_ARI_BUF,41,TRUE,FALSE,FALSE,TRUE\n"
        self.write()
        with self.assertRaisesRegex(ValueError,"duplicate FTN"):
            self.profiles()

    def test_unknown_flag_not_assumed_false(self):
        self.ftn_text=self.ftn_text.replace("TRUE,FALSE,FALSE,TRUE","UNKNOWN,FALSE,FALSE,TRUE")
        self.write()
        result=self.profiles()
        self.assertEqual(result["excluded"]["UNKNOWN_CONCEPT_FLAG"],1)
        self.assertEqual(result["team"]["ARI"]["n"],1)

    def test_ambiguous_player_identity_excluded(self):
        self.part_text=self.part_text.replace("QB;WR","QB;QB")
        self.write()
        self.assertEqual(self.profiles()["excluded"]["NO_UNIQUE_QB_SCRIMMAGE_PLAY"],2)

    def test_no_adjustment_preserves_baseline(self):
        x=self.row(); p=self.profiles()
        z=f14.feature(x,p)
        self.assertEqual(z["reason"],"INSUFFICIENT_PRIOR_TEAM_CONCEPT_PLAYS")
        self.assertEqual(f14.predict({**x,"f14":z},{"scale_only":1.0,"challenger":[1]*5})["baseline_b0"],3.2)
        with self.assertRaisesRegex(ValueError,"team/game"):
            f14.feature({**x,"team":"KC"},p)

    def test_deterministic_contribution_and_dev_only_fit(self):
        x=self.row(); p=self.profiles()
        with patch.object(f14,"MIN_TEAM",2),patch.object(f14,"MIN_PLAYER",2):
            active=f14.feature(x,p)
        self.assertEqual(active["status"],"ACTIVE")
        fit={"scale_only":.9,"challenger":[.9,.1,-.2,.3,.4]}
        pred=f14.predict({**x,"f14":active},fit)
        self.assertAlmostEqual(pred["challenger"],sum(pred["contributions"].values()))
        self.assertEqual(pred,f14.predict({**x,"f14":active},fit))
        with self.assertRaisesRegex(ValueError,"development"):
            f14.fit([{**x,"actual":3.0,"f14":active}])


if __name__=="__main__": unittest.main()
