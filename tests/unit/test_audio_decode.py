"""faster-whisper's own audio decoder must work with the installed PyAV: PyAV 19 dropped the
`metadata_errors` argument faster-whisper passes, and every transcription failed (D135)."""

from __future__ import annotations

import numpy as np
import soundfile as sf
from faster_whisper.audio import decode_audio


def test_faster_whisper_can_decode_audio_with_the_installed_pyav(tmp_path):
    path = tmp_path / "tone.wav"
    sf.write(path, np.sin(np.linspace(0, 440, 16000)).astype("float32"), 16000)
    assert len(decode_audio(str(path), sampling_rate=16000)) > 15000
