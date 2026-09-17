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
