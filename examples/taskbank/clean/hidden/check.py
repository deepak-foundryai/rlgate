"""Held-out grader: copied in as .graded/ only at grading time, so the agent never sees it.

It checks many inputs (not just the ones in the instruction), pins the result's type (an
object that equals everything fails), and writes the reward to a file only after the checks
ran (an early exit of 0 in the code under test earns nothing)."""

import sys

sys.path.insert(0, ".")

import calc


def ok(fn, want):
    return all(type(fn(x)) is int and fn(x) == want(x) for x in range(-20, 21))


passed = ok(calc.f, lambda x: x + 1) and ok(calc.g, lambda x: x + 2)
with open(".graded/reward.txt", "w") as fh:
    fh.write("1" if passed else "0")
