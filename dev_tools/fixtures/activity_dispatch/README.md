These offline regression fixtures retain only relevant game UI at its original
1280 by 720 coordinates. Chat, player information, resource counters and reward
amounts are removed. They remain available after error logs are cleared.

- `interrupted_multi_return.png`: title, game-character artwork/message and
  dismissal instruction from `oas1_1789404232149/2026-09-15_00-43-48-979229.png`.
  The game had already recalled several characters after a resource shortage.
- `five_available_portraits.png`: boss-name anchor, collapse arrow and five
  remaining cards from `oas2_1789404965119/2026-09-15_00-56-03-101309.png`.
  The busy first character was absent, moving all remaining cards left.

Tests explicitly label rearranged copies of these card strips as synthetic
layout variations; they are not additional real account screenshots.
