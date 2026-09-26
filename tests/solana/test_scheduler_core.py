import copy
import unittest
from tempfile import TemporaryDirectory

from module.scheduling.core import Candidate, FairScheduler, Policy
from module.scheduling.coordinator import Coordinator, LeaseLost, normalize_device_id
from module.scheduling.fence import DeviceProcessLock


class SchedulerTests(unittest.TestCase):
    def test_weighted_10000_seconds(self):
        engine = FairScheduler()
        policy = Policy(mode='eevdf', max_wait_seconds=100000)
        candidates = [Candidate(str(w), 'p', str(w), weight=w, quantum=30) for w in (1, 2, 3)]
        totals = {str(w): 0 for w in (1, 2, 3)}
        now = 0
        while now < 10000:
            result = engine.choose(candidates, now, now, policy)
            duration = min(30, 10000 - now)
            totals[result['key']] += duration
            now += duration
            engine.account(result['key'], duration, now, now, policy=policy)
        for weight in (1, 2, 3):
            self.assertLess(abs(totals[str(weight)] / 10000 - weight / 6), .05, totals)

    def test_future_disabled_and_restart_debt(self):
        policy = Policy(mode='eevdf')
        engine = FairScheduler()
        items = [Candidate('a', 'p', 'a'), Candidate('b', 'p', 'b'),
                 Candidate('future', 'p', 'future', release_at=500),
                 Candidate('disabled', 'p', 'disabled', enabled=False)]
        engine.choose(items, 0, 0, policy)
        engine.account('a', 120, 120, 120, policy=policy)
        self.assertLess(engine.entities['a']['lag'], 0)
        state = copy.deepcopy(engine.snapshot())
        restored = FairScheduler(state)
        selected = restored.choose(items, 121, 0, policy)
        self.assertEqual(selected['key'], 'b')
        self.assertNotIn('future', restored.entities)
        self.assertNotIn('disabled', restored.entities)
        # Repeated departure/rejoin never turns a debt into a positive credit.
        for _ in range(10):
            restored.choose([items[1]], 121, 0, policy)
            restored.choose(items, 121, 0, policy)
            self.assertGreaterEqual(restored.entities['a']['v'], restored.entities['b']['v'])

    def test_weight_change_preserves_physical_debt(self):
        engine, policy = FairScheduler(), Policy(mode='eevdf')
        items = [Candidate('a', 'p', 'a'), Candidate('b', 'p', 'b')]
        engine.choose(items, 0, 0, policy)
        engine.account('a', 100, 100, 100, policy=policy)
        reference = engine.virtual_time()
        before = engine.entities['a']['weight'] * (reference - engine.entities['a']['v'])
        policy.weights = {'a': 3}
        engine.sync(items, 100, 100, policy)
        after = 3 * (reference - engine.entities['a']['v'])
        self.assertAlmostEqual(before, after)

    def test_recovery_budget_and_backoff_not_fair_fallback(self):
        engine = FairScheduler()
        policy = Policy(mode='eevdf', urgent_budget_seconds=30, recovery_backoff_seconds=10)
        items = [Candidate('restart', 'p', 'Restart', quantum=30, recovery=True),
                 Candidate('daily', 'p', 'Daily', quantum=30)]
        result = engine.choose(items, 0, 0, policy)
        self.assertEqual(result['reason'], 'recovery')
        engine.account('restart', 30, 30, 30, result['reason'], True, policy)
        result = engine.choose(items, 31, 31, policy)
        self.assertEqual(result['key'], 'daily')
        result = engine.choose(items, 50, 50, policy)
        self.assertEqual(result['key'], 'daily')
        self.assertEqual(result['blocked']['restart'], 'urgent_budget_exhausted')

    def test_long_wait_protection_with_continuous_short_arrivals(self):
        engine, policy = FairScheduler(), Policy(mode='eevdf', max_wait_seconds=30)
        long = Candidate('long', 'p', 'Long', quantum=1000)
        served = False
        for now in range(0, 60, 5):
            short = Candidate('short' + str(now), 'p', 'Short', quantum=5)
            result = engine.choose([long, short], now, now, policy)
            if result['key'] == 'long':
                served = True
                self.assertLessEqual(now, 30)
                break
            engine.account(result['key'], 5, now + 5, now + 5, policy=policy)
        self.assertTrue(served)

    def test_empty_invalid_and_virtual_vs_real_deadline(self):
        engine, policy = FairScheduler(), Policy(mode='eevdf')
        self.assertIsNone(engine.choose([], 100, 100, policy))
        result = engine.choose([Candidate('a', 'p', 'A', quantum=20, deadline=110)], 100, 100, policy)
        self.assertEqual(result['reason'], 'real_deadline')
        self.assertFalse(result['overdue'])
        with self.assertRaises(ValueError):
            Policy(weights={'bad': float('nan')})


class CoordinatorTests(unittest.TestCase):
    def payload(self, profile='p', owner='o', task='A', device='one'):
        return {'profile_id': profile, 'owner_id': owner, 'device_id': device,
                'candidates': [{'task': task, 'quantum': 10}], 'legacy_order': [task]}

    def test_aliases_and_no_timeout_takeover(self):
        self.assertEqual(normalize_device_id('localhost:5555'), normalize_device_id('emulator-5554'))
        self.assertEqual(normalize_device_id('[::1]:5555'), normalize_device_id('127.0.0.1:5555'))
        self.assertEqual(normalize_device_id('10.0.0.1:5555', {'10.0.0.1:5555': 'physical-a'}), 'physical-a')
        coordinator = Coordinator(clock=lambda: 100000)
        lease = coordinator.acquire(self.payload())['lease']
        self.assertEqual(coordinator.acquire(self.payload('q', 'new'))['status'], 'waiting_resource')
        with self.assertRaises(LeaseLost):
            coordinator.revoke_owner('o', confirmed_dead=False)
        self.assertTrue(coordinator.validate(lease)['valid'])
        coordinator.revoke_owner('o', confirmed_dead=True)
        new = coordinator.acquire(self.payload('q', 'new'))['lease']
        self.assertGreater(new['generation'], lease['generation'])
        with self.assertRaises(LeaseLost):
            coordinator.validate(lease)

    def test_global_candidates_and_distinct_devices(self):
        coordinator = Coordinator(clock=lambda: 100)
        coordinator.set_policy({'mode': 'eevdf', 'weights': {'B': 3}})
        coordinator.register_profile('one', 'q', 'other', [{'task': 'B', 'quantum': 10}], ['B'])
        first = coordinator.acquire(self.payload())
        self.assertEqual(first['status'], 'waiting_resource')
        self.assertEqual(first['holder'], 'q')
        lease = coordinator.acquire(self.payload('q', 'other', 'B'))['lease']
        separate = coordinator.acquire(self.payload('r', 'third', 'C', 'two'))
        self.assertEqual(separate['status'], 'acquired')
        coordinator.release({'lease': lease, 'actual_seconds': 10, 'outcome': 'succeeded'})
        self.assertEqual(coordinator.acquire(self.payload())['status'], 'acquired')

    def test_shadow_keeps_legacy_and_explains_difference(self):
        coordinator = Coordinator(clock=lambda: 100)
        coordinator.set_policy({'mode': 'eevdf_shadow', 'weights': {'B': 3}})
        payload = self.payload()
        payload['candidates'].append({'task': 'B', 'quantum': 10})
        payload['legacy_order'].append('B')
        result = coordinator.acquire(payload)
        self.assertEqual(result['lease']['task'], 'A')
        self.assertEqual(result['decision']['fair_key'], 'p:B')
        self.assertEqual(result['decision']['mode'], 'eevdf_shadow')

    def test_generation_persistence_precedes_grant(self):
        def fail(*args):
            raise OSError('disk full')
        coordinator = Coordinator(save=fail)
        with self.assertRaises(OSError):
            coordinator.acquire(self.payload())
        self.assertIsNone(coordinator.snapshot()['one']['lease'])

    def test_restart_epoch_invalidates_previous_authority(self):
        state = {}
        first = Coordinator(save=lambda key, data: state.update({key: copy.deepcopy(data)}))
        lease = first.acquire(self.payload())['lease']
        second = Coordinator(load=lambda key: state.get(key))
        current = second.acquire(self.payload())['lease']
        self.assertNotEqual(current['epoch'], lease['epoch'])
        with self.assertRaises(LeaseLost):
            second.validate(lease)

    def test_pause_release_and_resume(self):
        coordinator = Coordinator()
        lease = coordinator.acquire(self.payload())['lease']
        coordinator.request_control('p', 'pause')
        self.assertEqual(coordinator.validate(lease)['control'], 'pause')
        coordinator.release({'lease': lease, 'actual_seconds': 5, 'outcome': 'paused'})
        self.assertEqual(coordinator.acquire(self.payload())['status'], 'waiting')
        coordinator.request_control('p', 'resume')
        self.assertEqual(coordinator.acquire(self.payload())['status'], 'acquired')

    def test_control_before_registration_and_immediate_stop(self):
        coordinator = Coordinator()
        coordinator.request_control('p', 'pause')
        self.assertEqual(coordinator.acquire(self.payload())['status'], 'waiting')
        coordinator.request_control('p', 'immediate_stop')
        self.assertEqual(coordinator.dispatch('scheduling.control', {
            'device_id': 'one', 'profile_id': 'p', 'action': 'status'})['control'], 'stop')

    def test_os_device_lock_survives_new_coordinator_epoch(self):
        with TemporaryDirectory() as directory:
            old = DeviceProcessLock('same-device', directory)
            new = DeviceProcessLock('same-device', directory)
            self.assertTrue(old.acquire())
            try:
                self.assertFalse(new.acquire())
            finally:
                old.release()
            self.assertTrue(new.acquire())
            new.release()


if __name__ == '__main__':
    unittest.main()
