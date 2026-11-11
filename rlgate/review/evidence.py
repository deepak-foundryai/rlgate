"""Repository evidence for tested details (L2 second pass, determinability).

A test may check an edge case the statement never mentions and still be fair: the
repository already shows it (an existing error message, a constant, a sibling function that
handles the same case, a documented format). So before a judge calls a tested detail
undeterminable, it is shown where the pre-change repository mentions that detail.

Deterministic, no LLM: the details are the string literals and the names the tests' assertion
lines use; the search is a plain text search of the pre-change repository (never the graded
tests, never the reference fix). Details the statement already names are left out: they are
determinable by definition.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

MAX_TERMS = 60                # tested details looked up
MAX_HITS = 3                  # repository lines shown per detail
MAX_EVIDENCE = 30_000         # characters in the whole section
MAX_SCAN = 40_000_000         # bytes of repository text searched at most
MAX_FILE_SCAN = 1_000_000     # one file larger than this is not searched

_ASSERTISH = re.compile(
    r"\bassert|\bexpect\s*\(|\.should\b|assert[A-Z]\w*\s*\(|\braises\b|\bmatch\s*=|"
    r"\bt\.(?:Error|Fatal)|\brequire\.\w+|\bstatus(?:_code)?\b|==|!=|\btoEqual\b|\btoBe\b")
_STRING = re.compile(r"""(?P<q>["'`])(?P<s>(?:\\.|(?!(?P=q)).){3,80}?)(?P=q)""")
_NAME = re.compile(r"(?:\.|\b)([A-Za-z_][A-Za-z0-9_]{3,60})\s*(?=\(|\b)")
_TEST_DEF = re.compile(
    r"^\s*(?:async\s+)?def\s+(test\w*)|^\s*(?:it|test)\s*\(\s*['\"`]([^'\"`]{1,100})|"
    r"^\s*func\s+(Test\w+)|^\s*fn\s+(test_\w+)", re.MULTILINE)

# names the tests define themselves (variables, helpers, parameters, imports' aliases)
_DEFINED = re.compile(
    r"\b([A-Za-z_]\w*)\s*(?::\s*[\w\[\]., ]+)?=(?!=)|\bdef\s+(?=\w)(\w+)|"
    r"\bfor\s+([A-Za-z_]\w*)\s+in\b|\bas\s+([A-Za-z_]\w*)|\bclass\s+(\w+)|"
    r"\b(?:const|let|var)\s+([A-Za-z_]\w*)")

_PARAMS = re.compile(r"\bdef\s+\w+\s*\(([^)]*)\)")

# names that say nothing about the task: language, test framework, common library calls
_WORDS = """
self cls None True False assert assertEqual assertTrue assertFalse assertIn assertIs
assertIsNone assertIsNotNone assertRaises assertNotEqual assertAlmostEqual assertGreater
assertLess assertCountEqual assertDictEqual assertListEqual expect toBe toEqual toBeTruthy
toBeFalsy toContain toHaveLength toThrow toMatch should equal pytest raises mark fixture
parametrize approx mock patch Mock MagicMock return_value side_effect called assert_called
assert_called_once assert_called_with assert_called_once_with call_args call_count print
len str int float bool list dict set tuple type isinstance hasattr getattr setattr range
sorted reversed enumerate zip map filter open read write json loads dumps keys values items
append extend update get pop strip split join format replace lower upper startswith
endswith encode decode status status_code response request client data text content
headers result results value values error errors message match test tests main setUp
tearDown with from import async await lambda def class pass none null undefined this
const let var function require module exports describe beforeEach afterEach
Error Fatal Errorf Fatalf nil func
"""
_COMMON = set(_WORDS.split())


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    text: str


@dataclass(frozen=True)
class Detail:
    term: str
    tests: tuple[str, ...]        # the tests that check it
    hits: tuple[Hit, ...]         # where the pre-change repository shows it


def details(tests: dict[str, str], statement: str) -> list[tuple[str, tuple[str, ...]]]:
    """Tested details (term, tests that check it) the statement does not name, most
    specific first: string literals before names, then by how many tests check them.
    Left out: values the test supplies itself (a literal also on a non-assertion line of the
    same file is the test's own input) and assertion messages (prose)."""
    found: dict[str, set[str]] = {}
    kind: dict[str, int] = {}
    low = statement.lower()
    given = {_term(s.group("s")) for text in tests.values() for line in text.splitlines()
             if not _ASSERTISH.search(line) for s in _STRING.finditer(line)}
    local = {g for text in tests.values() for m in _DEFINED.finditer(text)
             for g in m.groups() if g}
    local |= {w for text in tests.values() for m in _PARAMS.finditer(text)
              for w in re.findall(r"([A-Za-z_]\w*)\s*(?=[:=,)]|$)", m.group(1))}
    for path, text in tests.items():
        current = ""                       # only lines inside a test count (not harnesses)
        for line in text.splitlines():
            m = _TEST_DEF.match(line)
            if m:
                current = f"{path}::{next(g for g in m.groups() if g)}"
                continue
            if not current or not _ASSERTISH.search(line):
                continue
            for s in _STRING.finditer(line):
                term = _term(s.group("s"))
                if term and _useful_string(term) and term not in given \
                        and term not in _COMMON and term.lower() not in low:
                    found.setdefault(term, set()).add(current)
                    kind[term] = 0
            for n in _NAME.finditer(_STRING.sub(" ", line)):
                term = n.group(1)
                if term in _COMMON or term.lower() in _COMMON or term in local \
                        or term.startswith(("test", "__")) or term.lower() in low \
                        or (term.isupper() and len(term) < 5):
                    continue
                found.setdefault(term, set()).add(current)
                kind.setdefault(term, 1)
    order = sorted(found, key=lambda t: (kind[t], -len(found[t]), t))
    return [(t, tuple(sorted(found[t]))[:3]) for t in order[:MAX_TERMS]]


def _term(s: str) -> str:
    """What to search for: a URL template's last fixed segment (``/api/x/rsvp/{id}`` →
    ``/rsvp``); other templates (f-string pieces) are skipped."""
    s = s.strip()
    if s.startswith("/") and ("{" in s or "<" in s):
        fixed = [p for p in re.split(r"[{<]", s, maxsplit=1)[0].split("/") if p]
        return "/" + fixed[-1] if fixed else ""
    if "{" in s or "[" in s or "]" in s:
        return ""
    return s


def _useful_string(s: str) -> bool:
    if len(s) < 3 or s.isdigit() or "\\" in s and len(s) < 6:
        return False
    if re.fullmatch(r"[\W_]+", s) or s.startswith("__"):
        return False
    if len(s.split()) >= 4:              # prose: an assertion message, not a checked value
        return False
    return not re.fullmatch(r"%\w|\{\}|utf-?8|ascii|rb?|wb?|[rwa]\+?", s, re.IGNORECASE)


def search(root: Path, terms: list[tuple[str, tuple[str, ...]]], *, skip_hashes: set[str],
           files: list[tuple[Path, str]]) -> list[Detail]:
    """Where each term appears in the pre-change repository (case-sensitive, whole text).
    ``files``: (host path, repo-relative path) to search; ``skip_hashes``: contents of the
    graded tests, never searched (a test can't justify itself)."""
    hits: dict[str, list[Hit]] = {t: [] for t, _ in terms}
    pending = set(hits)
    scanned = 0
    for host, rel in files:
        if not pending or scanned > MAX_SCAN:
            break
        try:
            size = host.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_SCAN:
            continue
        try:
            data = host.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:4096]:
            continue
        scanned += len(data)
        text = data.decode(errors="replace")
        if hashlib.sha1(data).hexdigest() in skip_hashes:
            continue
        here = [t for t in pending if t in text]
        if not here:
            continue
        lines = text.splitlines()
        for t in here:
            for i, line in enumerate(lines):
                if t in line:
                    hits[t].append(Hit(rel, i + 1, line.strip()[:200]))
                    if len(hits[t]) >= MAX_HITS:
                        pending.discard(t)
                        break
    return [Detail(t, ts, tuple(hits[t])) for t, ts in terms]


def render(found: list[Detail]) -> str:
    """The prompt section: each tested detail, the tests that check it, and the repository
    lines that show it (or "not found")."""
    if not found:
        return ""
    found = sorted(found, key=lambda d: bool(d.hits))     # the unexplained ones first
    out = [("Details the tests check that the statement does not mention, with the "
            "pre-change repository lines that show them (path:line). \"not found\" means no "
            "file in the pre-change repository contains it.")]
    size = len(out[0])
    for d in found:
        head = f"\n`{d.term}` (checked by {', '.join(d.tests)})"
        body = "\n".join(f"  {h.path}:{h.line}: {h.text}" for h in d.hits) \
            or "  not found in the pre-change repository"
        if size + len(head) + len(body) > MAX_EVIDENCE:
            out.append("\n(more details left out for size)")
            break
        out += [head, body]
        size += len(head) + len(body)
    return "\n".join(out)


def text(found: list[Detail]) -> str:
    """The verbatim repository lines, for checking quotes."""
    return "\n".join(h.text for d in found for h in d.hits)
