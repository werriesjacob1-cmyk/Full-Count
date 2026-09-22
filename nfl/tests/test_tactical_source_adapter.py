import unittest

from nfl.research.tactical_source_adapter import capture, bind_pass_targets, summarize


class TacticalSourceTests(unittest.TestCase):
    def setUp(self):
        self.raw = b'nflverse_game_id,play_id,possession_team,defense_man_zone_type,route,was_pressure\n2024_01_TEN_CHI,41,TEN,MAN_COVERAGE,,FALSE\n'
        self.source = capture(self.raw, kind='participation', season=2024, captured_at='2026-09-22T22:24:42Z')
        self.pbp = {'game_id': '2024_01_TEN_CHI', 'play_id': '41', 'game_date': '2024-09-08', 'pass_attempt': '1', 'play_type': 'pass', 'no_play': '0', 'receiver_player_id': '00-0031234', 'posteam': 'TEN', 'defteam': 'CHI', 'complete_pass': '1'}

    def bind(self, rows=None, cutoff='2026-09-23T00:00:00Z'):
        return bind_pass_targets(self.source, rows or [self.pbp], pbp_captured_at='2026-09-22T22:24:42Z', cutoff=cutoff)

    def test_partial_fields_are_useful_and_unknown_not_false(self):
        result = summarize(self.bind())
        self.assertEqual(result['bound_targets'], 1)
        self.assertEqual(result['field_coverage']['route']['unknown'], 1)
        self.assertEqual(result['field_coverage']['was_pressure']['known'], 1)
        self.assertEqual(result['conditional_cells'][0]['catches'], 1)

    def test_backdated_cutoff_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.bind(cutoff='2024-09-09T00:00:00Z')

    def test_duplicate_pbp_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate pbp'):
            self.bind([self.pbp, self.pbp])

    def test_duplicate_chart_rejected(self):
        self.source['rows'] *= 2
        with self.assertRaisesRegex(ValueError, 'duplicate chart'):
            self.bind()

    def test_unknown_target_is_not_bound(self):
        self.pbp['receiver_player_id'] = ''
        self.assertEqual(self.bind()['excluded'], {'UNKNOWN_TARGET_IDENTITY': 1})

    def test_team_mismatch(self):
        self.pbp['posteam'] = 'CHI'
        self.assertEqual(self.bind()['excluded'], {'TEAM_MISMATCH': 1})

    def test_same_day_game_excluded(self):
        self.pbp['game_date'] = '2026-09-23'
        self.assertEqual(self.bind()['excluded'], {'GAME_NOT_STRICTLY_PRIOR': 1})

    def test_no_play_excluded(self):
        self.pbp['no_play'] = '1'
        self.assertEqual(self.bind()['excluded'], {'NOT_LEGAL_PASS_ATTEMPT': 1})

    def test_timezone_required(self):
        with self.assertRaisesRegex(ValueError, 'timezone'):
            self.bind(cutoff='2026-09-23T00:00:00')

    def test_current_ftn_joins_without_faking_missing_coverage(self):
        raw = b'nflverse_game_id,nflverse_play_id,is_motion,read_thrown\n2026_01_TEN_CHI,41,FALSE,0\n'
        self.source = capture(raw, kind='ftn_charting', season=2026, captured_at='2026-09-22T22:24:42Z')
        self.pbp['game_id'] = '2026_01_TEN_CHI'
        self.pbp['game_date'] = '2026-09-08'
        result = summarize(self.bind())
        self.assertEqual(result['bound_targets'], 1)
        self.assertEqual(result['conditional_cells'], [])
        self.assertEqual(result['field_coverage']['read_thrown']['known'], 1)


if __name__ == '__main__':
    unittest.main()

