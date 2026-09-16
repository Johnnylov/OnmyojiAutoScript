# Battle disconnect regression

`2026-09-16_server_disconnect.png` comes from the 07:40:54 screenshot in
`oas1_1789515654768`. The activity battle was blocked by the explicit game-server
disconnect dialog, then incorrectly treated as a failed battle-exit transition.

Only the dialog at `(431, 253, 416, 208)` in the original 1280×720 frame is kept.
The fixture contains no player name, chat, account information, or battle scene.
Tests reconstruct its original location on a blank frame, so deleting runtime
error logs does not remove this regression coverage.
