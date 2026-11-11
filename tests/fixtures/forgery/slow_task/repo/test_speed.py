import time

from mod import work


def test_fast_enough():
    t = time.perf_counter()
    assert work() == 1
    assert time.perf_counter() - t < 0.1
