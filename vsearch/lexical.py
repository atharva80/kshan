"""Whole-word matching for "the exact words were said / written".

A plain substring match (ILIKE '%cat%') also hits "vacation", "education" and on-screen
"LOCATION", and those hits were scored as perfect matches. These patterns only match the
query's words on word boundaries, for Postgres' case-insensitive regex operator (~*), which
the existing pg_trgm GIN indexes on segment.text and frame.ocr can serve.

Pure (stdlib) so it can be tested without a database; tests/test_lexical_sql.py checks the
patterns against a real Postgres."""
import re

# Word characters. Devanagari vowel signs and the virama are combining marks, which neither
# Python's \\w nor POSIX [:alnum:] count as letters, so without them a boundary would fall
# inside "कुत्ते" and the match would land mid-word. The block's danda and double danda
# (U+0964-0965, Hindi/Marathi full stops) are punctuation, so they stay out.
_MARKS = "̀-ͯऀ-ॣ०-ॿ"
_TOKEN = re.compile(rf"[\w{_MARKS}]+")
_WORD = rf"[:alnum:]_{_MARKS}"
_EDGE_START = rf"(^|[^{_WORD}])"
_EDGE_END = rf"($|[^{_WORD}])"
_SPLIT_DIGITS = re.compile(r"\d+|[^\d]+")


def tokens(query: str, split_digits: bool = False) -> list[str]:
    words = [w.lower() for w in _TOKEN.findall(query)]
    if split_digits:  # "goa2023" -> "goa", "2023"
        words = [part for w in words for part in _SPLIT_DIGITS.findall(w)]
    return words


def pattern(query: str, ocr: bool = False) -> str | None:
    """A Postgres regex matching the query's words as a phrase, on word boundaries.

    Speech: words separated by at least one non-word character.
    OCR: OCR merges words ("GOA2023") and drops spaces, so separators are optional and
    letters/digits split - "goa 2023", "goa2023" and "GOA-2023" all match each other, but
    still only on the outer word boundaries."""
    words = tokens(query, split_digits=ocr)
    if not words:
        return None
    sep = rf"[^{_WORD}]*" if ocr else rf"[^{_WORD}]+"
    return _EDGE_START + sep.join(words) + _EDGE_END
