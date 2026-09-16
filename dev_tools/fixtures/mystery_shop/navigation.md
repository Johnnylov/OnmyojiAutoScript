# Navigation regression

`courtyard_marker.png` is only the 76×45 navigation marker at `(807, 108)`
from the September 16, 2026 09:40 shop error. It contains no player details.
The error image showed the courtyard after the old next-shop loop treated a
missing share button as success. Replay verifies the existing courtyard
recognizer; flow tests require a confirmed shop and stable nonblank friend
name before accepting a switch, and bound parent-page recovery to one re-entry.
