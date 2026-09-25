"""Pinned upstream nflverse asset digests consumed by `game_market_c2_data_prep.py`.

Each entry is one GitHub release asset's exact byte count and SHA-256,
recorded from the actual bytes downloaded and used to build the derived
`team_offense_week.csv` / `pbp_plays_filtered.csv` / `snap_counts_prepared.csv`
files whose own digests are pinned in `game_market_c2_research.py`
(`PINNED_TEAM_OFFENSE_SOURCE`, `PINNED_PBP_PLAY_SOURCE`,
`PINNED_SNAP_COUNTS_SOURCE`). `game_market_c2_data_prep.py` fails closed if a
re-fetched asset's bytes ever drift from what is recorded here.

Source: https://github.com/nflverse/nflverse-data
- `PBP_SOURCE_ASSET_DIGESTS`: release tag `pbp`, asset
  `play_by_play_<season>.csv.gz`, seasons 1999-2025.
- `SNAP_SOURCE_ASSET_DIGESTS`: release tag `snap_counts`, asset
  `snap_counts_<season>.csv`, seasons 2012-2025. The 2012 asset is a
  real, disclosed zero-row file (header only) in nflverse's own release;
  see `game_market_c2_features.OL_CONTINUITY_EXCLUSION_REASON`.
"""
from __future__ import annotations

PBP_SOURCE_ASSET_DIGESTS = {
    "1999": {"bytes": 12836909, "sha256": "0875bf329eb09496ae948d0261eed6a4e5be2e11844b2656e1625242846d083d"},
    "2000": {"bytes": 12972701, "sha256": "18804cf9e8299adbbb6cdaca479aa2b33f3a76f4389afe5ccdc654b12477d7e8"},
    "2001": {"bytes": 13890995, "sha256": "1fff9e1d0ef3ebac86767b8ca6a093e058f1dcb236eb064acbaf05f055cb13ac"},
    "2002": {"bytes": 14608544, "sha256": "c3d103466b90987ada09561d9b56b482a39db8cb1eaf6513654b6d97c81da94f"},
    "2003": {"bytes": 14381489, "sha256": "7a06c96ebefa19434c6a28cad8b17f11d9451a425eb6422ea6267777963d7f46"},
    "2004": {"bytes": 14348591, "sha256": "b2cf1feb6bf97e9e2295597e6eca8d0ff1a466c1cc91166eb03a668c48a13bcb"},
    "2005": {"bytes": 14373053, "sha256": "3f8e09c236cfa6fb189166ae39ecc84d704f10727e3852c5fb9b589ce71310be"},
    "2006": {"bytes": 17478126, "sha256": "d569c37a14000864392283006cae6f1ac43b4bb9e904fa1568db8cc8eb952d08"},
    "2007": {"bytes": 17618113, "sha256": "fa436bf2cc476e8a0dc044a0e516e88e48f74f8342de2effa2262fdacac2adb0"},
    "2008": {"bytes": 17469625, "sha256": "d889f7f473304763ed00cb4e8f26cb110122ea5b712f49b4e94d3e5c19dab483"},
    "2009": {"bytes": 17819486, "sha256": "15a6424d6629c1f02803e26b85bbbec9f2c43f48cf20f0f737cbb82e99855dd3"},
    "2010": {"bytes": 17960191, "sha256": "6d4f519b4821f06ffe2285faadad8cee718f464eeb9a310a8276753da10bee2e"},
    "2011": {"bytes": 18021304, "sha256": "ffa0bfb803f0c30a4b765af5e621b0f601d695efdba7d6d396fb067d6a0c89b2"},
    "2012": {"bytes": 18195788, "sha256": "ff1cda2e26610ef8720324e442d1e5ebc24918e08fd1d2933590b7b91a7068cd"},
    "2013": {"bytes": 18339628, "sha256": "b4cd0dc48697ec44a2041d57dfc1c7210a792d1b5be497c161ef20a5b7fb9942"},
    "2014": {"bytes": 18202391, "sha256": "4ab286d2873a7eab28e11120f1fe1bd1f0a6bbb6be452f86a76092bd49b2ee4a"},
    "2015": {"bytes": 18392969, "sha256": "837811471df06c43d55faede7a363274484da11e71f49d8e0801baede3a5965d"},
    "2016": {"bytes": 18230362, "sha256": "dc1703d9aae3ec697ac2787617f82b6f074d0c151e1a54f8217f570a63dcb5ed"},
    "2017": {"bytes": 17973428, "sha256": "74032964ca6bdfc5bbe186253231a533d2d82aa531fac02b9382da01b3a8b34e"},
    "2018": {"bytes": 17920627, "sha256": "d37899610b397b96a90da6902827de5b46c5457b93e0e16d5f95a8eb524e4a7d"},
    "2019": {"bytes": 17992849, "sha256": "b764668137052be23745953cbc33fa17e537a70c81e9b73785a19a15e7288216"},
    "2020": {"bytes": 18143553, "sha256": "bc954b5780e325df4faa255fac092585c981a4de5d2f553a8a4e0a3c8b3a3cf2"},
    "2021": {"bytes": 19022201, "sha256": "e8743a568f99667a8bdcd29ba12224ee1782ad2b7916bee78ee335c099383739"},
    "2022": {"bytes": 19093961, "sha256": "0c69a71eb39498956c7b1d5c1ca52ce7fe679934a95d1af249facb5ea9829ea4"},
    "2023": {"bytes": 19169807, "sha256": "4649804ee0f0a40b41e51ec75a1ce921949d7fab5459213488656b92f78560e8"},
    "2024": {"bytes": 19362351, "sha256": "23370d5d10f8104d80d46a1fc5e61f4f6f5a3263fe96fe2dd629913cfcb08c06"},
    "2025": {"bytes": 19105296, "sha256": "2f135887790a013fd004e609e37096bb4816d5cc80b9f19122e1bad478961978"},
}

SNAP_SOURCE_ASSET_DIGESTS = {
    "2012": {"bytes": 154, "sha256": "802475aec59f0b125de85a6c35eb69e289a512bcf62d6be007d385ea8d613ebd"},
    "2013": {"bytes": 2148056, "sha256": "9d1fdd557875b9a2c71d88e583da24a739dfca671edf8223d6171e39a421000e"},
    "2014": {"bytes": 2156558, "sha256": "84540709d9483552185c8ca3ef30b2f66fb94b30c3e6c21e7a27205f337f21d3"},
    "2015": {"bytes": 2155797, "sha256": "841496252ada825c3c50d3ba9b4b26d3f4fabd0fedc5fb44f286ddaaa5bdb7e0"},
    "2016": {"bytes": 2157110, "sha256": "a778e24e9eb665ffe8f16093b03c5a263dca0250b3aa92bd6002f61f534bea59"},
    "2017": {"bytes": 2157818, "sha256": "eab2fad2df99249d4061ed9c5c34d312cdba07cf3159d027d255bdc9b6526917"},
    "2018": {"bytes": 2157555, "sha256": "9a408b78a55f799110aed70de3564e157faf089ce9bed780d0368bf49ae13e0b"},
    "2019": {"bytes": 2156311, "sha256": "0cd52be8fa57f18503b670cb8263246c4f548375b768cbcad1c7a7462c98970d"},
    "2020": {"bytes": 2257664, "sha256": "c3b2a9aa472aca23332c030e574cfb134f885608ddf74fe0a659c38584d14358"},
    "2021": {"bytes": 2388493, "sha256": "8e4dae054a4749cf2d4919508d9161a6068fd67509979aefa385bfb3803d0ee5"},
    "2022": {"bytes": 2379719, "sha256": "0018a4833fbf0f825286c1c27450c6254391b548d6c55bcde728e93e4816815a"},
    "2023": {"bytes": 2394875, "sha256": "303b61aa5c33ffda863f93a750fc14483f397f9187ad502b1ce71e9b516a64c0"},
    "2024": {"bytes": 2402841, "sha256": "a2aa58efe093f8aa0ad5aadf09f81d8ec690a1183bd2dde68d20e7f109a9c335"},
    "2025": {"bytes": 2401193, "sha256": "80b02a6e511aa20283551cae622b29ba4d0a6f006c489a2d91591fcad33792e7"},
}
