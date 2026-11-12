"""Suite-wide fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    """Proof records (rlgate repro) go to a per-test temp dir, never the repo's ./.rlgate."""
    monkeypatch.setenv("RLGATE_STATE_DIR", str(tmp_path / "rlgate-state"))
    monkeypatch.setenv("RLGATE_JOBS_DIR", str(tmp_path / "rlgate-jobs"))
    # a developer's ~/.config/rlgate/.env (DAYTONA_SNAPSHOT, keys…) must not leak into tests
    monkeypatch.setenv("RLGATE_DOTENV", str(tmp_path / "no-user-dotenv"))
    # nor their ~/.config/rlgate/rlgate.toml, an RLGATE_CONFIG, or a .env found by a past test
    monkeypatch.setenv("RLGATE_HOME", str(tmp_path / "no-user-config"))
    monkeypatch.delenv("RLGATE_CONFIG", raising=False)
    from rlgate import config
    monkeypatch.setattr(config, "_DIRS", [])


@pytest.fixture(autouse=True)
def _no_stop_left_over():
    """A test of Ctrl-C stops the process's sandbox creation; the next test starts fresh."""
    yield
    import sys
    d = sys.modules.get("rlgate.sandbox.daytona")
    if d is not None:
        d._STOPPING.clear()
