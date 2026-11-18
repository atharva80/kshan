import re

import pytest

from vsearch import lexical
from vsearch.lexical import pattern, tokens

# what a token may contain: word characters plus the combining-mark ranges lexical.py adds
WORD_ONLY = re.compile("[\\w\u0300-\u036f\u0900-\u097f]+")


class TestTokens:
    def test_lowercased_words(self):
        assert tokens("Happy Birthday, Priya!") == ["happy", "birthday", "priya"]

    def test_devanagari_vowel_signs_and_virama_stay_in_the_word(self):
        # कुत्ते = क + ु + त + ् + त + े : the signs and the virama are combining marks, not \w
        assert tokens("कुत्ते") == ["कुत्ते"]
        assert tokens("चलो आज शाम को कुत्ते के साथ") == ["चलो", "आज", "शाम", "को", "कुत्ते", "के", "साथ"]
        assert tokens("नमस्ते, दोस्तों!") == ["नमस्ते", "दोस्तों"]

    def test_the_plain_word_regex_would_have_split_it(self):
        """Proves the test above means something: without the mark ranges the word falls apart."""
        assert len(re.findall(r"\w+", "कुत्ते")) > 1

    def test_combining_accents(self):
        assert tokens("café au lait") == ["café", "au", "lait"]

    def test_digits_underscore_and_unicode_letters(self):
        assert tokens("goa_2023 São") == ["goa_2023", "são"]

    def test_hyphens_and_punctuation_separate(self):
        assert tokens("goa-2023, (beach)") == ["goa", "2023", "beach"]

    def test_empty_and_punctuation_only(self):
        assert tokens("") == []
        assert tokens("  ") == []
        assert tokens("!?...--") == []

    def test_digits_not_split_by_default(self):
        assert tokens("goa2023") == ["goa2023"]

    def test_split_digits_in_ocr_mode(self):
        assert tokens("goa2023", split_digits=True) == ["goa", "2023"]
        assert tokens("2023goa", split_digits=True) == ["2023", "goa"]
        assert tokens("a1b22c", split_digits=True) == ["a", "1", "b", "22", "c"]
        assert tokens("GOA 2023", split_digits=True) == ["goa", "2023"]

    def test_split_digits_keeps_devanagari_words_whole(self):
        assert tokens("कुत्ते2", split_digits=True) == ["कुत्ते", "2"]

    def test_romanised_hinglish(self):
        assert tokens("Mummy ka birthday") == ["mummy", "ka", "birthday"]


class TestPattern:
    @pytest.mark.parametrize("query", ["", "   ", "!!!", "?", "...", "-- ,, ;;", "()[]{}", "\n\t"])
    def test_none_for_queries_without_any_word(self, query):
        assert pattern(query) is None
        assert pattern(query, ocr=True) is None

    def test_words_are_joined_by_a_required_separator(self):
        sep = f"[^{lexical._WORD}]+"
        assert "happy" + sep + "birthday" in pattern("happy birthday")
        assert pattern("cat").startswith(lexical._EDGE_START) and pattern("cat").endswith(lexical._EDGE_END)

    def test_ocr_separator_is_optional(self):
        sep = f"[^{lexical._WORD}]*"
        assert "goa" + sep + "2023" in pattern("goa 2023", ocr=True)
        assert pattern("goa 2023", ocr=True) == pattern("goa2023", ocr=True) == pattern("GOA-2023", ocr=True)

    def test_speech_does_not_split_digits(self):
        assert pattern("goa2023") != pattern("goa 2023")
        assert "goa2023" in pattern("goa2023")

    def test_case_is_normalised(self):
        assert pattern("Happy BIRTHDAY") == pattern("happy birthday")

    def test_whitespace_and_punctuation_are_normalised(self):
        assert pattern("  happy,   birthday! ") == pattern("happy birthday")

    def test_boundaries_include_the_combining_marks(self):
        # otherwise "कुत्त" would match inside "कुत्ते": the char after it is a mark, so no edge
        p = pattern("कुत्ते")
        assert "\u0900-\u0963" in p and "\u0966-\u097f" in p  # vowel signs + virama: word chars
        assert "\u0964" not in p and "\u0965" not in p  # the danda (full stop) is not
        assert pattern("कुत्त") != pattern("कुत्ते")

    HOSTILE = [".", "*", "+", "?", "(", ")", "[", "]", "{", "}", "|", "^", "$", "\\", "'", '"', ";", "%"]

    @pytest.mark.parametrize("meta", HOSTILE)
    def test_regex_metacharacters_never_reach_the_pattern(self, meta):
        hostile = f"cat{meta}dog"
        assert pattern(hostile) == pattern("cat dog")
        assert pattern(hostile, ocr=True) == pattern("cat dog", ocr=True)

    def test_tokens_only_ever_contain_word_characters(self):
        nasty = ["a.b*c(d)|e[f]\\g^$+?{1,2}", "'; drop table x; --", "c++", "(?:x)", "a\x00b", "%_%", "[[:alpha:]]",
                 "\\b", "(^|x)", "x{9999999}", "a b\tc\nd", "🙂cat🙂", "cat​dog"]
        for q in nasty:
            for split in (False, True):
                for t in tokens(q, split_digits=split):
                    assert WORD_ONLY.fullmatch(t), (q, t)

    def test_query_text_other_than_words_cannot_alter_the_pattern(self):
        assert pattern("(?:cat|dog)*") == pattern("cat dog")
        assert pattern("c++") == pattern("c")
        assert pattern("x{9999999}") == pattern("x 9999999")

    def test_returns_str(self):
        assert isinstance(pattern("cat"), str)
