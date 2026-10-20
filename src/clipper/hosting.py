"""Whether this Clipper runs on a server for other people (D224), for any module, not only the studio's.

On a server the gateway (`clipper.hosted`) starts one Clipper per user with CLIPPER_HOSTED=1; a Clipper
reachable from other machines (CLIPPER_HOST not this computer, D148) counts as hosted too. Hosted, the
owner's own-computer paths are off: Claude Code on a personal plan, self-update, the Recycle Bin, Explorer.
"""

from __future__ import annotations

import os

LOOPBACK = ("127.0.0.1", "localhost", "::1")


def hosted() -> bool:
    """On a server others reach."""
    host = os.environ.get("CLIPPER_HOST", "").strip() or "127.0.0.1"
    return host not in LOOPBACK or os.environ.get("CLIPPER_HOSTED", "") == "1"
