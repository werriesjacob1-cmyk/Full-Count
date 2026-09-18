"""Pinned upstream nflverse asset digests consumed by `game_market_c3_data_prep.py`.

Each entry is one GitHub release asset's exact byte count and SHA-256,
recorded from the actual bytes downloaded on 2026-09-18 and used to build
the derived `qb_player_stats_week.csv` / `injuries_week.csv` files whose own
digests are pinned in `game_market_c3_research.py`
(`PINNED_QB_PLAYER_STATS_SOURCE`, `PINNED_INJURY_REPORT_SOURCE`).
`game_market_c3_data_prep.py` fails closed if a re-fetched asset's bytes
ever drift from what is recorded here -- the exact same discipline
`game_market_c2_source_digests.py` uses for C2's own upstream pins.

Source: https://github.com/nflverse/nflverse-data
- `PLAYER_STATS_SOURCE_ASSET_DIGESTS`: release tag `stats_player`, asset
  `stats_player_week_<season>.csv`, seasons 1999-2025 -- the same source
  `qb_continuity_features.py`'s docstring names
  (`nflverse_history.PLAYER_STATS_URL`) and the same source
  `passing_yards_baseline_research.py` already consumes for QB rows. C3
  reuses the identical season range (1999-2025) C2's own `pbp`/team-offense
  pins use, so the earliest development season (2000) already has a full
  prior season of QB-continuity history behind it.
- `INJURY_SOURCE_ASSET_DIGESTS`: release tag `injuries`, asset
  `injuries_<season>.csv`, seasons 2009-2025 -- the exact source and season
  floor `injury_availability_features.py`'s own docstring documents and
  independently verified (`injuries_2009.csv` present, `injuries_2008.csv`
  absent). Seasons before 2009 are never fetched here because
  `injury_availability_features.season_is_covered` already reports them as
  `SEASON_NOT_COVERED_BY_SOURCE` from the season number alone, with no
  network call required.
"""
from __future__ import annotations

PLAYER_STATS_SOURCE_ASSET_DIGESTS = {
    "1999": {"bytes": 7369576, "sha256": "5bf732d1dcb4a8f1927af074c05d5534f4b91abd6434fd4cf6130e8a20a91d20"},
    "2000": {"bytes": 7268743, "sha256": "f0b95af81312d29de98947bd1e5aca049edfb1f9d7a7d74862f5c12bf47ad1bd"},
    "2001": {"bytes": 7344977, "sha256": "3b3397b02d42be30f9f5bdcc8a6991227bc235dea18a76d9f294d246bd1b3449"},
    "2002": {"bytes": 7651673, "sha256": "72bf998de6f6be8d22e277f31c106f9a516f8c095923fc7693546db8d08b187b"},
    "2003": {"bytes": 7390720, "sha256": "c440795696eac49e7383051b358e68bb206037a37e0e855b5b92347fa7ece059"},
    "2004": {"bytes": 7401579, "sha256": "f561943805c78786456d529d77f78bfdf97c6608511d189d91d2fdda1cefcc6d"},
    "2005": {"bytes": 7434902, "sha256": "cfaaa943c6b869c5eedfc9611400f0ca47a972bdf30823528b5929f4f9298273"},
    "2006": {"bytes": 7418077, "sha256": "32f5825b8e174ec00641c935de811d4deeb10b4a96807c5c1926f2a16d889714"},
    "2007": {"bytes": 7442890, "sha256": "24829b3f384aad21da867c06180f572a45619c793e94cdbf9e828bd892fa7837"},
    "2008": {"bytes": 7420439, "sha256": "1b0c72f0540f21c8d63f0f86717b821dd05bc97081480764db56ad74cd889856"},
    "2009": {"bytes": 7902720, "sha256": "4f2dbdef657aa2c961595b2a2c832e0fb2a7c13e41c301380900faff266c3477"},
    "2010": {"bytes": 7859210, "sha256": "76f33d484781a3bc1852aa8aab5003874ab219dcb6c67a7e2e881be9822c9d13"},
    "2011": {"bytes": 7800649, "sha256": "c1720e9e5ed59b7def9486ae650ea73ee80dda1fd2ab97d50c5fb4f6565dbeaa"},
    "2012": {"bytes": 7796458, "sha256": "5c8885d93ec529efa9d729cec1ef255f438ee27a59eba6e156a360fa0045b56b"},
    "2013": {"bytes": 7713866, "sha256": "f7329a8f15084c9c23d841f14eb117fcc630d6c82913501f3c60f2d8f4e651a4"},
    "2014": {"bytes": 7883194, "sha256": "a389b10d94eb8e6b9a153223d381aab821b0eff4cd73967835a21032192abc61"},
    "2015": {"bytes": 7881066, "sha256": "21052baa3b3291a92faf3e6827891295010dca8cc5cefd5a909984b9d7b64664"},
    "2016": {"bytes": 7852359, "sha256": "eb0264c65d1ef593196afcd4ba24e9f2e9b7942923bf6f4bb4639684e2c4d470"},
    "2017": {"bytes": 7821392, "sha256": "592b0916065987b0b3afb0480162f03350d16b12250dc42b0f194e2ab1378563"},
    "2018": {"bytes": 7792665, "sha256": "c53021bcfc9f89d30edd6f1fe145a1d7d6ce469efeec75974fe9f0adac568642"},
    "2019": {"bytes": 7767165, "sha256": "a05558126b1209b80fe11a8f18938cb35816370bf9990975a87de49e9eed7376"},
    "2020": {"bytes": 7883270, "sha256": "05b992676faccc8940efbf6242cb76358069372d31d7c2e02c1a4acdcd3cbe18"},
    "2021": {"bytes": 8477784, "sha256": "41915fb49238902ad1f129ebf0405b11a1e710454ae0fe8f7b3e4f9145875f48"},
    "2022": {"bytes": 8408729, "sha256": "ad426c3fe5bf1cc30c3f137fdfe96d054e19d400879ee4413129da49fa7b54be"},
    "2023": {"bytes": 8332874, "sha256": "f19cb71a5de0dce7fd09376026237c9ee9d5a93fe13815a2ea3ec2d37204cb17"},
    "2024": {"bytes": 8470040, "sha256": "3ddc45a84f759aa348ce465ae001752c530575455717657cdfe1f8abfcdb4759"},
    "2025": {"bytes": 8656387, "sha256": "e5e0615b3d96a3eaebfaee91e55afb4a4e7fe0caf057454177bcd7d6ad4bcfc2"},
}

INJURY_SOURCE_ASSET_DIGESTS = {
    "2009": {"bytes": 555769, "sha256": "75ded84697ed0b9c629d09e5d54c7cb3f80ebb97deb84df23b9004148276f448"},
    "2010": {"bytes": 606845, "sha256": "6e86b7aba42b9adf76c083686b9bc6fbd7943293b411893ffe952f3b71997519"},
    "2011": {"bytes": 675650, "sha256": "acf44d2952fec7d0806eed122e36d53f174ab742b25a4c6e9f3f1676da255a10"},
    "2012": {"bytes": 752393, "sha256": "8b8ce2ed5bd45bbb709e5402efa4fe930d63fedfce111df9b1fea08ffa51f237"},
    "2013": {"bytes": 689228, "sha256": "b84c71cf23be81375bbca62f7d2c51d53fa75330fbcf9f598ec4ac17fea7d8be"},
    "2014": {"bytes": 692574, "sha256": "e675fd5d86c3451d76e7c5d530095db1641347eb3f9796c9a30dd7a75e3ba634"},
    "2015": {"bytes": 715948, "sha256": "fbca54ef42594cc52facc0d8c8dd74802b812771b9e33032e44ae8c7e21b0360"},
    "2016": {"bytes": 674980, "sha256": "504e05968cb9c54ddc730fd9b6e7d1dd16a62db8029cc784d3d7bafcea7ff7cb"},
    "2017": {"bytes": 665280, "sha256": "0d5139fbf41b0866517bd2bd5fd85bb20dbd4dd9bc36a706fed030a88731de6f"},
    "2018": {"bytes": 665119, "sha256": "4724e1f37cc3076f564e997fbf269190e7dbe795a39315b903788e0a9c40065e"},
    "2019": {"bytes": 700757, "sha256": "daddfe8e04ddc3a29bce14f362e774fbadfb274c807f1f0fde7b5bf32aaf6cdf"},
    "2020": {"bytes": 737474, "sha256": "706cca82824214f6b73ba0c6e537d3ee39766a50c644b8974b9fc28d7518de90"},
    "2021": {"bytes": 737083, "sha256": "1049fb9ff0fe7cfcf3ba8bfe1f5a35d3aba985c461c8fb7bf5201735b6d34254"},
    "2022": {"bytes": 752433, "sha256": "5f0d60324e597edd1a2ced8540b04e40b51dd440591dd1df11b8d96343387c9e"},
    "2023": {"bytes": 738501, "sha256": "16b04e21da5aa3944cfa9c22cafcc84ed7e6d9866eaddf0344e9b8cbf2f63afe"},
    "2024": {"bytes": 816989, "sha256": "498bce8e13cb64b2ab9bb0ad6cb81d0a63c2ddb24016c9fc90c2de2126fae449"},
    "2025": {"bytes": 696006, "sha256": "873ca1606dd575bd01152508a243ef6b3a0f8f97b90b707217e62ee8c7ceb735"},
}
