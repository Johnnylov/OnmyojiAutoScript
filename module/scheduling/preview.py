"""Best-effort thumbnails of frames the executor already captured.

The single-slot queue is separate from durable control RPC. A slow/disconnected
UI can never block a battle or enlarge an unbounded screenshot backlog.
"""
import base64
from datetime import datetime, timezone
from queue import Full, Empty
import time
import uuid


class PreviewPublisher:
    def __init__(self, queue, interval=2.0, clock=time.monotonic, encoder=None, metadata=None):
        self.queue, self.interval, self.clock = queue, interval, clock
        self.encoder = encoder or self._encode
        self.next_at = 0.0
        self.metadata = metadata

    @staticmethod
    def _encode(frame):
        import cv2
        height, width = frame.shape[:2]
        scale = min(320 / width, 180 / height, 1.0)
        small = cv2.resize(frame, (max(1, int(width * scale)), max(1, int(height * scale))))
        # OAS screenshots are RGB; OpenCV encodes BGR.
        small = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
        ok, buffer = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 65])
        return buffer.tobytes() if ok else None

    def __call__(self, frame):
        now = self.clock()
        if now < self.next_at:
            return
        self.next_at = now + self.interval
        try:
            data = self.encoder(frame)
            if not data or len(data) > 96 * 1024:
                return
            self.queue.put_nowait({'available': True, 'frame_id': str(uuid.uuid4()),
                'occurred_at': datetime.now(timezone.utc).isoformat(), 'mime_type': 'image/jpeg',
                'image_base64': base64.b64encode(data).decode('ascii'),
                'progress': self.metadata() if self.metadata else None})
        except Exception:
            # Thumbnail failures are optional telemetry failures, never task
            # failures. KeyboardInterrupt/SystemExit still propagate normally.
            pass


def consume_previews(queue, service, owner_id, process):
    while process.is_alive():
        try:
            frame = queue.get(timeout=0.25)
            service.record_preview(owner_id, frame)
        except Empty:
            continue
        except (OSError, EOFError):
            break
