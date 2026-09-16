Retained regression crops from the 2026-09-16 MysteryShop reports:

- `locked_shared_shelf.png` keeps only the goods region and friendship-level
  toast. The black daruma scrap and four-star taiko are darkened/locked; the
  three-star taiko on the same shelf remains enabled.
- `blue_quantity_dialog.png` keeps the central real blue-amulet dialog.
  Its item icon matches the existing template at approximately 1.025 scale.

Both also retain the fixed back arrow and three bottom reward icons to test
that a modal's dimmed background is not treated as an actionable shelf.
The original 1280 × 720 coordinates are preserved with all other pixels black.
Player names, avatars, account balances, friend identity and shop timer are
excluded. Tests load these fixtures unconditionally and do not depend on the
private `log/error` directory or temporary clipboard screenshots.
