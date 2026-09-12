"""Locate the current event's soul selector using its own visual anchors.

Images are RGB arrays. Coordinates below describe the reference selector, not
the device screen: two independently matched anchors recover its scale and
translation before any click region is returned. No device or OCR is imported.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


_ASSETS = Path(__file__).with_name("soul_selection")
_TITLE = (60, 28)
_SUBMIT = (636, 396)
_CELL_X = (140, 262, 383, 505, 626)
_CELL_Y = (120, 224, 329)


@lru_cache(maxsize=4)
def _template(name):
    image = cv2.imread(str(_ASSETS / (name + ".png")), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(_ASSETS / (name + ".png"))
    return image


def _gray(image):
    if not isinstance(image, np.ndarray) or image.ndim != 3:
        return None
    if image.shape[2] != 3 or min(image.shape[:2]) < 32:
        return None
    if image.dtype != np.uint8:
        return None
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def _scaled(template, scale):
    return cv2.resize(template, None, fx=scale, fy=scale,
                      interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)


def _best(image, template):
    if (image.size == 0 or image.shape[0] < template.shape[0]
            or image.shape[1] < template.shape[1]):
        return None
    scores = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, point = cv2.minMaxLoc(scores)
    return float(score), point


def _candidates(gray, name, threshold):
    """Search reference scales independently of the screenshot aspect ratio."""
    reduction = min(1.0, 1050.0 / max(gray.shape))
    search = _scaled(gray, reduction) if reduction < 1 else gray
    original = _template(name)
    matches = []
    # Include the reference size exactly and common emulator scales.
    scales = sorted(set(np.arange(.55, 2.61, .05).round(3)) | {1., 1.25, 1.5, 1.625, 2.})
    for scale in scales:
        match = _best(search, _scaled(original, scale * reduction))
        if match is not None and match[0] >= threshold:
            score, (x, y) = match
            matches.append((score, x / reduction, y / reduction, scale))
    return sorted(matches, reverse=True)


@dataclass(frozen=True)
class Panel:
    """Reference-to-screenshot transform; all regions use (x, y, width, height)."""

    x: float
    y: float
    scale: float

    def _roi(self, x, y, width, height):
        return (round(self.x + x * self.scale),
                round(self.y + y * self.scale),
                max(1, round(width * self.scale)),
                max(1, round(height * self.scale)))

    def cell_roi(self, index):
        if not isinstance(index, int) or isinstance(index, bool) or not 1 <= index <= 15:
            raise ValueError("Soul cell index must be between 1 and 15")
        row, col = divmod(index - 1, 5)
        return self._roi(_CELL_X[col] - 14, _CELL_Y[row] - 14, 28, 28)

    @property
    def submit_roi(self):
        return self._roi(645, 410, 36, 38)

    @property
    def close_roi(self):
        return self._roi(696, 44, 23, 24)


class SoulSelectionView:
    """Small, conservative template reader for 虚无精锐's daily soul selector."""

    def find_entry(self, image):
        gray = _gray(image)
        if gray is None:
            return None
        matches = _candidates(gray, "entry", .82)
        if not matches:
            return None
        _, x, y, scale = matches[0]
        # Click the switch arrows beside the matched label, not its text.
        region = (round(x + 149 * scale), round(y),
                  max(1, round(30 * scale)), max(1, round(28 * scale)))
        rx, ry, rw, rh = region
        if rx < 0 or ry < 0 or rx + rw > gray.shape[1] or ry + rh > gray.shape[0]:
            return None
        return region

    def find_panel(self, image):
        gray = _gray(image)
        if gray is None:
            return None
        candidates = _candidates(gray, "title", .77)
        reference_delta = np.array(_SUBMIT, dtype=float) - _TITLE
        for _, title_x, title_y, scale in candidates[:8]:
            # The distant submit anchor must agree with the title in both
            # position and scale, preventing a title-only or partial dialog
            # from producing coordinates for clicks.
            template = _scaled(_template("submit"), scale)
            expected = np.array((title_x, title_y)) + reference_delta * scale
            margin = round(14 * scale + 3)
            left = max(0, round(expected[0]) - margin)
            top = max(0, round(expected[1]) - margin)
            right = min(gray.shape[1], round(expected[0]) + template.shape[1] + margin)
            bottom = min(gray.shape[0], round(expected[1]) + template.shape[0] + margin)
            match = _best(gray[top:bottom, left:right], template)
            if match is None or match[0] < .75:
                continue
            _, (mx, my) = match
            submit_point = np.array((left + mx, top + my), dtype=float)
            title_point = np.array((title_x, title_y), dtype=float)
            delta = submit_point - title_point
            fitted_scale = float(np.dot(delta, reference_delta) /
                                 np.dot(reference_delta, reference_delta))
            if abs(fitted_scale - scale) > .045:
                continue
            if np.linalg.norm(delta - reference_delta * fitted_scale) > max(4, 5 * scale):
                continue
            origin = ((title_point - np.array(_TITLE) * fitted_scale) +
                      (submit_point - np.array(_SUBMIT) * fitted_scale)) / 2
            panel = Panel(float(origin[0]), float(origin[1]), fitted_scale)
            # Every cell and both action regions must be visible. A cropped
            # dialog is not safe to operate even when its anchors are visible.
            regions = [panel.cell_roi(i) for i in range(1, 16)]
            regions += [panel.submit_roi, panel.close_roi]
            if all(x >= 0 and y >= 0 and x + w <= gray.shape[1]
                   and y + h <= gray.shape[0] for x, y, w, h in regions):
                return panel
        return None

    def selected(self, image, panel):
        """Return checked one-based cells, or None if the image is ambiguous."""
        gray = _gray(image)
        if gray is None or not isinstance(panel, Panel):
            return None
        selected = set()
        for index in range(1, 16):
            row, col = divmod(index - 1, 5)
            # The small blue tick is fixed at each portrait's upper-right.
            x, y, width, height = panel._roi(_CELL_X[col] + 6, _CELL_Y[row] - 43, 38, 40)
            if x < 0 or y < 0 or x + width > gray.shape[1] or y + height > gray.shape[0]:
                return None
            patch = gray[y:y + height, x:x + width]
            scores = []
            for factor in (1., .94, 1.06):
                match = _best(patch, _scaled(_template("selected"), panel.scale * factor))
                if match is not None:
                    scores.append(match[0])
            score = max(scores, default=0.)
            if score >= .78:
                selected.add(index)
            elif score >= .62:
                return None
        return frozenset(selected)
