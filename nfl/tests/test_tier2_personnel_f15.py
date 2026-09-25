"""Adversarial F15 source, chronology, and B0-preservation tests."""
import csv
import io
from collections import Counter
from pathlib import Path

import unittest
from unittest.mock import patch

from nfl.research.tier2 import personnel_f15 as f


IDS = [f"00-{i:07d}" for i in range(1,12)]
POS = ["C","G","G","T","T","QB","RB","TE","WR","WR","WR"]
LABEL = "1 C, 2 G, 1 QB, 1 RB, 2 T, 1 TE, 3 WR"


def test_package_requires_full_consistent_positions():
    assert f.classify(IDS,POS,LABEL)[0] == "11"
    assert f.classify(IDS,POS[:-1],LABEL) is None
    assert f.classify(IDS[:-1],POS[:-1],LABEL) is None
    assert f.classify(IDS[:-1]+IDS[-2:-1],POS,LABEL) is None
    assert f.classify(IDS,POS[:-1]+["CB"],LABEL) is None
    assert f.classify(IDS,POS,"1 C, 2 G, 1 QB, 1 RB, 2 T, 2 TE, 2 WR") is None
    assert f.classify(IDS,POS[:-1]+["QB"],LABEL) is None


def test_duplicate_play_and_source_cutoff():
    p, chart = Path("p.csv"), Path("f.csv")
    ctext="nflverse_game_id,nflverse_play_id\n2024_01_ARI_BUF,1\n"
    header="nflverse_game_id,play_id,possession_team,offense_players,offense_positions,offense_personnel\n"
    entry=f'2024_01_ARI_BUF,1,ARI,{";".join(IDS)},{";".join(POS)},"{LABEL}"\n'
    ptext=header+entry+entry
    def opened(path,*args,**kwargs): return io.StringIO(ptext if path==p else ctext)
    with patch.object(f,"verify"), patch.object(Path,"open",opened):
        with unittest.TestCase().assertRaisesRegex(ValueError,"duplicate participation"):
            f.load_profiles(p,chart)
    ctext += "2024_01_ARI_BUF,1\n"
    with patch.object(f,"verify"), patch.object(Path,"open",opened):
        with unittest.TestCase().assertRaisesRegex(ValueError,"duplicate FTN"):
            f.load_profiles(p,chart)
    with patch.object(Path,"stat") as stat, patch.object(Path,"read_bytes",return_value=b"ok"):
        stat.return_value.st_size=2
        with unittest.TestCase().assertRaisesRegex(ValueError,"published after"):
            f.verify(p,{"sha256":f.hashlib.sha256(b"ok").hexdigest(),"published_at":"2025-09-12T00:00:00Z"})
        with unittest.TestCase().assertRaisesRegex(ValueError,"SHA-256 mismatch"):
            f.verify(p,{"sha256":"0"*64,"published_at":"2025-09-04T00:00:00Z"})


def test_identity_cutoff_abstention_and_personnel_connection():
    t=Counter({"11":400,"12":100})
    p=Counter({"11":200,"12":50})
    profiles={"team":{"ARI":t},"player":{("00-0000001","ARI"):p},
              "positions":{("00-0000001","ARI"):Counter({"WR":250})},
              "source_published_at":"2025-09-04T00:00:00Z","source_sha256":{"participation":"a"}}
    row={"season":2025,"week":9,"game_id":"2025_09_ARI_BUF","team":"ARI","player_id":"00-0000001","b0":4.0}
    z=f.feature(row,profiles)
    assert z["status"]=="ACTIVE" and z["opportunity"]>0
    fitted={"scale_only":1.0,"coefficients":[1.0,0.5]}
    first=f.predict({**row,"f15":z},fitted)
    assert first["b0"]==4.0 and first["challenger"]!=first["b0"]
    # Changing the team mix while holding player conditional presence fixed
    # changes opportunity; fixed absolute player counts alone would not.
    t2=Counter({"11":100,"12":400})
    p2=Counter({"11":50,"12":200})
    changed=f.feature(row,{**profiles,"team":{"ARI":t2},"player":{("00-0000001","ARI"):p2}})
    assert changed["opportunity"]!=z["opportunity"]
    assert f.predict({**row,"f15":changed},fitted)["challenger"]!=first["challenger"]
    with unittest.TestCase().assertRaisesRegex(ValueError,"team/game mismatch"):
        f.feature({**row,"team":"NYJ"},profiles)
    assert f.feature({**row,"week":1},profiles)["status"]=="NO_ADJUSTMENT"
    assert f.feature({**row,"player_id":"00-0000010"},profiles)["reason"].startswith("INSUFFICIENT")
    p_small=Counter({"11":99})
    assert f.feature(row,{**profiles,"player":{("00-0000001","ARI"):p_small}})["reason"]=="INSUFFICIENT_PRIOR_SAME_TEAM_PLAYER_PLAYS"
    with unittest.TestCase().assertRaisesRegex(ValueError,"post-cutoff"):
        f.feature(row,{**profiles,"source_published_at":"2025-09-12T00:00:00Z"})


def test_fit_is_dev_only_and_predict_preserves_b0():
    r={"season":2025,"week":2,"b0":4.0,"actual":5.0,"f15":{"status":"ACTIVE","opportunity":0.35}}
    model=f.fit([r,{**r,"b0":6.0,"actual":5.0,"week":3}])
    z=f.predict(r,model)
    assert z["b0"]==4.0 and r["b0"]==4.0
    with unittest.TestCase().assertRaisesRegex(ValueError,"weeks 2-8"):
        f.fit([{**r,"week":9}])
    with unittest.TestCase().assertRaisesRegex(ValueError,"2025 weeks"):
        f.fit([{**r,"season":2024}])


class PersonnelTests(unittest.TestCase):
    def test_classification(self): test_package_requires_full_consistent_positions()
    def test_source(self): test_duplicate_play_and_source_cutoff()
    def test_feature(self): test_identity_cutoff_abstention_and_personnel_connection()
    def test_fit(self): test_fit_is_dev_only_and_predict_preserves_b0()


if __name__ == "__main__":
    unittest.main()

