"""Bounded Monte Carlo refinement for click ROIs; no device or timing state."""
import math

import numpy as np


def monte_carlo_click_point(roi, *, samples=32, rounds=3, contraction=0.65,
                            min_spread=0.30, inset=0.05, rng=None):
    """Return native integer (x, y) for an (x, y, width, height) ROI.

    Each round samples a population in the current rectangle, estimates a
    center from the closest quarter to the geometric center, and contracts
    the rectangle. The final point is sampled uniformly from that rectangle,
    NOT taken from its center or from the best candidate.

    min_spread is the minimum retained fraction on each axis. A one-pixel
    radius floor keeps small multi-pixel targets random. inset removes a
    fraction of pixels from each edge (rounded down). Positive extents use
    half-open bounds; a zero extent denotes a fixed coordinate on that axis.
    Invalid or negative extents fail before a device action is issued.

    Refinement restarts for every call. This is geometric refinement, not
    learning from successful clicks. rng may be a NumPy Generator/RandomState;
    the default uses np.random so existing np.random.seed calls still work.
    """
    if not isinstance(samples, (int, np.integer)) or not 4 <= samples <= 4096:
        raise ValueError("samples must be an integer in [4, 4096]")
    if not isinstance(rounds, (int, np.integer)) or not 1 <= rounds <= 16:
        raise ValueError("rounds must be an integer in [1, 16]")
    if not 0 < contraction < 1:
        raise ValueError("contraction must be in (0, 1)")
    if not 0 < min_spread <= 1:
        raise ValueError("min_spread must be in (0, 1]")
    if not 0 <= inset < 0.5:
        raise ValueError("inset must be in [0, 0.5)")
    values = np.asarray(roi, dtype=float)
    if values.shape != (4,) or not np.isfinite(values).all():
        raise ValueError("roi must contain four finite numbers")
    if np.any(values[2:] < 0):
        raise ValueError("roi width and height must be non-negative")

    lower, upper = [], []
    for origin, extent in zip(values[:2], values[2:]):
        if extent == 0:
            lo = int(round(origin))
            hi = lo + 1
        else:
            lo = math.ceil(origin)
            hi = math.ceil(origin + extent)
            if hi <= lo:
                raise ValueError("roi contains no integer pixel on an axis")
        padding = math.floor((hi - lo) * inset)
        lower.append(lo + padding)
        upper.append(hi - padding)

    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)
    span = upper - lower
    if np.all(span == 1):
        return int(lower[0]), int(lower[1])

    rng = np.random if rng is None else rng
    # Normalization prevents a wide target's x distance dominating y.
    center = np.full(2, 0.5)
    radius = np.full(2, 0.5)
    min_radius = np.minimum(0.5, np.maximum(min_spread / 2, 1.0 / span))
    elite_count = max(1, samples // 4)
    for _ in range(rounds):
        low = np.maximum(0.0, center - radius)
        high = np.minimum(1.0, center + radius)
        candidates = rng.uniform(low, high, size=(samples, 2))
        distance = np.sum((candidates - 0.5) ** 2, axis=1)
        elite = np.argpartition(distance, elite_count - 1)[:elite_count]
        center = candidates[elite].mean(axis=0)
        radius = np.maximum(radius * contraction, min_radius)
        # Keep the entire retained window inside the ROI without shrinking it.
        center = np.clip(center, radius, 1.0 - radius)

    point = lower + rng.uniform(center - radius, center + radius) * span
    # Guard against floating-point rounding at the exclusive upper edge.
    point = np.minimum(np.floor(point), upper - 1)
    return int(point[0]), int(point[1])
