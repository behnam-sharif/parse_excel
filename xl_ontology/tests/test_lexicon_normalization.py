"""F.2 — text normalization tests for ``_match_term`` / ``_normalize_text``.

Without normalization, lexicon term ``"end of life"`` misses cell text
``"end-of-life"`` and term ``"population"`` misses ``"populations"``.
The fix normalizes both sides through one pipeline.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from xl_ontology.tools.lexicon import (
    _match_term,
    _normalize_text,
    _normalised_terms,
)


class TestNormalizeText(unittest.TestCase):
    def test_lowercases(self) -> None:
        self.assertEqual(_normalize_text("Market Share"), "market share")
        self.assertEqual(_normalize_text("BUDGET IMPACT"), "budget impact")

    def test_replaces_dashes_with_space(self) -> None:
        # Hyphen-minus.
        self.assertEqual(_normalize_text("end-of-life"), "end of life")
        # En-dash, em-dash, minus sign.
        self.assertEqual(_normalize_text("end–of–life"), "end of life")
        self.assertEqual(_normalize_text("end—of—life"), "end of life")
        self.assertEqual(_normalize_text("end−of−life"), "end of life")

    def test_strips_punctuation(self) -> None:
        self.assertEqual(_normalize_text("AE rate (annual)"), "ae rate annual")
        self.assertEqual(_normalize_text("cost/dose"), "cost dose")
        self.assertEqual(_normalize_text("with InO."), "with ino")

    def test_collapses_whitespace(self) -> None:
        self.assertEqual(_normalize_text("market   share"), "market share")
        self.assertEqual(_normalize_text("  with   intervention  "), "with intervention")
        self.assertEqual(_normalize_text("tab\there"), "tab here")

    def test_simple_plural_fold_drops_trailing_s(self) -> None:
        # "populations" → "population" for matching.
        self.assertEqual(_normalize_text("populations"), "population")
        self.assertEqual(_normalize_text("rates"), "rate")
        self.assertEqual(_normalize_text("cycles"), "cycle")

    def test_plural_fold_handles_es_suffix(self) -> None:
        # "watches" → "watch", "boxes" → "box".
        self.assertEqual(_normalize_text("watches"), "watch")
        # Note: "boxes" → "box" via -es strip works.
        self.assertEqual(_normalize_text("boxes"), "box")

    def test_plural_fold_handles_ies_suffix(self) -> None:
        # "therapies" → "therapy".
        self.assertEqual(_normalize_text("therapies"), "therapy")
        self.assertEqual(_normalize_text("studies"), "study")

    def test_plural_fold_skips_short_words(self) -> None:
        # "AE" (3 chars after lowercasing → "ae") must NOT lose its 'e'.
        self.assertEqual(_normalize_text("AE"), "ae")
        # "OS" → "os" (short, leave alone).
        self.assertEqual(_normalize_text("OS"), "os")
        # "loss" (ends in ss) → leave alone (don't strip ss).
        self.assertEqual(_normalize_text("loss"), "loss")

    def test_plural_fold_only_last_token(self) -> None:
        # Multi-word phrases: only the last word is folded.
        self.assertEqual(_normalize_text("AE rates"), "ae rate")
        self.assertEqual(_normalize_text("market shares"), "market share")
        # Earlier tokens stay as-is — "rates" in front position is not
        # folded.  Last token "year" is 4 chars (≤ threshold) so also
        # untouched.
        self.assertEqual(_normalize_text("rates per year"), "rates per year")
        # Folding fires only when the LAST token is plural + long enough.
        self.assertEqual(_normalize_text("annual cost rates"), "annual cost rate")

    def test_empty_input(self) -> None:
        self.assertEqual(_normalize_text(""), "")
        self.assertEqual(_normalize_text(None), "")
        self.assertEqual(_normalize_text("   "), "")
        self.assertEqual(_normalize_text("!!!"), "")

    def test_unicode_fold(self) -> None:
        # NFKC fold collapses ligatures + full-width to ascii.
        self.assertEqual(_normalize_text("ﬁne"), "fine")  # ﬁne ligature
        self.assertEqual(
            _normalize_text("ＡＢＣ"), "abc"  # full-width A B C
        )


class TestMatchTermNormalized(unittest.TestCase):
    """Both text + terms go through normalization before substring check."""

    def test_dash_in_text_matches_space_term(self) -> None:
        terms = _normalised_terms(["end of life"])
        self.assertEqual(_match_term("end-of-life cost", terms), "end of life")

    def test_dash_in_term_matches_space_text(self) -> None:
        terms = _normalised_terms(["follow-up"])
        # Term and text both normalize → both lose the dash.
        self.assertEqual(_match_term("follow up rate", terms), "follow up")

    def test_case_insensitive_match(self) -> None:
        terms = _normalised_terms(["Budget Impact"])
        self.assertEqual(_match_term("BUDGET IMPACT", terms), "budget impact")

    def test_plural_in_text_matches_singular_term(self) -> None:
        terms = _normalised_terms(["population"])
        # Cell text "patient populations" → "patient population" → match.
        self.assertEqual(
            _match_term("patient populations", terms), "population"
        )

    def test_punctuation_in_text_does_not_block_match(self) -> None:
        terms = _normalised_terms(["AE rate"])
        # "ae rate (annual)" → "ae rate annual" → match.
        self.assertIsNotNone(_match_term("AE rate (annual)", terms))

    def test_no_match_returns_none(self) -> None:
        terms = _normalised_terms(["population"])
        self.assertIsNone(_match_term("drug cost summary", terms))

    def test_normalised_terms_dedupes(self) -> None:
        # Three spellings collapse to one normalised form.
        out = _normalised_terms([
            "end-of-life", "End of Life", "end of life",
        ])
        self.assertEqual(out, ["end of life"])


if __name__ == "__main__":
    unittest.main()
