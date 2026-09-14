These small UI templates were extracted from the dispatch screenshots supplied
with this feature request. No full account screenshot is included.

- `map_name`, `empty`, `locked`: current activity map and slot state anchors.
- `inspect`: the occupied-slot magnifier, paired with a separately read countdown.
- `chevron`, `name_0` through `name_5`, `selected`: portrait drawer and selection.
- `rewards`, `duration`, `minus`, `plus`, `submit`: the dispatch setup panel.
- `success_title`, `success_dismiss`: the shared dispatch-success title and
  dismissal prompt. Both must match at their expected relative positions.
  Character art, name, duration and fading success/return text are not matched.
- `remaining`, `recall`: the occupied-character detail pane; these only permit
  collapsing its drawer, never pressing Recall.

Reference drawer/setup geometry is 1111 by 625. Matching recovers scale and
translation from the images; these are not absolute device coordinates.
The occupied-slot reference was 956 by 538 and is matched independently.
Name templates exclude the changing gold selection border. OCR regions exclude
the clock icon before a countdown and the decoration after a duration.
The success-overlay and running-detail templates use an 840 by 473 reference.
