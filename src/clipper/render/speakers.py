"""Telling who is talking when more than one person is in shot.

Reported on real output: in a b-roll interview the player was answering a
question and the frame held the interviewer. Both were in the picture, so this
is fixable by cropping -- unlike a reaction shot, where the speaker is not in the
source frame at all (docs/DECISIONS.md D42).

The signal is how much the mouth changes between samples *relative to how much
the eyes change*. The obvious version -- mouth change alone, from a patch centred
on YuNet's mouth landmarks -- picked the wrong person on exactly the reported
shot (interviewer 0.71, player 0.38): the interviewer was in profile, profile
landmarks jitter, and jitter looks like a moving mouth. Head motion, jitter and
lighting move the whole face; talking moves the mouth. Dividing by the eye
region cancels the first three. Patches are cut from fixed bands of the face box
rather than from landmarks, which removed the jitter at source.

Measured (docs/VERIFIED.md, 2026-09-22):

    stadium interview, player talking      player 1.10   interviewer 0.64
    studio two-shot, Paul talking          Paul   1.11   Mike        0.91

Two labelled shots is thin evidence, so the signal is only acted on when one
person leads clearly (`SPEAKER_MARGIN`); otherwise the framing shows everyone,
as before.
"""

from __future__ import annotations

from itertools import pairwise
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .layouts import FaceTrack

# Vertical bands of the face box, as fractions of its height.
MOUTH_BAND = (0.62, 0.95)
EYE_BAND = (0.20, 0.50)
# Horizontal extent of both bands, trimmed to stay off the background.
BAND_SIDES = (0.15, 0.85)
PATCH_SIZE = (24, 12)

# One talking score must beat every other by this factor to count as "the
# speaker". The measured leads were 1.72x and 1.22x.
SPEAKER_MARGIN = 1.2

# Observations further apart than this are not compared: a face that dropped
# out of detection for a while has moved for reasons unrelated to speech.
MAX_PAIR_GAP = 0.3

# Fewer compared pairs than this and the score is too noisy to use.
MIN_PAIRS = 5


def face_patches(frame, x: float, y: float, w: float, h: float
                 ) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Normalised mouth and eye patches for one face box in `frame` (BGR)."""
    return _band(frame, x, y, w, h, MOUTH_BAND), _band(frame, x, y, w, h, EYE_BAND)


def _band(frame, x: float, y: float, w: float, h: float,
          band: tuple[float, float]) -> np.ndarray | None:
    import cv2

    y0, y1 = int(y + h * band[0]), int(y + h * band[1])
    x0, x1 = int(x + w * BAND_SIDES[0]), int(x + w * BAND_SIDES[1])
    rows, cols = frame.shape[:2]
    y0, y1, x0, x1 = max(0, y0), min(rows, y1), max(0, x0), min(cols, x1)
    if y1 - y0 < 3 or x1 - x0 < 3:
        return None
    grey = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    patch = cv2.resize(grey, PATCH_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)
    # Normalised so a change in exposure is not mistaken for movement.
    return (patch - patch.mean()) / (patch.std() + 1e-6)


def talking_score(track: FaceTrack) -> float | None:
    """Mean mouth change over mean eye change, or None if too little to judge."""
    mouth: list[float] = []
    eyes: list[float] = []
    obs = track.observations
    for a, b in pairwise(obs):
        if b.t - a.t > MAX_PAIR_GAP:
            continue
        if a.mouth is None or b.mouth is None or a.eyes is None or b.eyes is None:
            continue
        mouth.append(float(np.abs(a.mouth - b.mouth).mean()))
        eyes.append(float(np.abs(a.eyes - b.eyes).mean()))
    if len(mouth) < MIN_PAIRS:
        return None
    return float(np.mean(mouth)) / max(1e-6, float(np.mean(eyes)))


def clear_talker(tracks: list[FaceTrack]) -> tuple[FaceTrack, float, float] | None:
    """The one track that is clearly talking most, with its score and the runner-up's.

    None unless every track could be scored and the leader beats the rest by
    `SPEAKER_MARGIN`: when it is close, showing everyone is safer than guessing.
    """
    if len(tracks) < 2:
        return None
    scored = [(talking_score(t), t) for t in tracks]
    if any(score is None for score, _ in scored):
        return None
    scored.sort(key=lambda pair: -pair[0])
    (best, leader), (second, _) = scored[0], scored[1]
    if best < second * SPEAKER_MARGIN:
        return None
    return leader, best, second
