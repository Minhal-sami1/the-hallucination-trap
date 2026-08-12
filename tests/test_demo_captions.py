import json
from pathlib import Path

from demo.build_captions import (
    LANGUAGE_SETTINGS,
    Word,
    build_language,
    group_cues,
    load_words,
    localize_arabic_numerals,
    repair_word,
    wrap_text,
)


def write_asr(
    path: Path,
    *,
    language_code: str,
    text: str,
    segment_texts: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    segments = [
        {"start": index * 0.4, "end": (index + 1) * 0.4, "text": token}
        for index, token in enumerate(segment_texts)
    ]
    path.write_text(
        json.dumps(
            {
                "language_code": language_code,
                "duration": len(segments) * 0.4 + 0.2,
                "text": text,
                "segments": segments,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_arabic_caption_uses_rtl_safe_numerals_without_control_characters() -> None:
    caption = localize_arabic_numerals("المادة 1529، والدقة 90% والاستدعاء 88.24%")

    assert caption == "المادة ١٥٢٩، والدقة ٩٠٪ والاستدعاء ٨٨٫٢٤٪"
    assert not any(0x200E <= ord(character) <= 0x2069 for character in caption)


def test_reviewed_arabic_metric_repairs_match_the_spoken_values() -> None:
    assert repair_word("arabic", "90", "والاستدعاء") == "تسعون بالمئة"
    assert repair_word("arabic", "8842", "وصفر") == "٨٨٫٢٤ بالمئة"


def test_new_english_asr_keeps_hyphens_and_metric_punctuation(tmp_path: Path) -> None:
    source = tmp_path / "fish-asr.json"
    write_asr(
        source,
        language_code="en",
        text=(
            "client-ready source-free. 90% citation precision, "
            "88.24% recall."
        ),
        segment_texts=[
            "clientready",
            "sourcefree",
            "90",
            "citation",
            "precision",
            "8824",
            "recall",
        ],
    )

    words, _ = load_words(source, "english")

    assert [word.text for word in words] == [
        "client-ready",
        "source-free.",
        "90%",
        "citation",
        "precision,",
        "88.24%",
        "recall.",
    ]


def test_new_english_asr_splits_fused_sentence_boundary(tmp_path: Path) -> None:
    source = tmp_path / "fish-asr.json"
    write_asr(
        source,
        language_code="en",
        text="That citation cannot exist.The grounded path retrieves Article 821.",
        segment_texts=[
            "That",
            "citation",
            "cannot",
            "existThe",
            "grounded",
            "path",
            "retrieves",
            "Article",
            "821",
        ],
    )

    words, _ = load_words(source, "english")

    assert " ".join(word.text for word in words) == (
        "That citation cannot exist. The grounded path retrieves Article 821."
    )


def test_new_english_asr_restores_reviewed_spoken_phrasing(tmp_path: Path) -> None:
    source = tmp_path / "fish-asr.json"
    write_asr(
        source,
        language_code="en",
        text=(
            "Watch the building collapse. Question: The first answer. "
            "Green shows the law and source with weak evidence. It refuses. "
            "There were no false, fabricated verdicts."
        ),
        segment_texts=[
            "Watch",
            "the",
            "building",
            "collapse",
            "Question",
            "The",
            "first",
            "answer",
            "Green",
            "shows",
            "the",
            "law",
            "and",
            "source",
            "with",
            "weak",
            "evidence",
            "It",
            "refuses",
            "There",
            "were",
            "no",
            "false",
            "fabricated",
            "verdicts",
        ],
    )

    words, _ = load_words(source, "english")

    assert " ".join(word.text for word in words) == (
        "Watch the building-collapse question. The first answer. "
        "Green shows the law and source. With weak evidence, it refuses. "
        "There were no false fabricated verdicts."
    )


def test_new_arabic_asr_splits_fused_sentence_boundary(tmp_path: Path) -> None:
    source = tmp_path / "fish-asr.json"
    write_asr(
        source,
        language_code="ar",
        text=(
            "انتهى عند المادة ألف وأربعمئة واثنين وعشرينالمسار "
            "المسند يسترجع المادة"
        ),
        segment_texts=[
            "انتهى",
            "عند",
            "المادة",
            "ألف",
            "وأربعمئة",
            "واثنين",
            "وعشرينالمسار",
            "المسند",
            "يسترجع",
            "المادة",
        ],
    )

    words, _ = load_words(source, "arabic")

    assert " ".join(word.text for word in words) == (
        "ينتهي عند المادة ألف وأربعمئة واثنين وعشرين. "
        "المسار المسند يسترجع المادة"
    )


def test_new_arabic_asr_restores_reviewed_words_and_punctuation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "fish-asr.json"
    write_asr(
        source,
        language_code="ar",
        text=(
            "بثقة التأكيد يضربها بالأحمر مختلقة قانون انتهى عند المادة. "
            "الخلاصة الكلام المقنع ما يكفي."
        ),
        segment_texts=[
            "بثقة",
            "التأكيد",
            "يضربها",
            "بالأحمر",
            "مختلقة",
            "قانون",
            "انتهى",
            "عند",
            "المادة",
            "الخلاصة",
            "الكلام",
            "المقنع",
            "ما",
            "يكفي",
        ],
    )

    words, _ = load_words(source, "arabic")

    assert " ".join(word.text for word in words) == (
        "بثقة. التدقيق يضربها بالأحمر: مختلقة. قانون ينتهي عند المادة. "
        "الخلاصة: الكلام المقنع ما يكفي."
    )


def test_new_arabic_asr_preserves_the_two_path_explanation(tmp_path: Path) -> None:
    source = tmp_path / "fish-asr.json"
    write_asr(
        source,
        language_code="ar",
        text=(
            "قبل محامي سؤال إماراتي ومسار نموذج بلا مصادر ومسار يبحث. "
            "وإذا الدليل ضعيف يرفض بدلا التخمين."
        ),
        segment_texts=[
            "قبل",
            "محامي",
            "سؤال",
            "إماراتي",
            "ومسار",
            "نموذج",
            "بلا",
            "مصادر",
            "ومسار",
            "يبحث",
            "وإذا",
            "الدليل",
            "ضعيف",
            "يرفض",
            "بدلا",
            "التخمين",
        ],
    )

    words, _ = load_words(source, "arabic")

    assert " ".join(word.text for word in words) == (
        "قبل المحامي. سؤال إماراتي، ومساران: نموذج بلا مصادر، ومسار يبحث. "
        "وإذا الدليل ضعيف، يرفض بدل التخمين."
    )


def test_arabic_cues_never_wrap_to_more_than_two_lines() -> None:
    tokens = [
        "واثنين",
        "وعشرين",
        "المسار",
        "المسند",
        "يسترجع",
        "المادة",
        "ألف",
        "واثنين",
        "وأربعين",
        "ويعرض",
        "النص",
        "الرسمي",
        "ورابطه.",
    ]
    words = [
        Word(index * 400, (index + 1) * 400, token)
        for index, token in enumerate(tokens)
    ]

    cues = group_cues(words, "arabic")
    width = int(LANGUAGE_SETTINGS["arabic"]["line_width"])

    assert cues[0].start_ms == words[0].start_ms
    assert cues[-1].end_ms == words[-1].end_ms
    assert all(
        len(wrap_text(cue.text, width).splitlines()) <= 2 for cue in cues
    )
    assert all(
        current.end_ms <= following.start_ms
        for current, following in zip(cues, cues[1:], strict=False)
    )


def test_english_metric_value_stays_with_citation_precision() -> None:
    tokens = [
        "Compare",
        "the",
        "scoreboards",
        "across",
        "50",
        "checked",
        "questions:",
        "90%",
        "citation",
        "precision,",
    ]
    words = [
        Word(index * 500, (index + 1) * 500, token)
        for index, token in enumerate(tokens)
    ]

    cues = group_cues(words, "english")

    assert any("90% citation precision," in cue.text for cue in cues)


def test_build_language_accepts_external_asr_and_writes_to_results_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input" / "english-fish-asr.json"
    output_root = tmp_path / "output"
    write_asr(
        source,
        language_code="en",
        text="Make every citation face the official record.",
        segment_texts=[
            "Make",
            "every",
            "citation",
            "face",
            "the",
            "official",
            "record",
        ],
    )

    output, cue_count, final_cue_ms, audio_duration_ms = build_language(
        output_root,
        "english",
        source_path=source,
    )

    assert output == output_root / "english" / "captions.srt"
    assert output.is_file()
    assert cue_count >= 1
    assert final_cue_ms == 2_800
    assert audio_duration_ms == 3_000
    assert "official record." in " ".join(
        output.read_text(encoding="utf-8").splitlines()
    )
