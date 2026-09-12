"""Date-specific soul choices for the September 2026 虚无精锐 event.

The supplied recommendation chart has 15 souls per day (8 + 7). The game
displays the same reading order in a 3 x 5 grid. Slots are one-based; they
are deliberately date-specific because the contents of a slot change daily.
"""
from datetime import date, datetime, timedelta, timezone
import random
import time


EVENT_START = date(2026, 9, 9)
EVENT_END = date(2026, 9, 29)
CHINA_TIME = timezone(timedelta(hours=8))
RECOMMENDED_SLOTS = (
    (1, 4, 10, 13, 14),       # 9 / 15 / 21 / 27
    (1, 4, 6, 13, 14),        # 10 / 16 / 22 / 28
    (1, 4, 6, 14),            # 11 / 17 / 23 / 29
    (1, 4, 6, 10, 13, 14),    # 12 / 18 / 24
    (1, 4, 13),               # 13 / 19 / 25: 狂骨、镇墓兽、火灵
    (1, 4, 6, 13, 14),        # 14 / 20 / 26
)


def server_date():
    """Use the game's Chinese calendar even if the host has another timezone."""
    return datetime.now(CHINA_TIME).date()


def recommendations(day):
    if not EVENT_START <= day <= EVENT_END:
        return ()
    return RECOMMENDED_SLOTS[(day - EVENT_START).days % 6]


def choose_souls(day, rng=None):
    """Sample four distinct slots, filling only when recommendations lack four."""
    preferred = recommendations(day)
    if not preferred:
        raise ValueError('Date is outside the current soul selection event')
    rng = rng or random
    if len(preferred) >= 4:
        return frozenset(rng.sample(preferred, 4))
    others = [slot for slot in range(1, 16) if slot not in preferred]
    return frozenset(preferred + tuple(rng.sample(others, 4 - len(preferred))))


def recorded_today(record, day, owner):
    slots = set(record.slots)
    return (bool(owner) and record.owner == owner
            and record.date == day.isoformat() and len(record.slots) == 4
            and len(slots) == 4 and slots <= set(range(1, 16)))


class SoulSelectionError(RuntimeError):
    """The UI did not confirm a choice; the caller must not start a battle."""


class DailySoulSelector:
    """Bounded UI transaction with fresh-frame checks and persisted readback.

    ``capture`` returns an RGB screenshot. ``click`` takes an (x,y,w,h) ROI
    and a control name. A view supplies image recognition without device I/O.
    The caller saves the daily record only after this transaction returns.
    """

    def __init__(self, capture, click, view, today=server_date, sleep=time.sleep):
        self.capture = capture
        self.click = click
        self.view = view
        self.today = today
        self.sleep = sleep

    def _check_day(self, day):
        if self.today() != day:
            raise SoulSelectionError('御魂选择期间已跨日，将在下次任务按新日期重试')

    def _read_panel(self, day, expected=None):
        """Require two consistent frames, allowing a click animation to settle."""
        previous = None
        for _ in range(10):
            self._check_day(day)
            image = self.capture()
            panel = self.view.find_panel(image)
            selected = None if panel is None else self.view.selected(image, panel)
            if selected is not None and len(selected) <= 4:
                if selected == previous and (expected is None or selected == expected):
                    return panel, selected
                previous = selected
            else:
                previous = None
            self.sleep(0.25)
        raise SoulSelectionError('无法确认御魂面板或勾选结果')

    def _open_panel(self, day):
        self._check_day(day)
        image = self.capture()
        if self.view.find_panel(image) is None:
            entry = self.view.find_entry(image)
            if entry is None:
                raise SoulSelectionError('没有识别到御魂自选入口')
            self.click(entry, 'daily_souls_open')
        return self._read_panel(day)

    def _wait_closed(self, day):
        matched = 0
        for _ in range(10):
            self._check_day(day)
            image = self.capture()
            if self.view.find_panel(image) is None and self.view.find_entry(image) is not None:
                matched += 1
                if matched == 2:
                    return
            else:
                matched = 0
            self.sleep(0.25)
        raise SoulSelectionError('御魂面板未确认关闭或提交未生效')

    def select(self, day, target):
        target = frozenset(target)
        if len(target) != 4 or not target <= set(range(1, 16)):
            raise ValueError('Exactly four distinct soul slots are required')
        panel, selected = self._open_panel(day)
        # Remove old choices first, so the game's four-item cap cannot block an add.
        for slot in sorted(selected - target):
            self._check_day(day)
            self.click(panel.cell_roi(slot), f'daily_souls_remove_{slot}')
            panel, selected = self._read_panel(day, selected - {slot})
        for slot in sorted(target - selected):
            self._check_day(day)
            self.click(panel.cell_roi(slot), f'daily_souls_add_{slot}')
            panel, selected = self._read_panel(day, selected | {slot})
        panel, selected = self._read_panel(day, target)
        self._check_day(day)
        self.click(panel.submit_roi, 'daily_souls_submit')
        self._wait_closed(day)

        # Reopen to distinguish a saved submission from merely closing the popup.
        panel, selected = self._open_panel(day)
        if selected != target:
            raise SoulSelectionError('重新打开后的御魂选择与提交结果不一致')
        self._check_day(day)
        self.click(panel.close_roi, 'daily_souls_close')
        self._wait_closed(day)
        self._check_day(day)
        return target
