# Fonts

This project ships **no third-party font**. Text is drawn with the
self-contained 3×5 pixel font hand-authored in
[`dashboard/pixelfont.py`](../pixelfont.py) (public domain — created for this
project), so there is never a runtime dependency on a system font and no font
licensing concern.

If you want a larger/second font later, add its glyph table to `pixelfont.py`
(same `list[str]` format) rather than copying an external `.ttf`/`.bdf` with an
unclear licence into the repository.
