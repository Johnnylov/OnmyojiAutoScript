"""True Orochi rendezvous regressions using actual file locks and journals."""
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tasks.TrueOrochi.team import LocalTeam, TeamSyncError, choose_plan, week_key


def concurrent_player(directory, name, result):
    try:
        peer = 'b' if name == 'a' else 'a'
        team = LocalTeam(name, peer, 'a', 'true_orochi_random', directory)
        team.ready(0, 2, 2)
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            row = team.round_state(0)
            if 'decision' in row:
                result.put(('ok', name, team.session, row['decision']))
                return
            time.sleep(.03)
        result.put(('error', name, 'timeout'))
    except Exception as exc:
        result.put(('error', name, repr(exc)))


class TrueOrochiTeamTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1000

    def player(self, name, mode='true_orochi_leader_twice', week='2026-38'):
        return LocalTeam(name, 'b' if name == 'a' else 'a', 'a', mode,
                         self.directory.name, clock=lambda: self.now, week=week)

    def pair(self, mode='true_orochi_leader_twice'):
        return self.player('a', mode), self.player('b', mode)

    def decision(self, a, b, number, entries=(2, 2), rewards=(2, 2)):
        a.ready(number, entries[0], rewards[0])
        self.assertNotIn('decision', a.round_state(number))
        b.ready(number, entries[1], rewards[1])
        first, second = a.round_state(number), b.round_state(number)
        self.assertEqual(first['decision'], second['decision'])
        return first['decision']

    def test_all_three_hosting_modes(self):
        for mode, expected in [('true_orochi_leader_twice', ['a', 'a']),
                               ('true_orochi_split', ['a', 'b']),
                               ('true_orochi_member_twice', ['b', 'b'])]:
            with self.subTest(mode=mode):
                a, b = self.pair(mode)
                self.assertEqual(self.decision(a, b, 0), expected[0])
                self.assertEqual(self.decision(a, b, 1, rewards=(1, 1)), expected[1])
                a.close(); b.close()

    def test_random_can_choose_each_plan(self):
        for mode, plan in [('true_orochi_leader_twice', ['a', 'a']),
                           ('true_orochi_split', ['a', 'b']),
                           ('true_orochi_member_twice', ['b', 'b'])]:
            draw = Mock(return_value=mode)
            self.assertEqual(choose_plan('a', 'b', 'true_orochi_random', draw), plan)
            self.assertEqual(len(draw.call_args.args[0]), 3)

    def test_leader_one_entry_then_member_hosts_second(self):
        a, b = self.pair()
        self.assertEqual(self.decision(a, b, 0, entries=(1, 2)), 'a')
        self.assertEqual(self.decision(a, b, 1, entries=(0, 2), rewards=(1, 1)), 'b')

    def test_empty_initial_host_falls_back_in_either_direction(self):
        a, b = self.pair()
        self.assertEqual(self.decision(a, b, 0, entries=(0, 2)), 'b')
        a.close(); b.close()
        a, b = self.pair('true_orochi_member_twice')
        self.assertEqual(self.decision(a, b, 0, entries=(2, 0)), 'a')

    def test_no_entries_and_no_rewards_are_distinct(self):
        a, b = self.pair()
        self.assertEqual(self.decision(a, b, 0, entries=(0, 0)), 'no_entries')
        self.assertEqual(self.decision(a, b, 1, rewards=(0, 0)), 'finished')

    def test_completed_account_can_help_partner(self):
        a, b = self.pair()
        self.assertEqual(self.decision(a, b, 0, entries=(1, 0), rewards=(0, 1)), 'a')

    def test_unknown_ocr_counts_are_not_zero(self):
        a, _ = self.pair()
        for entries, rewards in [(None, 2), (2, None), (-1, 2), (2, 3)]:
            with self.assertRaises(TeamSyncError):
                a.ready(0, entries, rewards)

    def test_done_barrier_survives_faster_peer_next_round(self):
        a, b = self.pair()
        self.decision(a, b, 0)
        a.joined(0); b.joined(0)
        a.done(0, True)
        self.assertEqual(len(b.round_state(0)['done']), 1)
        b.done(0, True)
        a.ready(1, 1, 1)
        self.assertEqual(b.round_state(0)['done'], {'a': True, 'b': True})
        self.assertNotIn('decision', a.round_state(1))

    def test_failed_battle_cancels_other_process(self):
        a, b = self.pair()
        self.decision(a, b, 0)
        a.done(0, False)
        with self.assertRaises(TeamSyncError):
            b.heartbeat()

    def test_crashed_peer_is_not_reused(self):
        a, b = self.pair()
        self.now += 181
        with self.assertRaises(TeamSyncError):
            a.heartbeat()

    def test_replacement_fences_old_runner_and_old_cleanup(self):
        a, b = self.pair()
        a.close('invitation timeout')
        replacement = self.player('a')
        with self.assertRaises(TeamSyncError):
            b.ready(0, 2, 2)
        b.close('late cleanup')
        replacement.heartbeat()

    def test_duplicate_runner_is_rejected(self):
        a, b = self.pair()
        with self.assertRaises(TeamSyncError):
            self.player('a')
        a.heartbeat(); b.heartbeat()

    def test_weekly_random_plan_persists_across_retries(self):
        a, b = self.pair('true_orochi_random')
        original = json.loads(a.path.read_text(encoding='utf-8'))['plan']
        a.close('retry'); b.close()
        a = self.player('a', 'true_orochi_random')
        self.assertEqual(json.loads(a.path.read_text(encoding='utf-8'))['plan'], original)

    def test_new_week_resets_round_journal(self):
        a, b = self.pair()
        self.decision(a, b, 0)
        a.close(); b.close()
        a = self.player('a', week='2026-39')
        self.assertEqual(a.round_state(0), {})

    def test_iso_week_includes_year(self):
        from datetime import datetime
        self.assertEqual(week_key(datetime(2027, 1, 1)), '2026-53')
        self.assertEqual(week_key(datetime(2027, 1, 4)), '2027-01')

    def test_concurrent_processes_share_one_draw_and_decision(self):
        context = multiprocessing.get_context('spawn')
        queue = context.Queue()
        workers = [context.Process(target=concurrent_player, args=(self.directory.name, name, queue))
                   for name in ('a', 'b')]
        for worker in workers:
            worker.start()
        try:
            results = [queue.get(timeout=15) for _ in workers]
            self.assertTrue(all(row[0] == 'ok' for row in results), results)
            self.assertEqual(results[0][2:], results[1][2:])
        finally:
            for worker in workers:
                worker.join(timeout=3)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=3)
            queue.close()


if __name__ == '__main__':
    unittest.main()
