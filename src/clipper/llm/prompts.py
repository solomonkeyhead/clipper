"""Versioned rubric prompts.

`PROMPT_VERSION` is part of the LLM cache key, so editing anything in this file
must bump it -- otherwise a changed prompt silently reuses scores produced by the
old one. BUILD_BRIEF.md section 14.2 requires this for the few-shot injection
too, and the same mechanism covers both.

Prompt A and prompt B are the two voices from section 9.1. They are *not*
independent judges -- same model, same call shape, correlated errors -- so the
disagreement penalty is a hedge against prompt-specific artefacts rather than
real variance reduction. That limitation is recorded in PLAN.md (P3).
"""

from __future__ import annotations

from dataclasses import dataclass

PROMPT_VERSION = "v3"
"""Bump on ANY edit below. It is part of the cache key."""

SCHEMA_DESCRIPTION = """\
Each object must contain exactly these keys:
  index                 integer, the clip number you were given
  hook_strength         integer 0-10, would the first 3 seconds stop a scroll
  standalone_clarity    integer 0-10, understandable with zero prior context
  payoff                integer 0-10, is there a clear point, punchline or reveal
  emotional_intensity   integer 0-10, humour, surprise, controversy, vulnerability, awe
  quotability           integer 0-10, contains a line someone would repeat
  ending_completeness   integer 0-10, does it end on a finished thought
  needs_prior_context   boolean
  is_sponsor_or_ad      boolean
  policy_risk           one of "none", "low", "high"
  hook_text             string
  suggested_caption     string
  hashtags              array of 3-5 strings, each starting with #\
"""

# The model tends to echo the transcript back verbatim as `hook_text` unless
# told very explicitly not to -- observed on gemini-3.5-flash-lite during Phase 0
# verification. Hence the emphatic wording and the worked example.
HOOK_TEXT_RULES = """\
hook_text is an ORIGINAL on-screen line of 3-8 words (at most 40 characters),
shown from the first frame to give a scrolling viewer a reason to stay. It is
NOT a quote and NOT a copy of the transcript. Describe the situation, the
stakes, or a relatable identity ("When your boss asks for honesty"), or ask a
question -- and never promise an outcome the clip does not deliver. It must be
faithful to the clip and must not invent facts, numbers or claims that are not
in the transcript. Name only people, places and things the transcript itself
mentions: a party scene is not a "group chat", a dinner is not a "meeting".
  Transcript: "I lost forty thousand dollars in one afternoon because I ignored
               the one rule I had written down myself."
  Good hook_text: "The $40,000 rule he ignored"
  Bad  hook_text: "I lost forty thousand dollars in one afternoon" (a quote)\
"""

# Scenes from scripted TV or film are judged for viewers who have never seen
# the show; which moments travel comes from short-form retention research
# (docs/DECISIONS.md D52).
SCRIPTED_RULES = """\
If a clip is a scene from a TV show or film: favour conflict with a clear
winner, awkward or cringe situations, quick one-liners and roasts, and
relatable work, family or dating moments. Judge standalone_clarity for a viewer
who has never seen the show: the premise must be clear from the first line.\
"""

POLICY_RULES = """\
policy_risk covers hate, harassment, dangerous claims, sexual content, and
unmarked medical or financial claims. Use "high" only for content that a
platform would likely remove. Ordinary strong opinions are "none" or "low".\
"""

PROMPT_A_SYSTEM = f"""\
You are a short-form video editor picking moments from a long video for TikTok,
YouTube Shorts and Reels. You will receive a numbered list of candidate clips,
each with its transcript and duration. Score each one on the rubric using
integers 0-10. Be harsh: most clips should score 3-6. Reserve 8+ for moments
that are exceptional. Judge only the text given. Do not invent context.

{HOOK_TEXT_RULES}

{SCRIPTED_RULES}

{POLICY_RULES}

Return ONLY a JSON array, one object per candidate, in the order given.
{SCHEMA_DESCRIPTION}\
"""

PROMPT_B_SYSTEM = f"""\
You are a distracted viewer scrolling a short-video feed. For each clip
transcript, decide how quickly you would swipe away and why. Score each on the
rubric using integers 0-10, where 0 means you swipe in one second and 10 means
you watch to the end and send it to a friend. Penalize slow starts, missing
context, rambling, and endings that cut off mid-thought.

{HOOK_TEXT_RULES}

{SCRIPTED_RULES}

{POLICY_RULES}

Return ONLY a JSON array, one object per candidate, in the order given.
{SCHEMA_DESCRIPTION}\
"""

REPAIR_SYSTEM = """\
Your previous reply was not valid JSON matching the required schema. Return ONLY
a JSON array, with no prose, no markdown fences, and no trailing commas. One
object per candidate, in the order given.
"""


@dataclass(frozen=True)
class PromptVariant:
    """One of the two scoring voices."""

    key: str
    system: str

    @property
    def cache_key(self) -> str:
        return f"{PROMPT_VERSION}:{self.key}"


PROMPT_A = PromptVariant("a", PROMPT_A_SYSTEM)
PROMPT_B = PromptVariant("b", PROMPT_B_SYSTEM)
VARIANTS = {"a": PROMPT_A, "b": PROMPT_B}


def build_user_message(
    items: list[tuple[int, float, str]],
    *,
    examples: list[dict] | None = None,
) -> str:
    """Render candidates as the numbered blocks the prompts describe.

    `items` is (index, duration_seconds, transcript). The ``[n]`` prefix is the
    format the mock backend parses, so changing it means changing that too.
    """
    parts: list[str] = []

    if examples:
        parts.append(
            "Here are clips from this account that performed well, for calibration "
            "only. Do not copy their wording.\n"
        )
        for example in examples:
            views = example.get("views")
            suffix = f"  ({views:,} views)" if isinstance(views, int) else ""
            parts.append(f"- \"{example.get('hook', '')}\"{suffix}\n  {example.get('text', '')[:300]}\n")
        parts.append("\nNow score the following candidates.\n")

    for index, duration, text in items:
        parts.append(f"[{index}] ({duration:.0f}s)\n{text.strip()}\n")

    return "\n".join(parts).strip()
