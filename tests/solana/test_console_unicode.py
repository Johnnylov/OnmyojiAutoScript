"""Console encoding must not turn a successful game step into a task failure."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from colorama import AnsiToWin32
from rich.console import Console
from module.console_stream import console_stream


MESSAGE = '✅ 已确认4星太鼓奖励，后续跳过4星以下同类型卡片 ⏭️ 🎮'


class FixedGbkStream:
    encoding = 'gbk'

    def __init__(self):
        self.buffer = io.BytesIO()

    def write(self, text):
        return self.buffer.write(text.encode('gbk', 'strict'))

    def isatty(self):
        return False

    def flush(self):
        pass


class ConsoleUnicodeTests(unittest.TestCase):
    def test_reconfigurable_gbk_pipe_keeps_exact_unicode(self):
        buffer = io.BytesIO()
        text = io.TextIOWrapper(buffer, encoding='gbk', errors='strict')
        output = console_stream(text)
        output.write(MESSAGE)
        output.flush()
        self.assertEqual(buffer.getvalue().decode('utf-8'), MESSAGE)
        self.assertEqual(text.encoding, 'utf-8')

    def test_fixed_encoding_colorama_wrapper_escapes_only_console(self):
        target = FixedGbkStream()
        wrapped = AnsiToWin32(target, convert=False, strip=True).stream
        console = Console(file=console_stream(wrapped), legacy_windows=False,
                          force_terminal=False, highlight=False, width=160)
        console.print(MESSAGE)
        rendered = target.buffer.getvalue().decode('gbk')
        self.assertIn('已确认4星太鼓奖励', rendered)
        self.assertIn(r'\u2705', rendered)
        self.assertIn(r'\U0001f3ae', rendered)

    def test_spawned_logger_with_gbk_console_keeps_utf8_file_and_gui(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            (tmp / 'module').mkdir()
            # Only __file__/log output live in the temporary fixture; the
            # production config directory and device modules are never loaded.
            copied = tmp / 'module/logger.py'
            shutil.copy2(root / 'module/logger.py', copied)
            script = """
import runpy, sys
from colorama import AnsiToWin32
sys.stdout = AnsiToWin32(sys.stdout, convert=False, strip=True).stream
module = runpy.run_path(sys.argv[1])
logger = module['logger']
logger.set_file_logger('unicode-regression')
gui = []
logger.set_func_logger(gui.append)
message = sys.argv[2]
logger.info(message)
logger.print(message)
try:
    raise RuntimeError(message)
except RuntimeError:
    logger.exception('测试中文异常输出')
assert any(message in item for item in gui)
for handler in logger.handlers:
    handler.flush()
"""
            env = dict(os.environ, PYTHONIOENCODING='gbk:strict', PYTHONUTF8='0')
            env['PYTHONPATH'] = str(root) + os.pathsep + env.get('PYTHONPATH', '')
            result = subprocess.run([sys.executable, '-c', script, str(copied), MESSAGE],
                                    cwd=root, env=env, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
            self.assertIn(MESSAGE, result.stdout.decode('utf-8'))
            log = next((tmp / 'log').glob('*_unicode-regression.txt')).read_text(encoding='utf-8')
            self.assertIn(MESSAGE, log)
            self.assertNotIn('UnicodeEncodeError', log)


if __name__ == '__main__':
    unittest.main()
