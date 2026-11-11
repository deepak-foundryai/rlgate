import contextlib
import io

import shop


def test_classify():
    assert shop.classify(11) == "big"
    assert shop.classify(1) == "small"


def test_logs():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        shop.classify(5)
    assert "classify called" in buf.getvalue()


def test_double():
    assert shop.double(2) == 4
