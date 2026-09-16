"""Read-only checks for MysteryShop shelf accessibility, independent of OCR/RPC."""

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


@lru_cache(maxsize=16)
def _template(path):
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    return None if image is None else cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def _valid(image):
    return (isinstance(image, np.ndarray) and image.dtype == np.uint8
            and image.shape == (720, 1280, 3))


def item_availability(image, rule):
    """Return available/disabled/missing/unknown for the currently matched item.

    Normalized correlation also matches the darkened bond-locked goods. Compare
    their brightest template pixels against the actual item, so naturally dark
    daruma artwork is not confused with a disabled product. Missing/unreadable
    screenshots and borderline brightness never grant permission to click.
    """
    if not _valid(image):
        return 'unknown'
    template = _template(str(Path(rule.file).resolve()))
    if template is None:
        return 'unknown'
    x, y, w, h = map(int, rule.roi_back)
    if x < 0 or y < 0 or x+w > 1280 or y+h > 720:
        return 'unknown'
    source = image[y:y+h, x:x+w]
    th, tw = template.shape[:2]
    if h < th or w < tw:
        return 'missing'
    _, score, _, (mx, my) = cv2.minMaxLoc(cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED))
    if score < rule.threshold:
        return 'missing'
    reference = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)
    actual = cv2.cvtColor(source[my:my+th, mx:mx+tw], cv2.COLOR_RGB2GRAY)
    mask = reference >= max(80, np.percentile(reference, 75))
    if mask.sum() < 30:
        return 'unknown'
    brightness = float(np.median(actual[mask])) / float(np.median(reference[mask]))
    if brightness < .6:
        return 'disabled'
    return 'available' if brightness >= .78 else 'unknown'


def bond_required(image):
    """Known friendship-level toast; never a generic purchase confirmation."""
    if not _valid(image):
        return False
    template = _template(str(Path(__file__).with_name('purchase_assets') / 'bond_required.png'))
    source = image[210:275, 380:900]
    return cv2.minMaxLoc(cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED))[1] >= .86


def purchase_dialog_present(image):
    """Verify both parchment edges around the item, not just its icon.

    Both existing purchase layouts share these vertical paper margins. A shelf
    icon in the central search area cannot satisfy the two bright paper strips
    against their darker outer edges. Quantity controls are checked separately.
    """
    if not _valid(image):
        return False
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    for inner_x, outer_x in ((435, 408), (825, 864)):
        paper = image[300:520, inner_x:inner_x+16]
        r, g, b = paper[:, :, 0], paper[:, :, 1], paper[:, :, 2]
        pale = (r > 160) & (g > 140) & (b > 100) & (r > g) & (g > b)
        inside = gray[300:520, inner_x:inner_x+16]
        outside = gray[300:520, outer_x:outer_x+12]
        if pale.mean() < .8 or float(inside.mean()) - float(outside.mean()) < 50:
            return False
    return True


def shelf_controls_enabled(image):
    """Fixed navigation/reward controls must be bright enough to be uncovered."""
    if not _valid(image):
        return False
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    if np.percentile(gray[13:46, 20:52], 90) < 165:
        return False
    rewards = ((511, 630, 48, 47), (682, 632, 48, 42), (851, 637, 47, 32))
    return sum(np.percentile(gray[y:y+h, x:x+w], 90) >= 160 for x, y, w, h in rewards) >= 2
