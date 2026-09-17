"""Read the supplied True Orochi screens without assuming the crop's origin.

All public regions use x/y/width/height in the actual screenshot. Templates are
matched at multiple scales; controls are relative to a verified panel anchor.
"""
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


ASSETS = Path(__file__).with_name('team_images')


def parse_entries(text):
    value = str(text).strip().translate(str.maketrans('１２', '12'))
    return int(value) if value in ('1', '2') else None


def parse_rewards(text):
    value = re.sub(r'\s', '', str(text)).translate(str.maketrans('０１２／：', '012/:'))
    ratio = re.search(r'(?<!\d)([012])/2(?!\d)', value)
    if ratio:
        return int(ratio[1])
    remaining = re.search(r'剩余奖励次数[:：]?([012])(?!\d)', value)
    return int(remaining[1]) if remaining else None


@lru_cache(maxsize=16)
def template(name):
    image = cv2.imread(str(ASSETS / f'{name}.png'), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(ASSETS / f'{name}.png')
    return image


@dataclass(frozen=True)
class Panel:
    x: float
    y: float
    scale: float

    def roi(self, x, y, width, height):
        return (round(self.x + x * self.scale), round(self.y + y * self.scale),
                max(1, round(width * self.scale)), max(1, round(height * self.scale)))


class TrueOrochiView:
    ANCHORS = {'entry': (10, 53), 'detail': (184, 331),
               'confirm': (375, 201), 'private': (207, 21), 'room': (78, 19),
               'prepare': (540, 6)}

    def __init__(self, image):
        self.image = image
        self.gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        self.cache = {}

    def _find(self, name, verify=None):
        key = (name, verify)
        if key in self.cache:
            return self.cache[key]
        source = template(name)
        # Work at a bounded resolution, retaining enough pixels for label text.
        reduction = min(1., 960 / self.gray.shape[1])
        search = cv2.resize(self.gray, None, fx=reduction, fy=reduction) if reduction < 1 else self.gray
        best = None
        scales = sorted(set(np.arange(.65, 1.81, .05).round(3)) |
                        {1., 1280 / 1145, 720 / 645, 1.125, 1.25, 1.5})
        for scale in scales:
            scaled = cv2.resize(source, None, fx=scale * reduction, fy=scale * reduction)
            h, w = scaled.shape
            if h > search.shape[0] or w > search.shape[1]:
                continue
            _, score, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(search, scaled, cv2.TM_CCOEFF_NORMED))
            if score < .79 or (best and score <= best[0]):
                continue
            # Reject a matching label dimmed by a modal on top of its page.
            if abs(float(search[y:y+h, x:x+w].mean()) - float(scaled.mean())) > 32:
                continue
            ax, ay = self.ANCHORS[name]
            panel = Panel(x / reduction - ax * scale, y / reduction - ay * scale, scale)
            # A small header can match at neighboring scales. Validate the
            # distant control before choosing the best candidate, not after.
            if verify and not self._at(panel, *verify):
                continue
            best = (score, panel)
        self.cache[key] = best[1] if best else None
        return self.cache[key]

    def crop(self, roi):
        x, y, w, h = roi
        return self.image[max(0, y):max(0, y+h), max(0, x):max(0, x+w)]

    def _at(self, panel, name, region, threshold=.76):
        x, y, w, h = panel.roi(*region)
        if x < 0 or y < 0 or x+w > self.gray.shape[1] or y+h > self.gray.shape[0]:
            return False
        margin = max(3, round(7 * panel.scale))
        left, top = max(0, x-margin), max(0, y-margin)
        patch = self.gray[top:y+h+margin, left:x+w+margin]
        expected = cv2.resize(template(name), (w, h))
        _, score, _, (mx, my) = cv2.minMaxLoc(cv2.matchTemplate(patch, expected, cv2.TM_CCOEFF_NORMED))
        return (abs(float(patch[my:my+h, mx:mx+w].mean()) - float(expected.mean())) < 32 and
                score >= threshold)

    def entry(self):
        return self._find('entry')

    def entry_count(self, read_text):
        panel = self.entry()
        if panel is None:
            return 0
        badge = self.crop(panel.roi(65, 0, 31, 27))
        if badge.size == 0:
            return None
        red = (badge[:, :, 0].astype(int) > badge[:, :, 1].astype(int) + 45) & (badge[:, :, 0] > 130)
        # A visible icon without a red stack badge represents one entry.
        if np.count_nonzero(red) < 8:
            return 1
        return parse_entries(read_text(badge))

    def detail(self):
        panel = self._find('detail')
        if panel and self._at(panel, 'detail_fire', (830, 435, 57, 35)):
            return panel
        return None

    def confirm(self):
        panel = self._find('confirm')
        if panel and self._at(panel, 'confirm_button', (610, 329, 95, 34)):
            return panel
        return None

    def private(self):
        panel = self._find('private')
        if panel and self._at(panel, 'create_button', (270, 321, 68, 37)):
            return panel
        return None

    def private_selected(self, panel):
        return self._at(panel, 'private_selected', (47, 242, 32, 32), .8)

    def room(self):
        panel = self._find('room')
        if panel and self._at(panel, 'room_target', (840, 580, 116, 27)):
            return panel
        return None

    def empty_slot(self, panel):
        return self._at(panel, 'room_plus', (550, 196, 55, 62), .7)

    def prepare(self):
        return self._find('prepare', verify=('prepare_ready', (1011, 518, 98, 52)))

    def rewards(self, read_text):
        if panel := self.confirm():
            return parse_rewards(read_text(self.crop(panel.roi(420, 252, 275, 27))))
        if panel := self.detail():
            return parse_rewards(read_text(self.crop(panel.roi(182, 329, 222, 27))))
        return None
