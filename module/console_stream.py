"""Unicode-safe console output; file and GUI logs keep their original text."""
import io
import sys


class SafeConsoleStream:
    """Only the optional console may escape characters its encoding cannot print.

    A colorama/launcher wrapper can reject reconfigure or change stdout after
    startup. Keep a final write fallback so logging cannot abort a game task.
    """
    def __init__(self, stream):
        self.stream = stream

    def __getattr__(self, name):
        return getattr(self.stream, name)

    @property
    def encoding(self):
        return getattr(self.stream, 'encoding', None) or 'utf-8'

    def write(self, text):
        if self.stream is None:
            return len(text)
        try:
            self.stream.write(text)
        except UnicodeEncodeError as error:
            escaped = text.encode(error.encoding, errors='backslashreplace').decode(error.encoding)
            self.stream.write(escaped)
        return len(text)

    def flush(self):
        if self.stream is not None:
            self.stream.flush()

    def isatty(self):
        return bool(self.stream is not None and self.stream.isatty())


def console_stream(stream=None):
    """Set UTF-8 where supported, including Windows spawned workers/pipes."""
    stream = sys.stdout if stream is None else stream
    if stream is not None:
        try:
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
        except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
            # Some hosts expose a fixed-encoding wrapper; SafeConsoleStream
            # protects that output without altering UTF-8 file/GUI handlers.
            pass
    return SafeConsoleStream(stream)
