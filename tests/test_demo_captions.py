from demo.build_captions import localize_arabic_numerals, repair_word


def test_arabic_caption_uses_rtl_safe_numerals_without_control_characters() -> None:
    caption = localize_arabic_numerals("المادة 1529، والدقة 90% والاستدعاء 88.24%")

    assert caption == "المادة ١٥٢٩، والدقة ٩٠٪ والاستدعاء ٨٨٫٢٤٪"
    assert not any(0x200E <= ord(character) <= 0x2069 for character in caption)


def test_reviewed_arabic_metric_repairs_match_the_spoken_values() -> None:
    assert repair_word("arabic", "90", "والاستدعاء") == "تسعون بالمئة"
    assert repair_word("arabic", "8842", "وصفر") == "٨٨٫٢٤ بالمئة"

