"""Find the activity entry before advancing the courtyard's rotating menu."""

from module.atom.image import RuleImage
from tasks.ActivityShikigami.assets import ActivityShikigamiAssets
from tasks.Component.RightActivity.assets import RightActivityAssets
from tasks.GameUi.page import page_main


# The leader reward glow changes the old icon's center. Keep that original
# template, and recognize the complete bright variant inside the right menu.
BRIGHT_ACTIVITY_ENTRY = RuleImage(
    roi_front=(1179, 207, 57, 68), roi_back=(1164, 134, 83, 393),
    threshold=0.9, method='Template matching',
    file='./tasks/ActivityShikigami/entrance_assets/activity_entry_bright.png',
)


def enter_activity(task) -> bool:
    """Only a delivered entry click completes the navigation action.

    Navigation supplies a fresh screenshot on each call and bounds this search.
    A menu toggle is an intermediate step: return False so the next frame can
    find the revealed icon before recording a failed page transition.
    """
    if not task.match_page_once(page_main):
        return False
    for marker in (ActivityShikigamiAssets.I_MAIN_GOTO_ACT, BRIGHT_ACTIVITY_ENTRY):
        if task.appear(marker):
            # A visible entry on cooldown must not be cycled out of view.
            return task.appear_then_click(marker, interval=0.8)
    task.appear_then_click(RightActivityAssets.I_TOGGLE_BUTTON, interval=2)
    return False
