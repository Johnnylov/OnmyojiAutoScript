"""No real process, device or socket: exercise start/stop at broadcast yields."""
import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.server import solana_bridge, solana_runtime
from module.scheduling import preview


def process_methods(namespace):
    path = Path(__file__).resolve().parents[2] / 'module/server/script_process.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    source = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ScriptProcess')
    methods = [node for node in source.body if isinstance(node, ast.AsyncFunctionDef) and node.name in {'start', 'stop'}]
    assert len(methods) == 2
    subject = ast.ClassDef(name='ScriptProcess', bases=[], keywords=[], body=methods, decorator_list=[])
    isolated = ast.fix_missing_locations(ast.Module(body=[subject], type_ignores=[]))
    exec(compile(isolated, str(path), 'exec'), namespace)
    return namespace['ScriptProcess']


class ProcessStartOrderTests(unittest.TestCase):
    def subject(self):
        events = []
        owner = {'alive': False}

        class FakeProcess:
            exitcode = -15
            alive = False

            def start(self):
                events.append('process.started')
                self.alive = True

            def is_alive(self):
                return self.alive

            def terminate(self):
                events.append('process.terminated')
                self.alive = False

            def join(self, *args):
                events.append('process.joined')

        process = FakeProcess()
        context = Mock()
        context.Pipe.return_value = (Mock(), Mock())
        context.Process.return_value = process

        def register(name, owner_id):
            owner['alive'] = True
            events.append('owner.registered')
            return {'profile_id': 'profile-a', 'owner_id': owner_id, 'device_id': 'test-device'}

        def exited(owner_id, code):
            owner['alive'] = False
            events.append('owner.exited')

        runtime = Mock(register_process=Mock(side_effect=register),
                       process_exited=Mock(side_effect=exited))
        cls = process_methods({
            'logger': Mock(), 'uuid': SimpleNamespace(uuid4=lambda: 'owner-1'),
            'threading': Mock(), 'func': Mock(), '_SCRIPT_PROCESS_CONTEXT': context,
            'ScriptState': SimpleNamespace(RUNNING=1, INACTIVE=0),
        })
        subject = cls()
        subject.config_name = 'test-profile'
        subject._process = None
        subject._owner_id = None
        subject.state_queue = Mock()
        subject.log_pipe_in = Mock()

        async def immediate_join(function, *args):
            return function(*args)

        for replacement in (
            patch.object(solana_runtime, 'get_runtime', return_value=runtime),
            patch.object(solana_bridge, 'ProcessBridge', return_value=Mock()),
            patch.object(solana_bridge, 'serve_bridge', Mock()),
            patch.object(preview, 'consume_previews', Mock()),
            patch('asyncio.to_thread', immediate_join),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)
        return subject, process, owner, events, runtime

    def test_stop_arriving_during_start_broadcast_terminates_the_started_process(self):
        subject, process, owner, events, runtime = self.subject()

        async def broadcast(data):
            events.append(f'broadcast.{data["state"]}')
            if data['state'] == 1:
                self.assertTrue(process.alive)
                self.assertIs(subject._process, process)
                self.assertEqual(subject._owner_id, 'owner-1')
                await subject.stop()

        subject.broadcast_state = broadcast
        asyncio.run(subject.start())
        self.assertFalse(process.alive)
        self.assertFalse(owner['alive'])
        self.assertIsNone(subject._process)
        self.assertLess(events.index('process.started'), events.index('broadcast.1'))
        runtime.process_exited.assert_called_once_with('owner-1', -15)

    def test_process_start_failure_never_announces_running_or_keeps_owner(self):
        subject, process, owner, events, runtime = self.subject()
        process.start = Mock(side_effect=OSError('cannot spawn'))
        broadcast = Mock()

        async def announce(data):
            broadcast(data)

        subject.broadcast_state = announce
        with self.assertRaises(OSError):
            asyncio.run(subject.start())
        self.assertFalse(owner['alive'])
        broadcast.assert_not_called()
        runtime.process_exited.assert_called_once_with('owner-1', -1)


if __name__ == '__main__':
    unittest.main()
