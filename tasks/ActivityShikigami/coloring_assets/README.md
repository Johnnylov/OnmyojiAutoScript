The tiny `intro_logo` and `intro_speaker` anchors are cropped from the supplied
first-entry 百鬼夜行图 story screenshot (1280 × 720). Their rectangles are
(89, 162, 47, 149) and (601, 564, 87, 27). Full private screenshots remain local.

Story handling reuses `../as/as_skip_button.png` and `../as/as_confirm_skip.png`.
Neither button alone identifies the guide: the logo, speaker, relative layout,
and action brightness are verified together. The confirmation layout uses the
existing activity asset's coordinates; its regression is synthetic because the
new report supplied only the initial story screen.
