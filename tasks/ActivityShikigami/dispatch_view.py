"""Read dispatch controls using local visual anchors, without device access.

All public images are RGB uint8 arrays. The injected read_text(image, roi)
reads a single line; ambiguous counters never produce an actionable setup.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re

import cv2
import numpy as np


ASSETS = Path(__file__).with_name('dispatch_assets')
# Exclude each card's bottom selection border from its name template.
NAMES = ((102, 594, 39, 20), (276, 594, 40, 20), (426, 594, 93, 20),
         (614, 594, 70, 20), (789, 594, 70, 20), (957, 594, 87, 20))
CARD_X = (47, 223, 399, 575, 751, 927)


@dataclass(frozen=True)
class DispatchObservation:
    kind: str = 'unknown'
    empty: tuple = ()
    locked: int = 0
    running: int = 0
    uncertain: int = 0
    available: tuple = ()  # (zero-based portrait index, click ROI)
    selected: int | None = None
    current: int | None = None
    maximum: int | None = None
    plus_roi: tuple | None = None
    minus_roi: tuple | None = None
    submit_roi: tuple | None = None
    close_roi: tuple | None = None
    dismiss_roi: tuple | None = None
    return_id: str | None = None

    @property
    def all_slots_known(self):
        return (self.kind == 'map' and not self.uncertain
                and len(self.empty) + self.locked + self.running == 4)


@dataclass(frozen=True)
class Match:
    x: float
    y: float
    scale: float
    width: float
    height: float
    score: float

    @property
    def center(self):
        return self.x + self.width / 2, self.y + self.height / 2

    def roi(self, dx=0, dy=0, width=None, height=None):
        return (round(self.x + dx * self.scale), round(self.y + dy * self.scale),
                max(1, round(self.width if width is None else width * self.scale)),
                max(1, round(self.height if height is None else height * self.scale)))


@lru_cache(maxsize=32)
def _template(name):
    path = (ASSETS.parent / 'as/as_map_goto_battle.png'
            if name == 'map_menu' else ASSETS / (name + '.png'))
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(path)
    return image


def parse_duration(text):
    if not isinstance(text, str):
        return None
    text = re.sub(r'\s+', '', text)
    match = re.fullmatch(r'(?:放置时间)?(\d{1,2})/(\d{1,2})(?:时|小时)?', text)
    if match is None:
        return None
    current, maximum = map(int, match.groups())
    return (current, maximum) if 0 <= current <= maximum <= 24 else None


def _countdown(text):
    if not isinstance(text, str):
        return False
    match = re.search(r'(?<!\d)(\d{1,2}):([0-5]\d):([0-5]\d)(?!\d)',
                      re.sub(r'\s+', '', text).replace('：', ':'))
    return bool(match and int(match[1]) <= 24)


def _inside(image, roi):
    x, y, w, h = roi
    return x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= image.shape[1] and y + h <= image.shape[0]


def _crop(image, roi):
    x, y, w, h = roi
    return image[y:y+h, x:x+w]


def _best(source, template):
    if source.size == 0 or any(a < b for a, b in zip(source.shape[:2], template.shape[:2])):
        return 0., (0, 0)
    _, score, _, location = cv2.minMaxLoc(cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED))
    return score, location


def _scaled(image, scale):
    return cv2.resize(image, None, fx=scale, fy=scale,
                      interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)


def _lit(gray, match, name):
    """Correlation alone also matches controls beneath a dim modal overlay."""
    roi = match.roi()
    if not _inside(gray, roi):
        return False
    source = _crop(gray, roi)
    reference = _scaled(_template(name), match.scale)
    return (np.percentile(source, 90) >= np.percentile(reference, 90) * .85
            and float(source.std()) >= float(reference.std()) * .65)


def _matches(gray, name, threshold=.82, limit=8):
    """Search scale and translation, then suppress duplicate nearby matches."""
    reduction = min(1., 1050. / max(gray.shape))
    source = _scaled(gray, reduction) if reduction < 1 else gray
    template = _template(name)
    candidates = []
    scales = sorted(set(np.arange(.55, 2.61, .05).round(3)) | {1., 1.25, 1.5, 2.})
    for scale in scales:
        resized = _scaled(template, scale * reduction)
        if any(a < b for a, b in zip(source.shape, resized.shape)):
            continue
        scores = cv2.matchTemplate(source, resized, cv2.TM_CCOEFF_NORMED)
        for _ in range(limit):
            _, score, _, (x, y) = cv2.minMaxLoc(scores)
            if score < threshold:
                break
            height, width = resized.shape
            candidates.append(Match(x / reduction, y / reduction, scale,
                                    width / reduction, height / reduction, score))
            scores[max(0, y-height//2):y+height//2+1,
                   max(0, x-width//2):x+width//2+1] = -1
    unique = []
    for candidate in sorted(candidates, key=lambda m: m.score, reverse=True):
        if not _lit(gray, candidate, name):
            continue
        if all(np.linalg.norm(np.subtract(candidate.center, other.center))
               > max(candidate.width, candidate.height, other.width, other.height) * .65
               for other in unique):
            unique.append(candidate)
    return unique[:limit]


def _near(gray, name, reference, transform, threshold=.8, margin=5):
    origin_x, origin_y, scale = transform
    x, y, w, h = reference
    margin = max(3, round(margin * scale))
    roi = (round(origin_x + x * scale) - margin, round(origin_y + y * scale) - margin,
           round(w * scale) + 2 * margin, round(h * scale) + 2 * margin)
    if not _inside(gray, roi):
        return None
    template = _scaled(_template(name), scale)
    score, (mx, my) = _best(_crop(gray, roi), template)
    if score < threshold:
        return None
    match = Match(roi[0] + mx, roi[1] + my, scale, template.shape[1], template.shape[0], score)
    return match if _lit(gray, match, name) else None


class DispatchView:
    def __init__(self, read_text=None):
        self.read_text = read_text or (lambda image, roi: '')

    def _read(self, image, roi):
        if not _inside(image, roi):
            return ''
        return self.read_text(image, roi)

    def _success(self, gray):
        # Character art, names, duration and success text vary or fade. Only
        # the shared title and distant dismissal prompt identify this overlay;
        # neither a character template nor a return sentence is required.
        for title in _matches(gray, 'success_title', .82, limit=2):
            transform = (title.x - 329 * title.scale,
                         title.y - 62 * title.scale, title.scale)
            prompt = _near(gray, 'success_dismiss', (365, 442, 112, 19), transform, .8)
            if prompt is None:
                continue
            # Use the matched dismissal instruction itself. Character artwork
            # can cover the blank space beside another character's portrait.
            dismiss = prompt.roi(10, 3, 92, 13)
            if _inside(gray, dismiss):
                return DispatchObservation(kind='success', dismiss_roi=dismiss)
        return None

    def _level_up(self, gray):
        # This acknowledgement can follow dispatch returns before the map.
        # Require its full, distinct title and the distant dismissal prompt;
        # level digits and the changing unlock message are not anchors.
        for title in _matches(gray, 'level_up_title', .84, limit=2):
            for scale in (title.scale, title.scale - .025, title.scale + .025):
                transform = (title.x - 311 * scale, title.y - 125 * scale, scale)
                prompt = _near(gray, 'success_dismiss', (358, 436, 112, 19), transform, .8)
                if prompt is not None:
                    dismiss = prompt.roi(10, 3, 92, 13)
                    if _inside(gray, dismiss):
                        return DispatchObservation(kind='level_up', dismiss_roi=dismiss)
        return None

    def _result_overlay(self, image, gray, title_name, kind):
        # Returned/interrupted dispatches share a dismissal prompt, but each
        # requires its own title. Character art and reward amounts vary.
        title_top = 68 if kind == 'interrupted' else 62
        for title in _matches(gray, title_name, .82, limit=2):
            prompt = None
            # Refine the coarse title scale locally: 1280/840 is 1.524,
            # and the narrow prompt does not tolerate rounding it to 1.5.
            for scale in (title.scale, title.scale - .025, title.scale + .025):
                transform = (title.x - 329 * scale, title.y - title_top * scale, scale)
                prompt = _near(gray, 'success_dismiss', (365, 442, 112, 19), transform, .8)
                if prompt is not None:
                    break
            if prompt is None:
                continue
            dismiss = prompt.roi(10, 3, 92, 13)
            if not _inside(gray, dismiss):
                continue
            if kind == 'interrupted':
                # Resource shortage can recall several characters at once.
                # This acknowledgement is never a request to recall anyone.
                return DispatchObservation(kind=kind, dismiss_roi=dismiss)
            ox, oy, scale = transform
            line = (round(ox + 320 * scale), round(oy + 347 * scale),
                    round(201 * scale), round(24 * scale))
            # This optional identity proves progress between consecutive
            # return overlays; it is never required to recognize/close one.
            value = self._read(image, line)
            text = re.sub(r'\s+', '', value) if isinstance(value, str) else ''
            # The surrounding gold flourish is sometimes recognized as
            # punctuation. Remove only that boundary noise, never letters.
            text = text.strip('·。，、“”‘’—-~～')
            returned = re.fullmatch(r'([\u3400-\u9fff]{2,5})已回归', text)
            return DispatchObservation(kind='returned', dismiss_roi=dismiss,
                                       return_id=returned[1] if returned else None)
        return None

    def _drawer(self, image, gray):
        for arrow in _matches(gray, 'chevron', .82, limit=3):
            transform = (arrow.x - 545 * arrow.scale, arrow.y - 436 * arrow.scale, arrow.scale)
            ox, oy, scale = transform

            def roi(x, y, w, h):
                return (round(ox + x * scale), round(oy + y * scale),
                        max(1, round(w * scale)), max(1, round(h * scale)))

            # Busy characters are removed from the drawer, and the remaining
            # cards move left. Match a character inside each column rather
            # than assuming that identity always occupies its original slot.
            labels, ambiguous = [], False
            for column, card_x in enumerate(CARD_X):
                candidates = []
                for identity, (name_x, name_y, width, height) in enumerate(NAMES):
                    area = (card_x + name_x - CARD_X[identity], name_y, width, height)
                    match = _near(gray, 'name_' + str(identity), area, transform, .8)
                    if match is not None:
                        candidates.append((identity, match, column))
                if len(candidates) == 1:
                    labels.extend(candidates)
                elif candidates:
                    ambiguous = True
            if len(labels) < 2:
                continue
            close = arrow.roi(2, 2, 23, 20)
            if ambiguous or len({identity for identity, _, _ in labels}) != len(labels):
                return DispatchObservation(close_roi=close)
            available, selected = [], []
            for identity, match, column in labels:
                card_x = CARD_X[column]
                body = roi(card_x + 30, 480, 90, 105)
                click = roi(card_x + 48, 512, 50, 50)
                if not _inside(image, body) or not _inside(image, click):
                    continue
                hsv = cv2.cvtColor(_crop(image, body), cv2.COLOR_RGB2HSV)
                # Busy/disabled portraits are desaturated. A name alone is
                # insufficient to choose a card that may no longer be usable.
                colored = (hsv[:, :, 1] > 65) & (hsv[:, :, 2] < 225)
                if np.mean(colored) < .06:
                    continue
                available.append((identity, click))
                if _near(gray, 'selected', (card_x-4, 468, 26, 27), transform, .91):
                    selected.append(identity)
            if len(selected) > 1:
                return DispatchObservation(close_roi=close)
            # The post-submit detail pane has a recall button in the former
            # submit location. Expose only its drawer-collapse control.
            detail_transform = (ox, oy, scale * 1111 / 840)
            remaining = _near(gray, 'remaining', (650, 247, 74, 18), detail_transform, .82)
            recall = _near(gray, 'recall', (671, 310, 38, 19), detail_transform, .82)
            if remaining is not None and recall is not None:
                return DispatchObservation(kind='running_details', close_roi=close)
            rewards = _near(gray, 'rewards', (866, 140, 90, 24), transform, .82)
            submit = _near(gray, 'submit', (825, 400, 169, 45), transform, .82)
            duration = _near(gray, 'duration', (838, 271, 80, 25), transform, .8)
            plus = _near(gray, 'plus', (998, 302, 37, 37), transform, .82)
            minus = _near(gray, 'minus', (785, 303, 35, 36), transform, .82)
            if any((rewards, submit, duration, plus, minus)):
                if not all((rewards, submit, duration, plus, minus)) or len(selected) != 1:
                    return DispatchObservation(close_roi=close)
                # Exclude the decorative diamond following the duration; OCR
                # otherwise reads it as an extra character at runtime scale.
                counter = parse_duration(self._read(image, roi(833, 269, 148, 29)))
                return DispatchObservation(kind='setup', available=tuple(available),
                    selected=selected[0], current=counter[0] if counter else None,
                    maximum=counter[1] if counter else None,
                    plus_roi=plus.roi(9, 9, 18, 18), minus_roi=minus.roi(8, 8, 18, 18),
                    submit_roi=submit.roi(32, 11, 100, 24), close_roi=close)
            return DispatchObservation(kind='portraits', available=tuple(available), close_roi=close)
        return None

    def observe(self, image):
        if (not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3
                or image.shape[2] != 3 or min(image.shape[:2]) < 100):
            return DispatchObservation()
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        level_up = self._level_up(gray)
        if level_up is not None:
            return level_up
        interrupted = self._result_overlay(image, gray, 'interrupt_title', 'interrupted')
        if interrupted is not None:
            return interrupted
        returned = self._result_overlay(image, gray, 'return_title', 'returned')
        if returned is not None:
            return returned
        success = self._success(gray)
        if success is not None:
            return success
        drawer = self._drawer(image, gray)
        if drawer is not None and drawer.kind == 'running_details':
            return drawer
        if not _matches(gray, 'map_name', .8, limit=1):
            return DispatchObservation()
        if drawer is not None:
            return drawer
        # The boss name remains visible behind an open character drawer. A
        # missed collapse arrow must not turn that drawer into a verified map.
        # Its fixed upper-left quest menu is only present on the full map.
        menu_region = gray[:round(gray.shape[0] * .35), :round(gray.shape[1] * .35)]
        if not _matches(menu_region, 'map_menu', .82, limit=1):
            return DispatchObservation()
        empty = _matches(gray, 'empty', .8, limit=5)
        locked = _matches(gray, 'locked', .8, limit=5)
        inspected = _matches(gray, 'inspect', .78, limit=5)
        running = 0
        for button in inspected:
            # The magnifier must be paired with a live countdown to count as
            # an occupied slot. Reward-ready or unrelated icons are ambiguous.
            # Keep the numeric countdown, excluding its leading clock icon.
            if _countdown(self._read(image, button.roi(-65, 6, 54, 17))):
                running += 1
        # No partial map is allowed to announce daily completion.
        if len(empty) + len(locked) + len(inspected) > 4:
            return DispatchObservation()
        # A character or battle effect can hide both a slot's magnifier and
        # countdown. Count missing markers as unknown slots as well as icons
        # with unreadable timers; neither case proves empty or running.
        uncertain = 4 - len(empty) - len(locked) - running
        return DispatchObservation(kind='map', empty=tuple(m.roi(3, 3, 35, 45) for m in empty),
                                   locked=len(locked), running=running, uncertain=uncertain)
