The tiny `intro_logo` and `intro_speaker` anchors are cropped from the supplied
first-entry 百鬼夜行图 story screenshot (1280 × 720). Their rectangles are
(89, 162, 47, 149) and (601, 564, 87, 27). Full private screenshots remain local.

Story handling reuses `../as/as_skip_button.png` and `../as/as_confirm_skip.png`.
Neither button alone identifies the guide: the logo, speaker, relative layout,
and action brightness are verified together. The confirmation layout uses the
existing activity asset's coordinates; its regression is synthetic because the
new report supplied only the initial story screen.

`reward_frame.png` is the small lower-left scroll corner (312, 421, 88, 78)
from the 2026-09-15 coloring reward report. Recognition also requires the
shared `GlobalGame/ui/ui_ui_reward.png` title and the painting page's title,
back and global-progress label. Only the left margin is exposed for closing.
