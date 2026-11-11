"""Making a judge's negative valid.

A negative is kept only when:

  - its confidence reaches ``min_confidence``;
  - its quote exists verbatim in the file it says it comes from (whitespace aside; ``...``
    may stand for skipped text) — otherwise the judge made it up, and it is discarded;
  - the test it names exists in the test files, when it names one (and it must name one for
    the checks that are about a test);
  - its gap names something concrete (an identifier, a value, a quoted string, a word from
    the quote), not general wording like "could be clearer".

Every discarded negative is counted with its reason, so a report can show how many there
were and why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rlgate.review.bundle import Bundle

HALLUCINATED, NO_TEST, VAGUE, UNSURE = ("quote not found in the files",
                                        "named test not found", "gap too vague",
                                        "low confidence")

_WS = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…)\s*")
_VAGUE = re.compile(r"\b(could|should|might|may)\s+be\s+(clearer|more\s+\w+|improved|better)"
                    r"|\b(more|additional)\s+(detail|details|clarity|context|specific\w*)\b"
                    r"|\b(unclear|ambiguous|vague|not\s+clear|underspecified)\b", re.IGNORECASE)
_CODEISH = re.compile(r"`[^`]+`|\"[^\"]{2,}\"|'[^']{2,}'|\b\w+[_.(]\w*|\b[a-z]+[A-Z]\w*"
                      r"|\b\d[\d.]*\b|/[\w/.-]+")
_STOP = {"which", "their", "there", "these", "those", "should", "would", "could", "every",
         "about", "where", "while", "after", "before", "other", "being", "might", "under",
         "statement", "requirement", "requirements", "behaviour", "behavior", "solver",
         "tests", "test", "assertion", "implementation", "correct", "expected", "specified"}


@dataclass
class Negative:
    """One problem a judge reports, in the shape every judge returns."""
    check: str                # the criterion or question it answers
    gap: str                  # the specific problem / spec gap
    quote: str                # the verbatim text it cites
    source: str               # statement | tests | verifier | reference | repo
    test: str = ""            # the test it is about ("" when none)
    confidence: float = 0.0
    fix: str = ""             # what to change (first pass) or the cheat (weak pass)
    model: str = ""

    @property
    def key(self) -> tuple[str, str]:
        """Same problem from different votes: same check and the same quote from the same
        place. The test name isn't part of it: judges write it differently for one gap
        ("test_x", "tests/a.py::test_x (and others)")."""
        return (self.check, self.source + "|" + _norm(self.quote)[:200])


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip().lower()


def _strip(q: str) -> str:
    q = q.strip()
    while len(q) >= 2 and q[0] == q[-1] and q[0] in "\"'`":
        q = q[1:-1].strip()
    return q


def quoted(quote: str, text: str) -> bool:
    """The quote occurs in ``text`` (whitespace and case of spacing aside); ``...`` splits
    it into pieces that must occur in order."""
    q = _strip(quote)
    if len(q) < 3:
        return False
    hay = _WS.sub(" ", text)
    pos = 0
    pieces = [p for p in _ELLIPSIS.split(q) if p.strip()]
    if not pieces:
        return False
    for piece in pieces:
        piece = _WS.sub(" ", piece).strip()
        if len(pieces) > 1 and len(piece) < 6:
            continue
        i = hay.find(piece, pos)
        if i < 0:
            return False
        pos = i + len(piece)
    return True


def names_a_test(test: str, tests: str) -> bool:
    """``tests/test_x.py::TestA::test_b`` / ``test_b`` / ``TestA.test_b`` names a test that
    is defined (or, for other languages, named) in the test files."""
    name = re.split(r"::|\.|\s|\(", test.strip().rstrip("()"))
    name = [n for n in name if n and not n.endswith(("py", "js", "ts", "go"))]
    if not name:
        return False
    last = name[-1]
    blob = tests
    return re.search(r"(?<![\w])" + re.escape(last) + r"(?![\w])", blob) is not None


def concrete(gap: str, quote: str) -> bool:
    gap = gap.strip()
    if len(gap.split()) < 4:
        return False
    if _CODEISH.search(gap):
        return True
    words = {w.lower() for w in re.findall(r"[A-Za-z]{5,}", quote)} - _STOP
    shares = any(w.lower() in words for w in re.findall(r"[A-Za-z]{5,}", gap))
    return shares and not (_VAGUE.search(gap) and len(gap.split()) < 12)


def why_invalid(n: Negative, bundle: Bundle, *, min_confidence: float,
                needs_test: bool) -> str | None:
    """None when the negative is valid, else the reason it is discarded."""
    if n.confidence < min_confidence:
        return UNSURE
    if not quoted(n.quote, bundle.source(n.source)):
        return HALLUCINATED
    if n.test and not names_a_test(n.test, bundle.source("tests")):
        return NO_TEST
    if needs_test and not n.test:
        return NO_TEST
    if not concrete(n.gap, n.quote):
        return VAGUE
    return None
