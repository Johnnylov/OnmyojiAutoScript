# Duel result regressions

`victory_1544.png` and `victory_1804.png` are the result banner at
`(350, 40, 660, 185)` from the September 15, 2026 errors at 15:44 and 18:04.
Only the shared result artwork is retained; player names, teams, scores and
chat are excluded. Tests restore the crop to its original 1280×720 position.

Both images already match the existing victory template. The regression was
the blocking auto-mode loop continuing OCR after battle controls disappeared.
