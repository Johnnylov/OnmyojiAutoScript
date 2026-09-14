"""Bounded, resource-verified coloring transaction for 百鬼夜行图."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
import time

from tasks.ActivityShikigami.coloring_view import ColoringView


class ColoringError(RuntimeError):
    """The observed interface does not safely support the next action."""


@dataclass(frozen=True)
class ColoringResult:
    status: str
    submissions: int = 0
    global_progress: float | None = None
    reason: str = ''


def parse_progress(text):
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r'\s*(\d{1,3}(?:\.\d+)?)\s*[%％]\s*', text)
    if match is None:
        return None
    value = float(match[1])
    return value if 0 <= value <= 100 else None


def parse_amount(text):
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(万?)\s*', text)
    if match is None:
        return None
    try:
        value = Decimal(match[1]) * (10000 if match[2] else 1)
    except InvalidOperation:
        return None
    return int(value) if value == value.to_integral_value() else None


class DailyColorer:
    MAX_FRAMES = 12
    MAX_SUBMISSIONS = 1000
    MAX_RUNTIME = 30 * 60

    def __init__(self, capture, click_roi, read_text, view=None, sleep=time.sleep, clock=time.monotonic):
        self.capture = capture
        self.click = click_roi
        self.read_text = read_text
        self.view = view or ColoringView()
        self.sleep = sleep
        self.clock = clock

    def _wait_page(self, panel=None):
        for _ in range(self.MAX_FRAMES):
            image = self.capture()
            page = self.view.find_page(image)
            if page is not None and (panel is None or page.panel == panel):
                return image, page
            self.sleep(.25)
        raise ColoringError('未确认百鬼夜行图界面，停止上色，未点击未知弹窗')

    def _read(self, image, page, quantity=False):
        progress = parse_progress(self.read_text(image, page.global_progress_roi, 'coloring_global_progress'))
        currency = self._counter(image, page.currency_roi, 'coloring_currency')
        if progress is None or currency is None:
            return None
        amount = None
        if quantity:
            if not page.panel:
                return None
            amount = self._counter(image, page.amount_roi, 'coloring_selected_amount', quantity=True)
            if amount is None or amount > currency:
                return None
        return progress, currency, amount

    def _counter(self, image, roi, name, quantity=False):
        prepared = self.view.prepare_counter(image, roi, quantity)
        if prepared is None:
            return None
        h, w = prepared.shape[:2]
        return parse_amount(self.read_text(prepared, (0, 0, w, h), name))

    def _stable(self, quantity=False):
        previous = None
        for _ in range(self.MAX_FRAMES):
            image = self.capture()
            page = self.view.find_page(image)
            values = None if page is None else self._read(image, page, quantity)
            if values is not None and values == previous:
                return page, values
            previous = values
            self.sleep(.25)
        raise ColoringError('未能连续确认全服进度、灵彩或上色数量，停止上色')

    def _acknowledge(self, before, before_progress):
        previous = None
        for _ in range(self.MAX_FRAMES):
            image = self.capture()
            page = self.view.find_page(image)
            values = None if page is None else self._read(image, page)
            if values is not None and values == previous:
                # Global progress can rise due to other players. Only a lower
                # resource count proves our submission succeeded and permits
                # another submit; 100% may safely end the workflow regardless.
                if values[0] == 100 or (values[0] >= before_progress and values[1] < before):
                    return page, values
            previous = values
            self.sleep(.25)
        return None

    def _stable_character(self, different_from=None):
        previous = None
        for _ in range(self.MAX_FRAMES):
            image = self.capture()
            page = self.view.find_page(image)
            values = None
            if page is not None and page.panel:
                name = self.read_text(image, page.character_name_roi, 'coloring_character_name')
                local = parse_progress(self.read_text(image, page.character_progress_roi,
                                                     'coloring_character_progress'))
                if (isinstance(name, str) and re.fullmatch(r'[\u3400-\u9fff·]{1,16}', name.strip())
                        and local is not None):
                    values = name.strip(), local
            if values is not None and values == previous and values[0] != different_from:
                return page, values
            previous = values
            self.sleep(.25)
        return None

    def run(self):
        started = self.clock()
        image = self.capture()
        page = self.view.find_page(image)
        if page is None:
            entry = self.view.find_map_entry(image)
            if entry is None:
                return ColoringResult('unavailable', reason='未识别到百鬼夜行图入口')
            self.click(entry, 'coloring_open')
            _, page = self._wait_page()
        submitted = 0
        progress = None
        finished_characters = set()
        while submitted < self.MAX_SUBMISSIONS and self.clock() - started < self.MAX_RUNTIME:
            page, (progress, currency, _) = self._stable()
            if progress == 100:
                return ColoringResult('complete', submitted, progress)
            if currency == 0:
                return ColoringResult('no_currency', submitted, progress)
            if not page.panel:
                self.click(page.start_roi, 'coloring_start')
                self._wait_page(panel=True)
            page, (progress, currency, _) = self._stable()
            if progress == 100:
                return ColoringResult('complete', submitted, progress)
            if currency == 0:
                return ColoringResult('no_currency', submitted, progress)
            if not page.panel:
                raise ColoringError('上色面板已关闭，停止操作')
            # Device protects both one repeated control and two alternating
            # controls. Keep each verified transaction distinct without clearing
            # click history. This phase changes only after consumed resources or
            # a completed character followed by a verified changed name.
            phase = f'{submitted}_{len(finished_characters)}'
            self.click(page.max_roi, f'coloring_max_{phase}')
            page, (progress, currency, amount) = self._stable(quantity=True)
            if progress == 100:
                return ColoringResult('complete', submitted, progress)
            if currency == 0:
                return ColoringResult('no_currency', submitted, progress)
            if amount == 0:
                character = self._stable_character()
                if character is not None:
                    character_page, (name, local_progress) = character
                    if local_progress == 100 and name not in finished_characters and character_page.next_available:
                        self.click(character_page.next_roi, f'coloring_next_character_{phase}')
                        if self._stable_character(different_from=name) is not None:
                            finished_characters.add(name)
                            continue
                    if local_progress == 100:
                        return ColoringResult('no_progress', submitted, progress, '可见式神已完成或切换无变化')
                return ColoringResult('no_progress', submitted, progress, '选择数量为 0')
            self.click(page.submit_roi, f'coloring_submit_{phase}')
            acknowledged = self._acknowledge(currency, progress)
            if acknowledged is None:
                return ColoringResult('no_progress', submitted, progress, '未确认灵彩消耗，不重复提交')
            submitted += 1
            _, (progress, currency, _) = acknowledged
            if progress == 100:
                return ColoringResult('complete', submitted, progress)
            if currency == 0:
                return ColoringResult('no_currency', submitted, progress)
        return ColoringResult('no_progress', submitted, progress, '已达到本次上色时间或次数上限')

    def leave(self):
        """Return to the activity map only through recognized collapse/back controls."""
        image = self.capture()
        page = self.view.find_page(image)
        if page is None:
            return self.view.find_map_entry(image) is not None
        if page.panel:
            self.click(page.collapse_roi, 'coloring_collapse')
            _, page = self._wait_page(panel=False)
        self.click(page.back_roi, 'coloring_back')
        for _ in range(self.MAX_FRAMES):
            image = self.capture()
            if self.view.find_map_entry(image) is not None:
                return True
            self.sleep(.25)
        return False
