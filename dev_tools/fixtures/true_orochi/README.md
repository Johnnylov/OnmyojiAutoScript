# True Orochi screenshots

Five reference crops provided by the user on 2026-09-17: entry stack badge (2),
weekly rewards (2/2), confirmation (2 remaining), invitation-only room creation,
and the empty True Orochi team room. Coordinates in `tasks/TrueOrochi/view.py`
are relative to these crops, recovered by template anchors at runtime.

The small crops in `tasks/TrueOrochi/team_images/` are recognition templates
extracted from these references. Tests also translate and resize the references,
remove a private check mark, and obscure a room slot to check transitions.

`invite_tabs.png` contains only the invitation tab labels from the 2026-09-17
stuck-screen log. It reproduces the different True Orochi tab order and wide
fourth tab without including account names or the friend list.

`room_joined.png` and `prepare.png` are the follow-up screenshots supplied on
2026-09-17: the configured friend occupies the middle slot while the third slot
stays empty, followed by the configuration page with its Ready drum. Tests
replay both images at their supplied size and at 1280x720 with two local task
instances and real name OCR, including a missing member-side room acknowledgement.

`exploration_one.png` is the subsequent user screenshot with a red "1" badge
on the top-left dragon. Its unprocessed badge has low OCR confidence; tests
exercise contrast isolation, the normal confidence threshold, and clicking
this entry using the real task counter reader.
