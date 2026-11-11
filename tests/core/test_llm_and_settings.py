"""Model layer (via a stubbed litellm — no network) and the settings precedence chain."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rlgate import settings
from rlgate.cli import main
from rlgate.llm import LLM, LLMError, family, same_family, validate

litellm = pytest.importorskip("litellm")

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "confidence"],
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


def _resp(text: str, finish: str = "stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        model="claude-opus-5-5",
    )


@pytest.fixture
def stub(monkeypatch):
    """Replace litellm's network surface. `replies` is consumed in order."""
    state = SimpleNamespace(replies=[], calls=[], native=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def completion(**kw):
        state.calls.append(kw)
        r = state.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr(litellm, "completion_cost", lambda completion_response: 0.001)
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: state.native)
    return state


def _llm(**kw) -> LLM:
    return LLM(model="anthropic/claude-opus-5-5", **kw)


def test_native_structured_output_parses_and_validates(stub):
    stub.replies = [_resp('{"verdict": "fail", "confidence": 0.9}')]
    r = _llm().complete("judge this", schema=SCHEMA)
    assert r.data == {"verdict": "fail", "confidence": 0.9} and r.mode == "native_schema"
    assert stub.calls[0]["response_format"]["json_schema"]["schema"] == SCHEMA
    assert "temperature" not in stub.calls[0]       # current Claude models reject it


def test_code_fences_are_tolerated(stub):
    stub.replies = [_resp('```json\n{"verdict": "pass", "confidence": 1}\n```')]
    assert _llm().complete("x", schema=SCHEMA).data["verdict"] == "pass"


def test_invalid_output_gets_one_repair_round(stub):
    stub.replies = [_resp('{"verdict": "maybe", "confidence": 0.5}'),
                    _resp('{"verdict": "pass", "confidence": 0.5}')]
    r = _llm().complete("x", schema=SCHEMA)
    assert r.data["verdict"] == "pass"
    repair = stub.calls[1]["messages"]
    assert repair[-2]["role"] == "assistant" and "not one of" in repair[-1]["content"]


def test_invalid_twice_fails_closed(stub):
    stub.replies = [_resp("not json"), _resp('{"verdict": "pass"}')]
    with pytest.raises(LLMError) as e:
        _llm().complete("x", schema=SCHEMA)
    assert e.value.kind == "invalid_output"


def test_truncated_reply_is_never_parsed(stub):
    stub.replies = [_resp('{"verdict": "pass", "conf', finish="length")]
    with pytest.raises(LLMError) as e:
        _llm().complete("x", schema=SCHEMA)
    assert e.value.kind == "truncated"


def test_provider_rejecting_native_schema_falls_back_to_prompt(stub):
    bad = litellm.exceptions.BadRequestError("response_format unsupported",
                                             model="m", llm_provider="anthropic")
    stub.replies = [bad, _resp('{"verdict": "fail", "confidence": 0.2}')]
    r = _llm().complete("x", schema=SCHEMA)
    assert r.mode == "prompt_schema" and r.data["verdict"] == "fail"
    assert "response_format" not in stub.calls[1]
    assert "JSON Schema" in stub.calls[1]["messages"][0]["content"]


def test_auth_error_is_unavailable(stub):
    stub.replies = [litellm.exceptions.AuthenticationError("bad key", llm_provider="anthropic",
                                                           model="m")]
    with pytest.raises(LLMError) as e:
        _llm().complete("x")
    assert e.value.kind == "unavailable"


def test_budget_stops_calls(stub):
    stub.replies = [_resp("ok")]
    llm = _llm(budget_usd=0.0005)
    llm.complete("x")
    assert llm.spent_usd == pytest.approx(0.001)
    with pytest.raises(LLMError) as e:
        llm.complete("again")
    assert e.value.kind == "budget"


def test_missing_key_is_reported_before_any_call(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("rlgate.llm.load_dotenv", lambda: None)
    llm = _llm()
    assert "ANTHROPIC_API_KEY" in (llm.check() or "")
    with pytest.raises(LLMError) as e:
        llm.complete("x")
    assert e.value.kind == "unavailable"


def test_model_without_provider_prefix_is_rejected():
    assert "provider prefix" in (LLM(model="claude-opus-5-5").check() or "")


@pytest.mark.parametrize("model, fam", [
    ("anthropic/claude-opus-5-5", "anthropic"),
    ("bedrock/anthropic.claude-opus-5-5", "anthropic"),
    ("vertex_ai/claude-sonnet-5-5", "anthropic"),
    ("openai/o3", "openai"),
    ("azure/gpt-4o", "openai"),
    ("gemini/gemini-2.5-pro", "google"),
    ("ollama/llama3.1", "meta"),
    ("ollama/my-local-model", "ollama"),
    ("openrouter/openai/gpt-4o", "openai"),
    ("openrouter/anthropic/claude-sonnet-4.5", "anthropic"),
    ("openrouter/moonshotai/kimi-k2", "moonshot"),
    ("openrouter/z-ai/glm-4.6", "zhipu"),
    ("openrouter/nousresearch/hermes-4", "nousresearch"),
    ("together_ai/meta-llama/Llama-3.3-70B", "meta"),
])
def test_family(model, fam):
    assert family(model) == fam


def test_cross_family():
    assert same_family("anthropic/claude-opus-5-5", "bedrock/anthropic.claude-haiku-5-5")
    assert not same_family("anthropic/claude-opus-5-5", "openai/o3")


def test_models_behind_a_router_are_not_one_family():
    # Two makers served by OpenRouter must not clash just because they share the router.
    assert not same_family("openrouter/moonshotai/kimi-k2", "openrouter/nousresearch/hermes-4")
    assert same_family("openrouter/openai/gpt-4o", "openai/gpt-4o")


def test_openrouter_key_is_what_check_asks_for(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("rlgate.llm.load_dotenv", lambda: None)
    assert "OPENROUTER_API_KEY" in (LLM(model="openrouter/openai/gpt-4o").check() or "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert LLM(model="openrouter/openai/gpt-4o").check() is None


def test_a_zero_price_litellm_guessed_counts_as_unknown():
    from rlgate.llm import price, register_models
    # LiteLLM answers $0 for some OpenRouter models it has no price for.
    assert price("openrouter/anthropic/claude-sonnet-4", 10_000, 1_500) is None
    assert price("openrouter/rlgate-test/no-such-model", 10_000, 1_500) is None
    register_models({"openrouter/rlgate-test/priced": {
        "input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6,
        "supports_function_calling": True}})
    assert price("openrouter/rlgate-test/priced", 10_000, 1_500) == pytest.approx(0.013)
    assert litellm.supports_function_calling("openrouter/rlgate-test/priced")
    register_models({"ollama/rlgate-test-free": {"input_cost_per_token": 0,
                                                 "output_cost_per_token": 0}})
    assert price("ollama/rlgate-test-free", 10, 10) == 0.0      # free because you said so


def test_model_info_in_the_config(tmp_path):
    cfg = tmp_path / "rlgate.toml"
    cfg.write_text('[models]\njudge = "openrouter/moonshotai/kimi-k2"\n'
                   '[models.info."openrouter/moonshotai/kimi-k2"]\n'
                   'input_cost_per_token = 0.0000006\noutput_cost_per_token = 0.0000025\n')
    s = settings.load(cfg)
    assert s.models.info["openrouter/moonshotai/kimi-k2"]["input_cost_per_token"] == 6e-7
    assert "info" not in s.models.extra                  # never sent to the provider
    cfg.write_text('[models.info."openrouter/x/y"]\ninput_cost_per_token = "cheap"\n')
    with pytest.raises(settings.SettingsError, match="dollars per token"):
        settings.load(cfg)


def test_validator_paths():
    assert validate({"verdict": "pass", "confidence": 2}, SCHEMA).endswith("maximum 1")
    assert "unexpected field" in validate({"verdict": "pass", "confidence": 0, "x": 1}, SCHEMA)
    assert validate({"items": [1, "a"]}, {"type": "object", "properties": {
        "items": {"type": "array", "items": {"type": "integer"}}}}) == \
        "$.items[1]: expected integer, got str"


# -- settings --------------------------------------------------------------------------


def test_settings_precedence(tmp_path, monkeypatch):
    cfg = tmp_path / "rlgate.toml"
    cfg.write_text('[environment]\ntype = "daytona"\ncpus = 2\n\n'
                   '[models]\njudge = "openai/o3"\napi_base = "http://proxy"\n')
    monkeypatch.delenv("RLGATE_MODEL", raising=False)
    s = settings.load(cfg)
    assert (s.environment.type, s.environment.kwargs) == ("daytona", {"cpus": 2})
    assert s.models.judge == "openai/o3" and s.models.extra == {"api_base": "http://proxy"}

    monkeypatch.setenv("RLGATE_MODEL", "gemini/gemini-2.5-pro")
    assert settings.load(cfg).models.judge == "gemini/gemini-2.5-pro"       # env > file
    s = settings.load(cfg, backend="local", env_kwargs=["cpus=4", "image=python:3.12"],
                      model="anthropic/claude-opus-5-5")                     # flags > env
    assert s.environment.type == "local"
    assert s.environment.kwargs == {"cpus": 4, "image": "python:3.12"}
    assert s.models.judge == "anthropic/claude-opus-5-5"


def test_settings_errors(tmp_path):
    bad = tmp_path / "rlgate.toml"
    bad.write_text("[enviroment]\ntype='x'\n")
    with pytest.raises(settings.SettingsError, match="unknown section"):
        settings.load(bad)
    with pytest.raises(settings.SettingsError, match="key=value"):
        settings.parse_kv("cpus")
    with pytest.raises(settings.SettingsError, match="not found"):
        settings.load(tmp_path / "missing.toml")


def test_parse_kv_types():
    assert settings.parse_kv("cpus=2") == ("cpus", 2)
    assert settings.parse_kv("network-block-all=true") == ("network_block_all", True)
    assert settings.parse_kv("image=python:3.12") == ("image", "python:3.12")


def test_doctor_reports_environments_and_models(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("rlgate.llm.load_dotenv", lambda: None)
    main(["doctor"])
    out = capsys.readouterr().out
    assert "Where full checks run" in out and "Daytona" in out and "Models" in out
    assert "ANTHROPIC_API_KEY" in out


def test_settings_file_is_used_by_check_cli(tmp_path, monkeypatch, capsys):
    (tmp_path / "rlgate.toml").write_text('[environment]\ntype = "local"\ncpus = 2\n')
    task = tmp_path / "task"
    (task / "repo").mkdir(parents=True)
    (task / "task.rlgate.yaml").write_text("workspace: ./repo\ngrader:\n  run: \"false\"\n")
    monkeypatch.chdir(tmp_path)
    assert main(["check", str(task), "--runs", "1"]) == 2   # cpus is daytona-only
    assert "only apply to --backend daytona" in capsys.readouterr().out


def test_progress_lines_fit_the_terminal_and_unfinished_is_not_failed(capsys):
    import io

    from rich.console import Console

    from rlgate import ui
    from rlgate.core.probe import CouldNotJudge

    narrow = Console(file=io.StringIO(), width=40)
    assert len(ui._fit("LLM review, reference hidden: do the tests enforce the request",
                       narrow).plain) <= 36
    old_steps, old_quiet = ui._S.steps, ui._S.quiet
    ui._S.steps, ui._S.quiet = True, False
    try:
        with pytest.raises(CouldNotJudge), ui.step("Agent writes wrong implementations"):
            raise CouldNotJudge("the agent stopped at its limit")
    finally:
        ui._S.steps, ui._S.quiet = old_steps, old_quiet
    err = " ".join(capsys.readouterr().err.split())
    assert "! Agent writes wrong implementations" in err
    assert "didn't finish: the agent stopped at its limit" in err
    assert "✗" not in err and "[fail]" not in err
