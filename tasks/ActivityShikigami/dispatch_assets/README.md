These small UI templates were extracted from the dispatch screenshots supplied
with this feature request. No full account screenshot is included.

- `map_name`, `empty`, `locked`: current activity map and slot state anchors.
- `inspect`: the occupied-slot magnifier, paired with a separately read countdown.
- `chevron`, `name_0` through `name_5`, `selected`: portrait drawer and selection.
- `rewards`, `duration`, `minus`, `plus`, `submit`: the dispatch setup panel.
- `success_title`, `success_dismiss`: the shared dispatch-success title and
  dismissal prompt. Both must match at their expected relative positions.
  Character art, name, duration and fading success/return text are not matched.
- `return_title`: the shared dispatch-ended title from a returned-character
  reward overlay. It must pair with `success_dismiss`; character name OCR is
  optional progress evidence between successive return overlays, not an anchor.
- `interrupt_title`: the resource-shortage interruption acknowledgement; paired
  with the common dismissal prompt. Closing it never activates a recall button.
- `level_up_title`: the full "派遣等级升级" title from the September 18 level
  4-to-5 error capture, resized to the 840 by 473 reference. It must pair with
  the common dismissal prompt at the level-up popup's relative position.
  Level numbers and unlock descriptions are variable, so neither is matched.
- `remaining`, `recall`: the occupied-character detail pane; these only permit
  collapsing its drawer, never pressing Recall.

Reference drawer/setup geometry is 1111 by 625. Matching recovers scale and
translation from the images; these are not absolute device coordinates.
The occupied-slot reference was 956 by 538 and is matched independently.
Name templates exclude the changing gold selection border. OCR regions exclude
the clock icon before a countdown and the decoration after a duration.
The centered duration label shifts when the current/maximum hours gain digits;
its matched position anchors the OCR crop, including the full `12/12时` line.
Busy characters can disappear from the drawer. Name templates are searched
within each remaining card column; their original column is not an identity.
The success-overlay and running-detail templates use an 840 by 473 reference.
The level-up title starts at (311, 125) in that reference and its dismissal
prompt at (358, 436). Recognition exposes only the prompt's safe interior;
recovery closes one level-up acknowledgement per attempt and requires progress.
