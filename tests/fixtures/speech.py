"""Speech fixtures synthesised locally with Windows SAPI.

Windows ships two SAPI voices, so a speech fixture can be generated with no
downloads and -- crucially -- with **known ground-truth text**. A test can then
assert what the transcriber should have produced, not merely that it produced
something. Verified on this machine: faster-whisper `small` transcribes SAPI
speech word-for-word with correct punctuation.

Silence between utterances is inserted by FFmpeg rather than by SSML
``<break>``, so the gaps are exact. Segmentation tests depend on that: they
assert that a pause of a known length does or does not force a sentence break.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from clipper.render.ffmpeg import run

from .synthetic import _cached, _encode

SAPI_VOICES = ("Microsoft David Desktop", "Microsoft Zira Desktop")

_SAPI_SCRIPT = """Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {{ $s.SelectVoice("{voice}") }} catch {{ }}
$s.Rate = {rate}
$s.SetOutputToWaveFile("{out}")
$s.Speak([System.IO.File]::ReadAllText("{textfile}", [System.Text.Encoding]::UTF8))
$s.Dispose()
"""


@dataclass(frozen=True)
class Utterance:
    """One spoken line plus the exact silence that follows it."""

    text: str
    pause_after: float = 0.5
    voice: int = 0  # index into SAPI_VOICES
    rate: int = 0   # SAPI rate, -10..10


@dataclass(frozen=True)
class SpeechFixture:
    """Generated speech plus the ground truth to assert against."""

    audio: Path
    video: Path | None
    utterances: tuple[Utterance, ...]
    duration: float

    @property
    def transcript_text(self) -> str:
        return " ".join(u.text for u in self.utterances)

    @property
    def expected_sentences(self) -> int:
        """Utterances ending in terminal punctuation."""
        return sum(1 for u in self.utterances if u.text.rstrip().endswith((".", "!", "?")))


def sapi_available() -> bool:
    """Whether Windows SAPI can be driven from here."""
    if os.name != "nt":
        return False
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Add-Type -AssemblyName System.Speech; "
             "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
             ".GetInstalledVoices().Count"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return proc.returncode == 0 and proc.stdout.strip().isdigit()
    except (OSError, subprocess.TimeoutExpired):
        return False


def _speak(text: str, out: Path, *, voice: int, rate: int) -> Path:
    """Synthesise one utterance to a WAV.

    The text goes via a file rather than the command line: PowerShell quoting of
    apostrophes and non-ASCII is a reliable source of corrupted fixtures.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    textfile = out.with_suffix(".txt")
    textfile.write_text(text, encoding="utf-8")
    script = out.with_suffix(".ps1")
    script.write_text(
        _SAPI_SCRIPT.format(
            voice=SAPI_VOICES[voice % len(SAPI_VOICES)],
            rate=rate,
            out=str(out).replace("\\", "\\\\"),
            textfile=str(textfile).replace("\\", "\\\\"),
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, timeout=180, check=False,
    )
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"SAPI synthesis failed: {proc.stderr[:400]}")
    return out


def speech_audio(utterances: list[Utterance], *, name: str = "speech") -> Path:
    """Concatenate synthesised utterances with exact silences between them."""
    sig = "|".join(f"{u.text}~{u.pause_after}~{u.voice}~{u.rate}" for u in utterances)
    out = _cached("speech_" + name, sig).with_suffix(".wav")
    if out.exists():
        return out

    work = out.parent / f"{out.stem}_parts"
    work.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []

    for i, utterance in enumerate(utterances):
        raw = _speak(utterance.text, work / f"{i:03d}_raw.wav",
                     voice=utterance.voice, rate=utterance.rate)
        part = work / f"{i:03d}.wav"
        run([
            "-hide_banner", "-loglevel", "error",
            "-i", str(raw),
            "-af", f"aresample=16000,apad=pad_dur={utterance.pause_after}",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            "-y", str(part),
        ])
        parts.append(part)

    listing = work / "concat.txt"
    listing.write_text("\n".join(f"file '{p.name}'" for p in parts) + "\n", encoding="utf-8")
    run([
        "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", listing.name,
        "-c:a", "pcm_s16le", "-ac", "1", "-ar", "16000",
        "-y", str(out),
    ], cwd=work)
    return out


def speech_video(
    utterances: list[Utterance],
    *,
    name: str = "speech",
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    faces: int = 1,
) -> SpeechFixture:
    """A video carrying real synthesised speech, for end-to-end pipeline tests."""
    from clipper.ingest.probe import probe

    audio = speech_audio(utterances, name=name)
    duration = probe(audio).duration

    sig = f"sv-{name}-{width}x{height}-{fps}-{faces}-{duration:.2f}"
    video = _cached("speechvid_" + name, sig)
    if video.exists():
        return SpeechFixture(audio, video, tuple(utterances), duration)

    r = int(height * 0.13)
    size = r * 2
    disc = (
        f"format=yuva420p,geq=lum='p(X,Y)':"
        f"a='if(lte(hypot(X-{size / 2},Y-{size / 2}),{size / 2}),255,0)'"
    )
    args = [
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i",
        f"color=c=0x1c1c2c:size={width}x{height}:rate={fps}:duration={duration:.3f}",
        "-f", "lavfi", "-i",
        f"color=c=0xE8C4A0:size={size}x{size}:rate={fps}:duration={duration:.3f}",
    ]
    if faces >= 2:
        args += ["-f", "lavfi", "-i",
                 f"color=c=0xB8D4F0:size={size}x{size}:rate={fps}:duration={duration:.3f}"]
        chain = (
            f"[1:v]{disc}[d1];[2:v]{disc}[d2];"
            f"[0:v][d1]overlay=x={int(width * 0.27) - r}:y={int(height * 0.45) - r}[b1];"
            f"[b1][d2]overlay=x={int(width * 0.73) - r}:y={int(height * 0.45) - r}[v]"
        )
        audio_input = 3
    else:
        chain = (
            f"[1:v]{disc}[d1];"
            f"[0:v][d1]overlay=x={int(width * 0.5) - r}:y={int(height * 0.45) - r}[v]"
        )
        audio_input = 2

    args += ["-i", str(audio), "-filter_complex", chain,
             "-map", "[v]", "-map", f"{audio_input}:a",
             "-c:a", "aac", "-b:a", "128k", "-shortest"]
    _encode(args, video)
    return SpeechFixture(audio, video, tuple(utterances), duration)


# --------------------------------------------------------------------------
# scripts
# --------------------------------------------------------------------------

# Long, exaggerated pauses. Used by segmentation tests, which need to assert
# that a gap of a known length forces a sentence boundary.
#
# Measured on this machine: ~42% of this fixture is silence, which is *above*
# the 25% hard filter in candidates config -- so it deliberately produces zero
# candidates. That is the correct behaviour, not a bug, and there is a test
# asserting it.
PAUSED_SCRIPT: tuple[Utterance, ...] = (
    Utterance("Most people think the hardest part is getting started.", 0.9),
    Utterance("It isn't.", 1.0),
    Utterance("The hardest part is deciding what to stop doing.", 1.8),
    Utterance("So, um, I want to tell you about a mistake I made last year.", 0.9),
    Utterance("I took on four projects at once because I could not say no.", 0.9),
    Utterance("Every single one of them shipped late.", 1.8),
    Utterance("Here is what changed everything for me.", 0.9),
    Utterance("I started writing down what I was going to refuse.", 0.9),
    Utterance("Within two months my output actually doubled.", 1.5),
)

DEFAULT_SCRIPT = PAUSED_SCRIPT

# Realistic conversational cadence (0.2-0.5 s between sentences) and long enough
# to generate a real candidate set. Structured so a human could say which window
# is best: a strong hook and payoff near the middle, filler-heavy opening lines
# for the refinement tests, and an ad read that the sponsor filter must catch.
DENSE_SCRIPT: tuple[Utterance, ...] = (
    Utterance("Welcome back to the show, it is good to have you here again.", 0.35),
    Utterance("Today we are talking about why most side projects die.", 0.3),
    Utterance("So, um, I guess we should start with the obvious question.", 0.3),
    Utterance("Why do people quit?", 0.4),
    Utterance("Most people think they quit because they run out of motivation.", 0.3),
    Utterance("That is not what the data says at all.", 0.4),
    Utterance("They quit because the next step was never written down.", 0.3),
    Utterance("I lost eleven months to that exact mistake.", 0.4),
    Utterance("Eleven months, on a project I could have finished in six weeks.", 0.45),
    Utterance("Here is the part that actually changed everything for me.", 0.3),
    Utterance("Every night I wrote one sentence describing tomorrow's first action.", 0.3),
    Utterance("One sentence, nothing more than that.", 0.4),
    Utterance("My completion rate went from about ten percent to over seventy.", 0.45),
    Utterance("And the strange part is that it had nothing to do with discipline.", 0.3),
    Utterance("It was purely about removing the decision at the start of the day.", 0.4),
    Utterance("Anyway, we should talk about what this means for teams.", 0.35),
    Utterance("A team that cannot name its next action is a team that is already stuck.", 0.3),
    Utterance("I have watched that happen at three different companies now.", 0.4),
)

# A sponsor read, to check the pre-LLM ad filter. Several distinct ad phrases,
# because the filter deliberately requires more than one hit.
SPONSOR_SCRIPT: tuple[Utterance, ...] = (
    Utterance("Before we continue, this episode is sponsored by a company I use daily.", 0.3),
    Utterance("Go to example dot com slash show to get twenty percent off.", 0.3),
    Utterance("Use the promo code SHOW at checkout for a free trial.", 0.3),
    Utterance("The link is in the description below, so go and check it out.", 0.4),
)
