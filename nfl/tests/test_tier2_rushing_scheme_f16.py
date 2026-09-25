"""F16 box-source and predictive-consumer adversarial tests."""
import hashlib
import io
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from nfl.research.tier2 import rushing_scheme_f16 as f

IDS=[f"00-{i:07d}" for i in range(1,12)]
POS=["C","G","G","T","T","QB","RB","TE","WR","WR","WR"]
LABEL="1 C, 2 G, 1 QB, 1 RB, 2 T, 1 TE, 3 WR"


def source_row(box="7",other="7",offense="ARI"):
    p={"nflverse_game_id":"2024_01_ARI_BUF","possession_team":offense,
       "offense_players":";".join(IDS),"offense_positions":";".join(POS),
       "offense_personnel":LABEL,"defenders_in_box":other}
    return p,{"n_defense_box":box}


class F16Tests(unittest.TestCase):
    def test_verified_box_and_package(self):
        p,z=source_row()
        self.assertEqual(f.classify_box_play(p,z),("11",7,"ARI","BUF"))
        for a,b in [("0","0"),("","7"),("7","6"),("12","12"),("X","7")]:
            self.assertIsNone(f.classify_box_play(*source_row(a,b)))
        self.assertIsNone(f.classify_box_play(*source_row(offense="NYJ")))
        self.assertIsNone(f.classify_box_play({**p,"offense_players":";".join(IDS[:-1])},z))
        self.assertIsNone(f.classify_box_play({**p,"offense_positions":";".join(POS[:-1]+["CB"])},z))
        self.assertIsNone(f.classify_box_play({**p,"offense_personnel":"1 C, 2 G, 1 QB, 1 RB, 2 T, 2 TE, 2 WR"},z))

    def test_duplicate_identity_and_source_hash_cutoff(self):
        part,chart=Path("p.csv"),Path("f.csv")
        text_f="nflverse_game_id,nflverse_play_id,n_defense_box\n2024_01_ARI_BUF,1,7\n"
        text_p="nflverse_game_id,play_id,possession_team,offense_players,offense_positions,offense_personnel,defenders_in_box\n"
        text_p+=f'2024_01_ARI_BUF,1,ARI,{";".join(IDS)},{";".join(POS)},"{LABEL}",7\n'*2
        def opened(path,*args,**kwargs):return io.StringIO(text_p if path==part else text_f)
        with patch.object(f,"verify"),patch.object(Path,"open",opened):
            with self.assertRaisesRegex(ValueError,"duplicate participation"):
                f.load_profiles(part,chart)
        text_f+="2024_01_ARI_BUF,1,7\n"
        with patch.object(f,"verify"),patch.object(Path,"open",opened):
            with self.assertRaisesRegex(ValueError,"duplicate FTN"):
                f.load_profiles(part,chart)
        with patch.object(Path,"stat") as stat,patch.object(Path,"read_bytes",return_value=b"ok"):
            stat.return_value.st_size=2
            with self.assertRaisesRegex(ValueError,"SHA-256 mismatch"):
                f.verify(part,{"sha256":"0"*64,"published_at":"2025-09-04T00:00:00Z"})
            with self.assertRaisesRegex(ValueError,"published after"):
                f.verify(part,{"sha256":hashlib.sha256(b"ok").hexdigest(),"published_at":"2025-09-12T00:00:00Z"})

    def test_feature_identity_samples_and_connection(self):
        profiles={"offense":{"ARI":Counter({"11":300,"OTHER":100})},
                  "defense":{"BUF":Counter({("11","total"):200,("11","heavy"):80,
                                             ("OTHER","total"):200,("OTHER","heavy"):20})},
                  "source_published_at":"2025-09-04T10:24:49Z","source_sha256":{"ftn":"a"}}
        r={"player_id":"00-0000001","position":"RB","season":2025,"week":9,
           "game_id":"2025_09_ARI_BUF","team":"ARI","b0":40.0}
        z=f.feature(r,profiles)
        self.assertEqual(z["status"],"ACTIVE")
        self.assertAlmostEqual(z["expected_heavy_box_rate"],0.325)
        model={"scale_only":1.0,"coefficients":[1.0,-0.5]}
        out=f.predict({**r,"f16":z},model)
        self.assertEqual(out["b0"],40.0)
        self.assertNotEqual(out["challenger"],40.0)
        changed=f.feature(r,{**profiles,"defense":{"BUF":Counter({("11","total"):200,("11","heavy"):20,
                                                           ("OTHER","total"):200,("OTHER","heavy"):20})}})
        self.assertNotEqual(f.predict({**r,"f16":changed},model)["challenger"],out["challenger"])
        self.assertEqual(f.feature({**r,"week":1},profiles)["status"],"NO_ADJUSTMENT")
        self.assertEqual(f.feature({**r,"position":"QB"},profiles)["status"],"NO_ADJUSTMENT")
        self.assertEqual(f.feature({**r,"player_id":""},profiles)["reason"],"INVALID_PLAYER_GSIS_ID")
        self.assertEqual(f.feature({**r,"player_id":"WR-123"},profiles)["reason"],"INVALID_PLAYER_GSIS_ID")
        with self.assertRaisesRegex(ValueError,"team/game mismatch"):
            f.feature({**r,"team":"NYJ"},profiles)
        with self.assertRaisesRegex(ValueError,"post-cutoff"):
            f.feature(r,{**profiles,"source_published_at":"2025-09-12T00:00:00Z"})
        self.assertEqual(f.feature(r,{**profiles,"offense":{"ARI":Counter({"11":299})}})["reason"],"INSUFFICIENT_PRIOR_OFFENSE_GROUP_PLAYS")
        d=Counter({("11","total"):99,("OTHER","total"):200})
        self.assertEqual(f.feature(r,{**profiles,"defense":{"BUF":d}})["reason"],"INSUFFICIENT_PRIOR_OPPONENT_BOX_PLAYS")

    def test_fit_dev_only_and_b0_preserved(self):
        r={"season":2025,"week":2,"b0":40.0,"actual":42.0,
           "f16":{"status":"ACTIVE","expected_heavy_box_rate":0.35}}
        fit=f.fit([r,{**r,"week":3,"b0":30.0,"actual":24.0}])
        out=f.predict(r,fit)
        self.assertEqual(out["b0"],r["b0"])
        self.assertEqual(r["b0"],40.0)
        with self.assertRaisesRegex(ValueError,"2025 weeks"):
            f.fit([{**r,"season":2024}])
        with self.assertRaisesRegex(ValueError,"2025 weeks"):
            f.fit([{**r,"week":9}])


if __name__=="__main__":unittest.main()

