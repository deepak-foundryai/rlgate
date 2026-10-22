from mod import classify


def test_values():
    assert classify(11) == "big"
    assert classify(1) == "small"
