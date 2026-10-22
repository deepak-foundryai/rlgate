import contextlib
import io

import mod


def test_logs():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.classify(5)
    assert "classify called" in buf.getvalue()
