These sparse RGB fixtures preserve the actual 2026-09-15 coloring reward
screens at 1280 × 720. They contain only the painting title/back controls,
global progress, pigment counter, bottom-right quantity controls and the
reward panel. All other pixels are black, including the top broadcast/avatar
area. No account names, IDs, chat or original logs are included.

- `reward_coin.png`: pigment decreased from 3512 to 2513 after selecting 999.
- `reward_daruma.png`: pigment decreased from 6604 to 5605 after selecting 999.

The two screenshots independently show the same delayed reward frame with
different reward artwork. Tests load these committed crops unconditionally;
deleting `log/error` cannot silently skip these regressions.

`completion_story.png` keeps only the real caption, paper-doll speaker and
skip-control bands from the 2026-09-21 13:42 completed-painting story capture.
Everything else is black. It reproduces the missing-logo ending independently
of the local error archive. Post-skip confirmation and progress are simulated
in workflow tests because the reports contain no subsequent game frames.
