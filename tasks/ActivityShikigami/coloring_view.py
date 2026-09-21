"""Read-only visual anchors for 百鬼夜行图; coordinates fit both supplied layouts."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


ASSETS = Path(__file__).with_name('coloring_assets')
ANCHORS = {
    'title': (119, 12), 'global_label': (111, 504), 'back': (17, 12),
    'start': (848, 489), 'max': (859, 449), 'submit': (761, 492),
    'collapse': (637, 234),
    'next': (903, 308),
    # The first-entry illustrated story uses a 1280 x 720 reference layout.
    'intro_logo': (89, 162), 'intro_speaker': (601, 564),
    'intro_skip': (1159, 37), 'intro_confirm': (707, 442),
    'completion_caption': (221, 631),
    # Reward layout uses the 1280 x 720 runtime screenshots.
    'reward_title': (481, 234), 'reward_frame': (312, 421),
}


@lru_cache(maxsize=20)
def _template(name):
    shared = {'intro_skip': 'as_skip_button', 'intro_confirm': 'as_confirm_skip'}
    path = (ASSETS.parent / 'as' / f'{shared[name]}.png' if name in shared
            else ASSETS / f'{name}.png')
    if name == 'reward_title':
        path = ASSETS.parent.parent / 'GlobalGame/ui/ui_ui_reward.png'
    result = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if result is None:
        raise FileNotFoundError(path)
    return result


def _gray(image):
    if (not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3
            or image.dtype != np.uint8 or min(image.shape[:2]) < 32):
        return None
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def _scaled(image, scale):
    return cv2.resize(image, None, fx=scale, fy=scale,
                      interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)


def _best(image, template):
    if image.shape[0] < template.shape[0] or image.shape[1] < template.shape[1]:
        return None
    _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED))
    return score, point


def _candidates(gray, name, threshold=.8):
    reduction = min(1., 1000 / max(gray.shape))
    search = _scaled(gray, reduction) if reduction < 1 else gray
    choices = []
    scales = set(np.arange(.55, 2.61, .05).round(3)) | {1., 1280 / 956}
    if name == 'completion_caption':
        # A long sentence needs the exact supported downscales: rounding
        # 840/1280 to .65 shifts its last glyphs by several pixels.
        scales.update((840 / 1280, 956 / 1280))
    for scale in sorted(scales):
        match = _best(search, _scaled(_template(name), scale * reduction))
        if match is not None and match[0] >= threshold:
            score, (x, y) = match
            choices.append((score, x / reduction, y / reduction, scale))
    return sorted(choices, reverse=True)


@dataclass(frozen=True)
class PaintingPage:
    x: float
    y: float
    scale: float
    panel: bool = False
    next_available: bool = False

    def roi(self, x, y, width, height):
        return (round(self.x + x * self.scale), round(self.y + y * self.scale),
                max(1, round(width * self.scale)), max(1, round(height * self.scale)))

    @property
    def global_progress_roi(self):
        return self.roi(110, 472, 78, 31)

    @property
    def currency_roi(self):
        # The flower counter moves left when the character panel opens.
        # Preserve the full numeric band for multi-digit stock, while excluding
        # the flower icon on the left and the purchase-plus control on the right.
        return self.roi(507 if self.panel else 840, 20, 66 if self.panel else 70, 18)

    @property
    def start_roi(self):
        return self.roi(851, 491, 80, 19)

    @property
    def amount_roi(self):
        return self.roi(760, 452, 58, 29)

    @property
    def max_roi(self):
        return self.roi(866, 456, 18, 18)

    @property
    def submit_roi(self):
        return self.roi(785, 498, 52, 22)

    @property
    def back_roi(self):
        return self.roi(21, 16, 25, 26)

    @property
    def collapse_roi(self):
        return self.roi(640, 240, 15, 23)

    @property
    def character_name_roi(self):
        return self.roi(689, 25, 107, 22)

    @property
    def character_progress_roi(self):
        # Keep room to the left for 100.00%, but stop before the portrait/rank
        # icon on the right: its edge otherwise becomes a spurious OCR digit.
        return self.roi(808, 25, 60, 22)

    @property
    def next_roi(self):
        return self.roi(911, 317, 17, 24)


@dataclass(frozen=True)
class PaintingIntro:
    kind: str
    action_roi: tuple


@dataclass(frozen=True)
class PaintingReward:
    dismiss_roi: tuple


class ColoringView:
    def __init__(self):
        self._last = None

    def _anchor(self, gray, name, page, threshold=.79, undimmed=False, search_margin=4):
        tx, ty = ANCHORS[name]
        x, y, _, _ = page.roi(tx, ty, 1, 1)
        template = _scaled(_template(name), page.scale)
        margin = max(3, round(search_margin * page.scale))
        left, top = max(0, x - margin), max(0, y - margin)
        right = min(gray.shape[1], x + template.shape[1] + margin)
        bottom = min(gray.shape[0], y + template.shape[0] + margin)
        match = _best(gray[top:bottom, left:right], template)
        if match is None or match[0] < threshold:
            return None
        _, (mx, my) = match
        if undimmed:
            patch = gray[top+my:top+my+template.shape[0], left+mx:left+mx+template.shape[1]]
            mask = template >= 175
            if name in ('intro_speaker', 'reward_frame'):
                # Orange lettering/gold trim have grayscale levels below the
                # white-label threshold used by the painting controls.
                mask = template >= np.percentile(template, 90)
            if not mask.any() or patch[mask].mean() < template[mask].mean() * .78:
                return None
        return left + mx, top + my

    def _layout(self, gray, page):
        if not all(self._anchor(gray, name, page, undimmed=True)
                   for name in ('title', 'global_label', 'back')):
            return None
        if all(self._anchor(gray, name, page) for name in ('max', 'submit', 'collapse')):
            return PaintingPage(page.x, page.y, page.scale, panel=True,
                                next_available=self._anchor(gray, 'next', page, threshold=.72) is not None)
        if self._anchor(gray, 'start', page):
            return PaintingPage(page.x, page.y, page.scale, panel=False)
        return None

    def find_page(self, image):
        gray = _gray(image)
        if gray is None:
            return None
        if self._last is not None:
            page = self._layout(gray, self._last)
            if page is not None:
                return page
        delta = np.array(ANCHORS['global_label']) - ANCHORS['title']
        for _, x, y, scale in _candidates(gray, 'title')[:8]:
            tentative = PaintingPage(x - 119 * scale, y - 12 * scale, scale)
            point = self._anchor(gray, 'global_label', tentative, threshold=.72, search_margin=16)
            if point is None:
                continue
            observed = np.array(point) - (x, y)
            fitted = float(np.dot(observed, delta) / np.dot(delta, delta))
            if abs(fitted - scale) > .04 or np.linalg.norm(observed - delta * fitted) > 5 * scale:
                continue
            candidate = PaintingPage(x - 119 * fitted, y - 12 * fitted, fitted)
            page = self._layout(gray, candidate)
            if page is not None:
                self._last = page
                return page
        return None

    def find_map_entry(self, image):
        gray = _gray(image)
        if gray is None:
            return None
        matches = _candidates(gray, 'map_entry', threshold=.85)
        if not matches:
            return None
        _, x, y, scale = matches[0]
        return (round(x + 21 * scale), round(y + 14 * scale),
                max(1, round(49 * scale)), max(1, round(43 * scale)))

    def find_intro(self, image):
        """Recognize the opening guide or the completed painting's story.

        The completed painting has no opening logo. Its exact completion
        sentence, speaker and distant action identify that second layout.
        Neither a generic story skip nor the speaker alone authorizes a click.
        A dimmed story exposes only its verified Confirm Skip button.
        """
        gray = _gray(image)
        if gray is None:
            return None
        # Coarse downscaling can weaken the narrow vertical logo; every
        # candidate is rechecked at full resolution with both distant anchors.
        for identity, threshold in (('intro_logo', .72), ('completion_caption', .8)):
            ax, ay = ANCHORS[identity]
            for _, x, y, scale in _candidates(gray, identity, threshold=threshold)[:8]:
                page = PaintingPage(x - ax * scale, y - ay * scale, scale)
                speaker = self._anchor(gray, 'intro_speaker', page, threshold=.8, search_margin=16)
                if speaker is None:
                    continue
                delta = np.array(ANCHORS['intro_speaker']) - ANCHORS[identity]
                observed = np.array(speaker) - (x, y)
                fitted = float(np.dot(observed, delta) / np.dot(delta, delta))
                if abs(fitted - scale) > .035 or np.linalg.norm(observed - delta * fitted) > 5 * scale:
                    continue
                scale = fitted
                page = PaintingPage(x - ax * scale, y - ay * scale, scale)
                if not all(self._anchor(gray, name, page, threshold=.82)
                           for name in (identity, 'intro_speaker')):
                    continue
                confirm = self._anchor(gray, 'intro_confirm', page, threshold=.86,
                                       undimmed=True, search_margin=12)
                if confirm is not None:
                    cx, cy = confirm
                    return PaintingIntro('confirm', (round(cx + 20 * scale), round(cy + 7 * scale),
                                                       max(1, round(95 * scale)), max(1, round(24 * scale))))
                if not all(self._anchor(gray, name, page, threshold=.82, undimmed=True)
                           for name in (identity, 'intro_speaker', 'intro_skip')):
                    continue
                sx, sy = self._anchor(gray, 'intro_skip', page, threshold=.82, undimmed=True)
                return PaintingIntro('skip', (round(sx + 4 * scale), round(sy + 2 * scale),
                                              max(1, round(43 * scale)), max(1, round(18 * scale))))
        return None

    def find_reward(self, image):
        """Recognize the reward overlay only over the verified painting layout.

        Currency can already be consumed while the reward obscures the page.
        Expose only the same empty left margin as the shared reward handler;
        item icons and painting controls remain outside the click target.
        """
        gray = _gray(image)
        if gray is None:
            return None
        delta = np.array(ANCHORS['reward_frame']) - ANCHORS['reward_title']
        for _, x, y, scale in _candidates(gray, 'reward_title', threshold=.8)[:8]:
            page = PaintingPage(x - 481 * scale, y - 234 * scale, scale)
            frame = self._anchor(gray, 'reward_frame', page, threshold=.8, search_margin=12)
            if frame is None:
                continue
            observed = np.array(frame) - (x, y)
            fitted = float(np.dot(observed, delta) / np.dot(delta, delta))
            if abs(fitted - scale) > .04 or np.linalg.norm(observed - delta * fitted) > 5 * scale:
                continue
            page = PaintingPage(x - 481 * fitted, y - 234 * fitted, fitted)
            if not all(self._anchor(gray, name, page, threshold=.8, undimmed=True)
                       for name in ('reward_title', 'reward_frame')):
                continue
            background = PaintingPage(page.x, page.y, fitted * 1280 / 956)
            # The modal deliberately darkens these fixed background controls.
            # They establish page identity, never permission to spend or go back.
            if not all(self._anchor(gray, name, background, threshold=.74, search_margin=10)
                       for name in ('title', 'global_label', 'back')):
                continue
            roi = page.roi(35, 280, 45, 100)
            rx, ry, rw, rh = roi
            if rx >= 0 and ry >= 0 and rx + rw <= gray.shape[1] and ry + rh <= gray.shape[0]:
                return PaintingReward(roi)
        return None

    @staticmethod
    def prepare_counter(image, roi, quantity=False):
        """Separate pale currency/red quantity glyphs from the decorative background.

        Tight foreground bounds plus a small border make a single zero readable
        without accepting the low-confidence guesses made from the raw panel.
        """
        if _gray(image) is None:
            return None
        x, y, w, h = roi
        patch = image[y:y+h, x:x+w]
        if patch.shape[:2] != (h, w):
            return None
        hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
        mask = (hsv[:, :, 1] < 85) & (hsv[:, :, 2] > 125)
        if quantity:
            mask |= (((hsv[:, :, 0] < 10) | (hsv[:, :, 0] > 170))
                     & (hsv[:, :, 1] > 95) & (hsv[:, :, 2] > 90))
        if not mask.any():
            return None
        rows, cols = np.where(mask)
        mask = mask[rows.min():rows.max()+1, cols.min():cols.max()+1]
        border = max(2, round(h / 12))
        result = cv2.copyMakeBorder(np.where(mask, 0, 255).astype(np.uint8),
                                   border, border, border, border,
                                   cv2.BORDER_CONSTANT, value=255)
        return cv2.cvtColor(result, cv2.COLOR_GRAY2RGB)
