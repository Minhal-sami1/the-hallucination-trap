"""Build deterministic SRT captions from the saved Fish Audio ASR response.

The ASR word timestamps are the source of truth. This script changes only a
small allow-list of known recognition artifacts, then groups adjacent words
into readable cues. It does not call Fish Audio or any other network service.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_ROOT = REPOSITORY_ROOT / "results" / "demo"

MIN_CUE_MS = 1_800
TARGET_CUE_MS = 3_200
DEFAULT_MAX_CUE_MS = 4_200
PAUSE_BREAK_MS = 450
SENTENCE_MIN_MS = 1_200

LANGUAGE_SETTINGS = {
    "english": {
        "language_code": "en",
        "line_width": 42,
        "max_chars": 82,
        "max_cue_ms": DEFAULT_MAX_CUE_MS,
    },
    "arabic": {
        "language_code": "ar",
        "line_width": 34,
        "max_chars": 64,
        # Fish's Arabic word timing contains longer pauses inside clauses.
        "max_cue_ms": 5_500,
    },
}


@dataclass(frozen=True)
class Word:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class Cue:
    start_ms: int
    end_ms: int
    text: str


def seconds_to_ms(value: Any) -> int:
    """Round a Fish timestamp to the nearest SRT millisecond."""

    milliseconds = Decimal(str(value)) * 1_000
    return int(milliseconds.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def repair_word(language: str, text: str, next_text: str | None) -> str:
    """Apply only reviewed, exact Fish ASR recognition repairs."""

    if language == "english":
        if text == "fabricatedThe":
            return "fabricated. The"
        if text == "check" and next_text == "questions":
            return "checked"
        if text == "90" and next_text == "precision":
            return "90%"
        if text == "8824" and next_text == "recall":
            return "88.24%"
    elif language == "arabic":
        if text in {"المحمي", "المحمل"}:
            return "المحمّل"
        if text == "إجاري":
            return "جار"
        if text == "90" and next_text == "والاستدعاء":
            return "تسعون بالمئة"
        if text == "8842" and next_text == "وصفر":
            return "٨٨٫٢٤ بالمئة"
        if text == "وصفا" and next_text == "بأنه":
            return "وُصف"
    return text


def normalize_asr_text(language: str, text: str) -> str:
    """Apply the same reviewed repairs to the Fish full-transcript field."""

    if language == "english":
        text = text.replace("fabricated.The", "fabricated. The")
        text = re.sub(r"\b50 check questions\b", "50 checked questions", text)
        text = re.sub(r"\b90 precision\b", "90% precision", text)
        text = re.sub(r"\b8824 recall\b", "88.24% recall", text)
    elif language == "arabic":
        text = text.replace("المحمي", "المحمّل")
        text = text.replace("المحمل", "المحمّل")
        text = text.replace("بناء إجاري", "بناء جار")
        text = text.replace("الإجابة تاني", "الإجابتان")
        text = text.replace("موجودة.والبطاقة", "موجودة. والبطاقة")
        text = text.replace("90%", "تسعون بالمئة")
        text = re.sub(r"\b90\b", "تسعون بالمئة", text)
        text = re.sub(r"\b88\.42\b", "٨٨٫٢٤ بالمئة", text)
        text = text.replace("مرجعاً حقيقياً وصفاً بأنه", "مرجعاً حقيقياً وُصف بأنه")
        text = text.replace("لم نجدها في خمسين", "لم نجدها. في خمسين")
        text = text.replace("تقول فقط لم", "تقول فقط: لم")
        text = text.replace("مدققاً الدقة", "مدققاً: الدقة")
        text = text.replace("تسعون بالمئة والاستدعاء", "تسعون بالمئة، والاستدعاء")
        text = text.replace("٨٨٫٢٤ بالمئة وصفر", "٨٨٫٢٤ بالمئة، وصفر")
        text = text.replace("بأنه مختلق الكلام", "بأنه مختلق. الكلام")
        text = text.replace("لا يكفي كل", "لا يكفي. كل")
        if text.endswith("السجل"):
            text += "."
    return " ".join(text.split())


def punctuate_words(words: list[Word], transcript: str, path: Path) -> list[Word]:
    """Restore punctuation already supplied by Fish's full transcript.

    Fish returns plain word tokens in ``segments`` and punctuation in ``text``.
    The token timing remains unchanged; this function only copies punctuation
    from the corresponding full-transcript token.
    """

    transcript_tokens = transcript.split()
    punctuated: list[Word] = []
    cursor = 0
    for index, word in enumerate(words):
        word_tokens = word.text.split()
        tokens = transcript_tokens[cursor : cursor + len(word_tokens)]
        if len(tokens) != len(word_tokens):
            raise ValueError(f"{path}: transcript ends before segment {index}")
        def comparison_text(token: str) -> str:
            without_marks = "".join(
                character
                for character in unicodedata.normalize("NFD", token)
                if unicodedata.category(character) != "Mn"
            )
            return without_marks.rstrip(".,!?،؛؟:")

        plain_word = " ".join(comparison_text(token) for token in word_tokens)
        plain_transcript = " ".join(comparison_text(token) for token in tokens)
        if plain_word != plain_transcript:
            raise ValueError(
                f"{path}: transcript/segment mismatch at token {index}: "
                f"{' '.join(tokens)!r} != {word.text!r}"
            )
        punctuated.append(Word(word.start_ms, word.end_ms, " ".join(tokens)))
        cursor += len(word_tokens)
    if cursor != len(transcript_tokens):
        raise ValueError(
            f"{path}: transcript has {len(transcript_tokens) - cursor} extra tokens"
        )
    return punctuated


def load_words(path: Path, language: str) -> tuple[list[Word], int]:
    """Load and validate one saved Fish ASR response."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_code = LANGUAGE_SETTINGS[language]["language_code"]
    if payload.get("language_code") != expected_code:
        raise ValueError(
            f"{path}: expected language_code={expected_code!r}, "
            f"got {payload.get('language_code')!r}"
        )

    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError(f"{path}: segments must be a non-empty list")

    if language == "arabic":
        merged_segments: list[dict[str, Any]] = []
        index = 0
        while index < len(raw_segments):
            segment = raw_segments[index]
            next_segment = raw_segments[index + 1] if index + 1 < len(raw_segments) else None
            if (
                isinstance(segment, dict)
                and isinstance(next_segment, dict)
                and str(segment.get("text", "")).strip() == "الإجابة"
                and str(next_segment.get("text", "")).strip() == "تاني"
            ):
                merged_segments.append(
                    {
                        **segment,
                        "end": next_segment.get("end"),
                        "text": "الإجابتان",
                    }
                )
                index += 2
                continue
            merged_segments.append(segment)
            index += 1
        raw_segments = merged_segments

    words: list[Word] = []
    previous_start = -1
    carry_prefix: str | None = None
    for index, segment in enumerate(raw_segments):
        if not isinstance(segment, dict):
            raise ValueError(f"{path}: segment {index} must be an object")
        start_ms = seconds_to_ms(segment.get("start"))
        end_ms = seconds_to_ms(segment.get("end"))
        text = str(segment.get("text", "")).strip()
        if start_ms < 0 or end_ms < start_ms:
            raise ValueError(f"{path}: invalid timestamp at segment {index}")
        if start_ms < previous_start:
            raise ValueError(f"{path}: segments are not ordered at segment {index}")
        if not text:
            raise ValueError(f"{path}: empty text at segment {index}")

        next_text = None
        if index + 1 < len(raw_segments):
            next_text = str(raw_segments[index + 1].get("text", "")).strip()
        repaired_text = repair_word(language, text, next_text)
        next_prefix: str | None = None
        if language == "english" and text == "fabricatedThe":
            repaired_text = "fabricated."
            next_prefix = "The"
        elif language == "arabic" and text == "موجودةوالبطاقة":
            repaired_text = "موجودة."
            next_prefix = "والبطاقة"
        if carry_prefix is not None:
            repaired_text = f"{carry_prefix} {repaired_text}"

        words.append(
            Word(
                start_ms=start_ms,
                end_ms=end_ms,
                text=repaired_text,
            )
        )
        carry_prefix = next_prefix
        previous_start = start_ms

    if carry_prefix is not None:
        raise ValueError(f"{path}: repaired transcript has an unused carried token")

    transcript = normalize_asr_text(language, str(payload.get("text", "")))
    words = punctuate_words(words, transcript, path)

    duration_ms = seconds_to_ms(payload.get("duration"))
    if words[-1].end_ms > duration_ms + 10:
        raise ValueError(f"{path}: final word exceeds the declared audio duration")
    return words, duration_ms


def cue_text(words: list[Word]) -> str:
    return " ".join(word.text for word in words)


def group_cues(words: list[Word], language: str) -> list[Cue]:
    """Group ASR words while keeping every cue on original word boundaries."""

    settings = LANGUAGE_SETTINGS[language]
    max_cue_ms = int(settings["max_cue_ms"])
    cues: list[Cue] = []
    pending: list[Word] = []

    def flush() -> None:
        if not pending:
            return
        cue = Cue(
            start_ms=pending[0].start_ms,
            end_ms=pending[-1].end_ms,
            text=cue_text(pending),
        )
        if cue.end_ms <= cue.start_ms:
            raise ValueError(f"zero-duration cue at {cue.start_ms} ms: {cue.text!r}")
        cues.append(cue)
        pending.clear()

    def sentence_ends_within(start_ms: int, start_index: int, limit_ms: int) -> bool:
        for candidate in words[start_index:]:
            if candidate.end_ms - start_ms > limit_ms:
                return False
            if candidate.text.endswith((".", "!", "?", "؟")):
                return True
        return False

    def boundary_ends_within(start_ms: int, start_index: int, limit_ms: int) -> bool:
        for candidate in words[start_index:]:
            if candidate.end_ms - start_ms > limit_ms:
                return False
            if candidate.text.endswith((".", "!", "?", "؟", ",", "،", ";", "؛")):
                return True
        return False

    for index, word in enumerate(words):
        next_word = words[index + 1] if index + 1 < len(words) else None

        current_plain = word.text.rstrip(".,!?،؛؟:")
        if language == "arabic" and pending and current_plain == "لم":
            flush()

        # Keep a metric value with its noun. If both cannot fit, start the pair
        # in the next cue rather than showing "88.24%" and "recall" apart.
        if (
            pending
            and word.text.rstrip(".,!?،؛؟").endswith("%")
            and next_word is not None
            and next_word.text.rstrip(".,!?،؛؟").lower() in {"precision", "recall"}
            and next_word.end_ms - pending[0].start_ms > max_cue_ms
        ):
            flush()

        if pending and word.end_ms - pending[0].start_ms > max_cue_ms:
            flush()

        pending.append(word)
        duration_ms = pending[-1].end_ms - pending[0].start_ms
        text_length = len(cue_text(pending))
        pause_ms = 0 if next_word is None else max(0, next_word.start_ms - word.end_ms)

        reached_target = duration_ms >= TARGET_CUE_MS
        reached_text_limit = text_length >= int(settings["max_chars"])
        natural_pause = pause_ms >= PAUSE_BREAK_MS
        current_plain = pending[-1].text.rstrip(".,!?،؛؟:")
        next_plain = (
            "" if next_word is None else next_word.text.rstrip(".,!?،؛؟:")
        )
        protected_phrase = language == "arabic" and (
            (current_plain in {"المادة", "فالمادة"} and next_plain.isdecimal())
            or (current_plain == "تظهر" and next_plain == "الإجابتان")
            or (current_plain == "وهنا" and next_plain == "الأحمر")
            or (current_plain == "بأنه" and next_plain == "مختلق")
            or (current_plain == "كل" and next_plain == "استشهاد")
            or (current_plain == "لم" and next_plain == "نجدها")
            or (current_plain == "ثمانية" and next_plain == "وثمانون")
        )
        natural_pause = natural_pause and not protected_phrase
        sentence_end = pending[-1].text.endswith((".", "!", "?", "؟"))
        clause_end = pending[-1].text.endswith((",", "،", ";", "؛"))
        readable_duration = duration_ms >= MIN_CUE_MS
        sentence_within_soft_limit = sentence_ends_within(
            pending[0].start_ms, index + 1, DEFAULT_MAX_CUE_MS
        )
        boundary_soon = boundary_ends_within(
            pending[0].start_ms, index + 1, max_cue_ms
        )

        semantic_end = (
            sentence_end and duration_ms >= SENTENCE_MIN_MS
        ) or (
            clause_end
            and duration_ms >= SENTENCE_MIN_MS
            and not sentence_within_soft_limit
        )
        should_flush = (
            next_word is None
            or semantic_end
            or readable_duration
            and (
                not protected_phrase
                and (
                    (reached_text_limit and not boundary_soon)
                    or natural_pause
                    or (reached_target and not boundary_soon)
                )
            )
        )
        if should_flush:
            flush()

    previous_end = -1
    for cue in cues:
        if cue.start_ms < previous_end:
            raise ValueError("generated cues overlap")
        if cue.end_ms - cue.start_ms > max_cue_ms:
            raise ValueError(f"generated cue exceeds {max_cue_ms} ms: {cue!r}")
        previous_end = cue.end_ms
    return cues


def wrap_text(text: str, width: int) -> str:
    """Wrap on spaces without changing, dropping, or reordering words."""

    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and len(candidate) > width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def localize_arabic_numerals(text: str) -> str:
    """Use Arabic-Indic digits so numeric tokens stay stable in RTL captions."""

    text = "".join(
        character
        for character in text
        if not (0x064B <= ord(character) <= 0x065F or ord(character) == 0x0670)
    )
    digit_map = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")

    def replace_number(match: re.Match[str]) -> str:
        return (
            match.group(0)
            .translate(digit_map)
            .replace(".", "٫")
            .replace("%", "٪")
        )

    return re.sub(r"\d+(?:[.]\d+)?%?", replace_number, text)


def format_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def render_srt(cues: list[Cue], language: str) -> str:
    width = int(LANGUAGE_SETTINGS[language]["line_width"])
    blocks = []
    for index, cue in enumerate(cues, start=1):
        text = wrap_text(cue.text, width)
        if language == "arabic":
            text = localize_arabic_numerals(text)
        blocks.append(
            "\n".join(
                (
                    str(index),
                    f"{format_timestamp(cue.start_ms)} --> {format_timestamp(cue.end_ms)}",
                    text,
                )
            )
        )
    return "\n\n".join(blocks) + "\n"


def build_language(results_root: Path, language: str) -> tuple[Path, int, int, int]:
    directory = results_root / language
    source_path = directory / "fish-asr.json"
    output_path = directory / "captions.srt"
    words, audio_duration_ms = load_words(source_path, language)
    cues = group_cues(words, language)
    output_path.write_text(render_srt(cues, language), encoding="utf-8", newline="\n")
    return output_path, len(cues), cues[-1].end_ms, audio_duration_ms


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build deterministic SRT captions from saved Fish Audio ASR JSON."
    )
    parser.add_argument(
        "--language",
        choices=("all", *LANGUAGE_SETTINGS),
        default="all",
        help="Language output to build (default: all).",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=DEFAULT_RESULTS_ROOT,
        help="Directory that contains english/ and arabic/ Fish ASR results.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    languages = LANGUAGE_SETTINGS if args.language == "all" else (args.language,)
    for language in languages:
        path, count, final_cue_ms, audio_duration_ms = build_language(
            args.results_root.resolve(), language
        )
        print(
            f"{language}: {count} cues; final cue {final_cue_ms / 1000:.3f}s; "
            f"audio {audio_duration_ms / 1000:.3f}s; {path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
