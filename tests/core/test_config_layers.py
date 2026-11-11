"""rlgate runs from anywhere: user → project (from cwd) → task's own → --config layers, and
the .env beside each file."""

from __future__ import annotations

import os

import pytest

from rlgate import settings


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "user"
    h.mkdir()
    monkeypatch.setenv("RLGATE_HOME", str(h))
    monkeypatch.setenv("RLGATE_DOTENV", str(h / ".env"))
    return h


def test_user_config_applies_from_any_folder(home, tmp_path, monkeypatch):
    (home / "rlgate.toml").write_text('[models]\nreview = ["openrouter/deepseek/x"]\n')
    elsewhere = tmp_path / "somewhere" / "deep"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    s = settings.load()
    assert s.models.review == ["openrouter/deepseek/x"]
    assert s.files == [home / "rlgate.toml"]


def test_project_found_upward_overrides_user_key_by_key(home, tmp_path, monkeypatch):
    (home / "rlgate.toml").write_text('[models]\nreview = ["a/x"]\nbudget_usd = 9.0\n'
                                      '[models.info."a/x"]\ninput_cost_per_token = 1e-6\n')
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / "rlgate.toml").write_text('[models]\nbudget_usd = 1.0\n'
                                      '[models.info."b/y"]\ninput_cost_per_token = 2e-6\n')
    monkeypatch.chdir(proj / "sub")
    s = settings.load()
    assert s.models.review == ["a/x"]                 # from the user's file
    assert s.models.budget_usd == 1.0                 # the project wins
    assert set(s.models.info) == {"a/x", "b/y"}       # model info merges


def test_the_checked_tasks_bank_config_applies_wherever_you_run(home, tmp_path,
                                                                 monkeypatch):
    bank = tmp_path / "bank"
    (bank / "task-1").mkdir(parents=True)
    (bank / "rlgate.toml").write_text('[models]\nagent = "openrouter/deepseek/x"\n')
    monkeypatch.chdir(tmp_path)
    assert settings.load().models.agent is None
    assert settings.load(near=bank / "task-1").models.agent == "openrouter/deepseek/x"


def test_explicit_config_replaces_the_search(home, tmp_path, monkeypatch):
    (tmp_path / "rlgate.toml").write_text('[models]\nagent = "a/found"\n')
    other = tmp_path / "ci.toml"
    other.write_text('[models]\nagent = "a/explicit"\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RLGATE_CONFIG", str(other))
    assert settings.load().models.agent == "a/explicit"
    monkeypatch.setenv("RLGATE_CONFIG", str(tmp_path / "missing.toml"))
    with pytest.raises(settings.SettingsError, match="not found"):
        settings.load()


def test_env_beside_the_nearest_config_wins_over_the_users(home, tmp_path, monkeypatch):
    (home / ".env").write_text("RLGATE_TEST_KEY=user\nRLGATE_TEST_ONLY_USER=u\n")
    (home / "rlgate.toml").write_text("")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "rlgate.toml").write_text("")
    (proj / ".env").write_text("RLGATE_TEST_KEY=project")
    monkeypatch.delenv("RLGATE_TEST_KEY", raising=False)
    monkeypatch.delenv("RLGATE_TEST_ONLY_USER", raising=False)
    monkeypatch.chdir(tmp_path)
    try:
        settings.load(near=proj)
        assert os.environ["RLGATE_TEST_KEY"] == "project"
        assert os.environ["RLGATE_TEST_ONLY_USER"] == "u"
    finally:
        os.environ.pop("RLGATE_TEST_KEY", None)
        os.environ.pop("RLGATE_TEST_ONLY_USER", None)


def test_init_user_writes_the_user_config(home):
    from rlgate.cli import main
    assert main(["init", "--user"]) == 0
    assert "[models]" in (home / "rlgate.toml").read_text()
    assert main(["init", "--user"]) == 2               # never overwrites
