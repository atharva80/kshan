"""lexical.pattern() against a real Postgres, since the point of the patterns is how Postgres'
regex engine (~*) reads them: POSIX classes, case folding, Devanagari combining marks.

Set VSEARCH_TEST_DSN to a throwaway database, e.g.
    docker run -d --rm -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=test pgvector/pgvector:pg16
    VSEARCH_TEST_DSN=postgresql://postgres:test@127.0.0.1:55432/postgres pytest tests/test_lexical_sql.py
"""
import os

import pytest

from vsearch import lexical

DSN = os.environ.get("VSEARCH_TEST_DSN")
if not DSN:
    pytest.skip("VSEARCH_TEST_DSN is not set (needs a throwaway Postgres to run the ~* pattern tests)",
                allow_module_level=True)
psycopg = pytest.importorskip("psycopg", reason="psycopg 3 is not installed (pip install 'psycopg[binary]')")


@pytest.fixture(scope="module")
def conn():
    with psycopg.connect(DSN, autocommit=True) as c:
        yield c


def matches(conn, query: str, text: str, ocr: bool = False) -> bool:
    pat = lexical.pattern(query, ocr=ocr)
    assert pat is not None
    return conn.execute("select %s ~* %s", (text, pat)).fetchone()[0]


# (query, ocr, text, should it match)
CASES = [
    # whole words only
    ("cat", False, "a cat on the sofa", True),
    ("cat", False, "the cat.", True),
    ("cat", False, "Cat", True),
    ("cat", False, "CAT!", True),
    ("cat", False, "cat", True),
    ("cat", False, "on vacation", False),
    ("cat", False, "education", False),
    ("cat", False, "Cats!", False),
    ("cat", False, "concat", False),
    ("cat", True, "LOCATION: PUNE", False),
    ("cat", True, "CAT", True),
    ("cat", True, "Cats!", False),
    ("park", False, "the parking lot", False),
    ("park", False, "meet at the park", True),
    ("park", False, "Central-Park", True),
    # phrases
    ("happy birthday", False, "Happy birthday, Priya!", True),
    ("happy birthday", False, "HAPPY   BIRTHDAY", True),
    ("happy birthday", False, "happybirthday", False),
    ("happy birthday", False, "happy and birthday", False),
    ("happy birthday", False, "unhappy birthday", False),
    ("happy birthday", False, "happy birthdays", False),
    # OCR merges and splits words
    ("goa 2023", True, "GOA2023", True),
    ("goa2023", True, "GOA 2023", True),
    ("goa 2023", True, "GOA 2023", True),
    ("goa 2023", True, "GOA-2023", True),
    ("goa 2023", True, "TRIP TO GOA2023!", True),
    ("goa 2023", True, "XGOA2023", False),
    ("goa 2023", True, "GOA20234", False),
    ("goa 2023", True, "GOA 2023X", False),
    # ...but speech needs the separator
    ("goa 2023", False, "GOA2023", False),
    ("goa 2023", False, "goa 2023", True),
    # Devanagari: the word ends at the last vowel sign, not mid-conjunct
    ("कुत्ते", False, "चलो आज शाम को कुत्ते के साथ", True),
    ("कुत्त", False, "चलो आज शाम को कुत्ते के साथ", False),
    ("कुत्ते", False, "कुत्ते", True),
    # a word right before a Hindi/Marathi full stop (danda) is still a whole word
    ("कुत्ते", False, "कुत्ते।", True),
    ("कुत्ते", False, "कुत्ते॥", True),
    ("कुत्ते", False, "सारे कुत्तेवाले", False),
    ("कुत्ते के साथ", False, "चलो आज शाम को कुत्ते के साथ", True),
    ("साथ", False, "चलो आज शाम को कुत्ते के साथ", True),
    ("कुत्ते", True, "कुत्ते", True),
    ("कुत्त", True, "कुत्ते", False),
    # romanised Hinglish
    ("mummy ka birthday", False, "mummy ka birthday 2023", True),
    ("mummy ka birthday", False, "Mummy ka Birthday!", True),
    ("mummy ka birthday", False, "mummy ka  birthday", True),
    ("mummy ka birthday", False, "mummy ke birthday", False),
    # nothing from the query is interpreted as regex
    ("a.b", False, "axb", False),
    ("a.b", False, "a b", True),
    ("c++", False, "I like c++ a lot", True),
    ("(cat|dog)", False, "a dog", False),
    ("(cat|dog)", False, "cat dog", True),
    ("cat*", False, "caaat", False),
    ("'; drop table x; --", False, "drop table x", True),
    ("100%", False, "100 percent", True),
    ("a_b", False, "a b", False),
    ("a_b", False, "a_b", True),
    # accents and case in non-ASCII Latin
    ("São Paulo", False, "visiting são paulo today", True),
    ("São Paulo", False, "SÃO PAULO", True),
    ("café", False, "the café opens", True),
    ("café", False, "cafés", False),
]


@pytest.mark.parametrize("query, ocr, text, expected", CASES,
                         ids=[f"{'ocr' if c[1] else 'speech'}-{c[0]!r}-{c[2]!r}" for c in
                              (getattr(c, "values", c) for c in CASES)])
def test_pattern_in_postgres(conn, query, ocr, text, expected):
    assert matches(conn, query, text, ocr) is expected


def test_required_cases_from_the_spec(conn):
    """The cases the lexical refactor was written for, spelled out."""
    assert not matches(conn, "cat", "on vacation")
    assert not matches(conn, "cat", "LOCATION: PUNE", ocr=True)
    assert not matches(conn, "cat", "Cats!")
    assert matches(conn, "cat", "a cat on the sofa")
    assert matches(conn, "cat", "the cat.")
    assert matches(conn, "goa 2023", "GOA2023", ocr=True)
    assert matches(conn, "goa2023", "GOA 2023", ocr=True)
    assert not matches(conn, "goa 2023", "XGOA2023", ocr=True)
    assert matches(conn, "happy birthday", "Happy birthday, Priya!")
    assert matches(conn, "कुत्ते", "चलो आज शाम को कुत्ते के साथ")
    assert not matches(conn, "कुत्त", "चलो आज शाम को कुत्ते के साथ")
    assert not matches(conn, "park", "the parking lot")
    assert matches(conn, "mummy ka birthday", "mummy ka birthday 2023")


def test_pattern_is_valid_postgres_regex_for_odd_queries(conn):
    for q in ["a", "x{9999999}", "a\\b", "[[:alpha:]]", "🙂 cat", "  spaced   out  ", "a" * 200]:
        pat = lexical.pattern(q)
        assert pat is not None
        conn.execute("select 'x' ~* %s", (pat,)).fetchone()  # raises on an invalid regex


def test_trigram_gin_index_serves_the_regex_query(conn):
    """The same pattern runs against a column with a pg_trgm GIN index (segment.text, frame.ocr
    are indexed like this in production) and returns the same rows as a sequential scan."""
    conn.execute("create extension if not exists pg_trgm")
    conn.execute("create temp table lexical_idx_test (id int, txt text)")
    try:
        rows = [(1, "a cat on the sofa"), (2, "on vacation"), (3, "LOCATION: PUNE"), (4, "Cats!"),
                (5, "the cat."), (6, "चलो आज शाम को कुत्ते के साथ"), (7, "GOA2023 trip")]
        rows += [(100 + i, f"filler text number {i} with words") for i in range(300)]
        with conn.cursor() as cur:
            cur.executemany("insert into lexical_idx_test values (%s, %s)", rows)
        conn.execute("create index on lexical_idx_test using gin (txt gin_trgm_ops)")
        conn.execute("analyze lexical_idx_test")

        for query, ocr, expected in [("cat", False, {1, 5}), ("cat", True, {1, 5}),
                                     ("कुत्ते", False, {6}), ("goa 2023", True, {7}), ("nope", False, set())]:
            sql = "select id from lexical_idx_test where txt ~* %s"
            pat = lexical.pattern(query, ocr=ocr)
            seq = {r[0] for r in _run(conn, sql, pat, indexed=False)}
            idx = {r[0] for r in _run(conn, sql, pat, indexed=True)}
            assert seq == idx == expected, (query, ocr)
    finally:
        conn.execute("drop table if exists lexical_idx_test")


def _run(conn, sql, pat, indexed: bool):
    conn.execute("set enable_seqscan = %s" % ("off" if indexed else "on"))
    conn.execute("set enable_bitmapscan = %s" % ("on" if indexed else "off"))
    try:
        return conn.execute(sql, (pat,)).fetchall()
    finally:
        conn.execute("reset enable_seqscan")
        conn.execute("reset enable_bitmapscan")
