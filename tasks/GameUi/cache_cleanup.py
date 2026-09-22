"""Dismiss the observed client-cache popup through its Cancel button."""

from module.atom.image import RuleImage


def _marker(name, roi):
    return RuleImage(roi_front=roi, roi_back=roi, threshold=.9,
                     method='Template matching',
                     file=f'./tasks/GameUi/recovery_assets/cache_cleanup_{name}.png')


TITLE = _marker('title', (382, 166, 520, 59))
VIDEO = _marker('video', (412, 254, 93, 28))
IMAGES = _marker('images', (412, 383, 93, 28))
CANCEL = _marker('cancel', (448, 493, 76, 36))


def cache_cleanup_visible(task):
    # Resource sizes and selected checkboxes can vary. Require both cache
    # labels and the specific prompt, never a generic confirmation button.
    return task.appear(TITLE) and task.appear(VIDEO) and task.appear(IMAGES)
