"""Suite-wide fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    """Proof records (rlgate repro) go to a per-test temp dir, never the repo's ./.rlgate."""
    monkeypatch.setenv("RLGATE_STATE_DIR", str(tmp_path / "rlgate-state"))
    # a developer's ~/.config/rlgate/.env (DAYTONA_SNAPSHOT, keys…) must not leak into tests
    monkeypatch.setenv("RLGATE_DOTENV", str(tmp_path / "no-user-dotenv"))
    # nor their ~/.config/rlgate/rlgate.toml, an RLGATE_CONFIG, or a .env found by a past test
    monkeypatch.setenv("RLGATE_HOME", str(tmp_path / "no-user-config"))
    monkeypatch.delenv("RLGATE_CONFIG", raising=False)
    from rlgate import config
    monkeypatch.setattr(config, "_DIRS", [])
