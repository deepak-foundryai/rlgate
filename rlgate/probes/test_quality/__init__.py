"""Test-quality (B-series) probes.

Deterministic coverage:
  - test_quality.overfit_equivalence (CONFIRMED): vary a spec-free incidental, a broken grade
    proves an over-fit test — the execution answer to instruction↔test alignment.
  - test_quality.symbol_crossref (REVIEW): a graded test references a gold-internal symbol the
    instruction never names (static over-fit proxy).

DEFERRED to the LLM layer (not decidable deterministically): true semantic alignment — whether
a given test actually verifies a given instruction requirement — and disclaimed-text checks
(a test asserting behavior the instruction explicitly says to ignore).
"""
