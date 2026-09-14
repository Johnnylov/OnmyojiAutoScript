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
}


@lru_cache(maxsize=9)
def _template(name):
    result = cv2.imread(str(ASSETS / f'{name}.png'), cv2.IMREAD_GRAYSCALE)
    if result is None:
        raise FileNotFoundError(ASSETS / f'{name}.png')
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
    for scale in sorted(set(np.arange(.55, 2.61, .05).round(3)) | {1., 1280 / 956}):
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
