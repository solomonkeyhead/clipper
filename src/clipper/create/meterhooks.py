"""Where a hosted plan counts a Create Short (D233): a Short is one first build plus 5 redraws, so the build tells
these hooks when each starts. Both do nothing here; the hosted plan meter (package E3) fills them in. Each is called
before any AI call of what it counts, so raising from one (a plain-words CreateError) stops that build before it
spends anything, and the reason shows on the video."""

from __future__ import annotations


def on_first_build(video_id: int) -> None:
    """A video's first build is starting: it has never been built. A first build that failed or was cancelled is
    still the first, so the next try calls this again: count a video once, by its id."""


def on_redraw(video_id: int, count: int = 1) -> None:
    """A build after the first is about to make `count` drawings again because the user asked: New drawing, Change
    drawing, Redraw all, or the drawings New pictures planned. Not called for the first build's own drawings, or for
    a rebuild that redraws nothing."""
