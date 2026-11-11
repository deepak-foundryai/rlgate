"""User configuration: where tasks run and which models judge them.

rlgate is installed once and run from anywhere, so settings come in layers, lowest to
highest precedence (each layer changes only the keys it sets):

  1. built-in defaults (below);
  2. the user's ``rlgate.toml`` in ``~/.config/rlgate/`` (``$XDG_CONFIG_HOME/rlgate``, or
     ``$RLGATE_HOME``): models and backend set once for every folder;
  3. the nearest ``rlgate.toml`` from the working directory upward (a project or bank);
  4. the nearest ``rlgate.toml`` from the checked task upward, when that is another file
     (so a bank's settings apply wherever you run from);
  5. ``--config PATH`` or ``$RLGATE_CONFIG``: one file, on top of the user's;
  6. environment variables: ``RLGATE_ENV``, ``RLGATE_MODEL``, ``RLGATE_SECOND_MODEL``,
     ``RLGATE_REVIEW_MODELS`` (comma-separated);
  7. CLI flags: ``--backend`` / ``--backend-opt key=value`` (environment kwargs) / ``--model``
     (``rlgate check --deep-review --model A --model B``: the review panel).

Each ``rlgate.toml`` found may have a ``.env`` beside it; those are read too (config.py).

Secrets never live here: provider keys come from the provider's own variables
(``DAYTONA_API_KEY``, ``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, ``OPENROUTER_API_KEY``,
…), read from the process environment or a local ``.env``.

Example ``rlgate.toml``::

    [environment]
    type = "daytona"            # auto | local | docker | daytona
    cpus = 2
    memory_gb = 4
    auto_stop_minutes = 30

    [checks]
    skip = ["build"]            # question groups (or rule IDs) to leave out
    workers = 4                 # grading runs at a time inside one task
    depth = "fast"              # fast: skip redundant variants | full: every mutation site
    parallel = true             # steps 3-6 at the same time (default: one task on a terminal)
    layout = "mimo"             # bank layout: a preset name, a mapping file, or an adapter

    [thresholds]
    runs = 5                    # reference / do-nothing runs in a full check

    [[waivers]]                 # written by `rlgate waive` (core/waivers.py)
    rule = "RG403"
    reason = "the exact message is part of the spec"

    [models]                    # any LiteLLM model: provider/model
    judge = "anthropic/claude-opus-5-5"
    second_opinion = "openai/<model>"    # a different family, for cross-family votes
    review = ["openai/<model>", "gemini/<model>", "anthropic/claude-opus-5-5"]
                                # the --deep-review review panel; votes take turns through it
                                # (empty: judge, then second_opinion)
    votes = 3                   # independent calls per review question
    author = "openai/<model>"   # the model that wrote the tasks: judges of its family are
                                # left out (a task's own metadata author_model wins)
    budget_usd = 5.0            # stop the LLM review once this much is spent in one run
    min_confidence = 0.5        # judge negatives below this are discarded
    agent = "openai/<model>"    # the L3 agent that writes and runs implementations
                                # (empty: the first review judge)
    agent_turns = 30            # tool-calling turns per L3 audit
    agent_grades = 6            # implementations an L3 audit may grade
    timeout_s = 600
    max_retries = 3

    [models.info."openrouter/moonshotai/kimi-k2"]   # a model LiteLLM has no entry for:
    input_cost_per_token = 0.0000006                # its price (for the estimate and
    output_cost_per_token = 0.0000025               # the budget) and what it supports
    max_input_tokens = 131072                       # (LiteLLM model_info keys, as in
    supports_function_calling = true                # Harbor)
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

ENV_TYPES = ("auto", "local", "docker", "daytona")
CONFIG_NAME = "rlgate.toml"


class SettingsError(ValueError):
    """Invalid configuration; the message says which key and what's allowed."""


@dataclass
class EnvironmentSettings:
    type: str = "auto"
    kwargs: dict = field(default_factory=dict)   # backend-specific (validated by the backend)


@dataclass
class ModelSettings:
    judge: str = "anthropic/claude-opus-5-5"
    second_opinion: str | None = None
    timeout_s: float = 600.0
    max_retries: int = 3
    max_tokens: int = 16000
    review: list[str] = field(default_factory=list)   # --deep-review panel (empty: judge + second)
    votes: int = 3                                    # independent calls per question
    author: str | None = None                         # the tasks' authoring model
    budget_usd: float | None = None                   # LLM spend cap per run
    min_confidence: float = 0.5                       # weaker negatives are discarded
    agent: str | None = None                          # L3 agent (empty: first judge)
    agent_turns: int = 30                             # tool-calling turns per L3 audit
    agent_grades: int = 6                             # candidate gradings per L3 audit
    info: dict = field(default_factory=dict)     # model → LiteLLM model_info (prices, …)
    extra: dict = field(default_factory=dict)    # passed through to litellm (api_base, …)

    def panel(self) -> list[str]:
        """The review models, in vote order, without duplicates."""
        models = self.review or [m for m in (self.judge, self.second_opinion) if m]
        return list(dict.fromkeys(models))


@dataclass
class CheckSettings:
    skip: list[str] = field(default_factory=list)   # group slugs / rule IDs to leave out
    runs: int = 5                                    # [thresholds] runs
    workers: int = 4                                 # grading runs at a time per task
    depth: str = "fast"                              # fast | full (variants graded)
    parallel: bool | None = None                     # None: one task on a terminal
    layout: str | None = None                        # bank layout (as --layout)


@dataclass
class Settings:
    environment: EnvironmentSettings = field(default_factory=EnvironmentSettings)
    models: ModelSettings = field(default_factory=ModelSettings)
    checks: CheckSettings = field(default_factory=CheckSettings)
    source: str = "defaults"                     # the files used, lowest first
    files: list[Path] = field(default_factory=list)


def user_dir() -> Path:
    """Where the user's rlgate.toml and .env live."""
    if os.environ.get("RLGATE_HOME"):
        return Path(os.environ["RLGATE_HOME"]).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "rlgate"


def find_up(start: str | Path) -> Path | None:
    """The nearest rlgate.toml at ``start`` or above it (the user's own file excluded)."""
    p = Path(start).expanduser().resolve()
    if p.is_file():
        p = p.parent
    user = (user_dir() / CONFIG_NAME).resolve()
    for d in (p, *p.parents):
        f = d / CONFIG_NAME
        if f.is_file() and f.resolve() != user:
            return f
    return None


def config_files(config: str | Path | None = None,
                 near: str | Path | None = None) -> list[Path]:
    """The rlgate.toml files that apply, lowest precedence first."""
    config = config or os.environ.get("RLGATE_CONFIG") or None
    if config and not Path(config).expanduser().is_file():
        raise SettingsError(f"config file not found: {config}")
    files: list[Path] = []
    user = user_dir() / CONFIG_NAME
    if user.is_file():
        files.append(user)
    if config:
        files.append(Path(config).expanduser())
    else:
        files += [f for f in (find_up(Path.cwd()), find_up(near) if near else None) if f]
    out: list[Path] = []
    for f in files:
        if f.resolve() not in {x.resolve() for x in out}:
            out.append(f)
    return out


def load(config: str | Path | None = None, *, backend: str | None = None,
         env_kwargs: list[str] | None = None, model: str | None = None,
         near: str | Path | None = None) -> Settings:
    """Settings for a run. ``near``: the task or folder being checked, whose own
    rlgate.toml (or its bank's) applies wherever the command runs from."""
    from rlgate.config import add_dotenv_dir, load_dotenv

    s = Settings()
    files = config_files(config, near)
    for f in files:
        _apply_file(s, f)
        add_dotenv_dir(f.parent)
    load_dotenv()                    # ./.env, then beside each file (nearest first), then user
    s.files = files
    if files:
        s.source = " + ".join(_short(f) for f in files)

    if os.environ.get("RLGATE_ENV"):
        s.environment.type = os.environ["RLGATE_ENV"]
    if os.environ.get("RLGATE_MODEL"):
        s.models.judge = os.environ["RLGATE_MODEL"]
    if os.environ.get("RLGATE_SECOND_MODEL"):
        s.models.second_opinion = os.environ["RLGATE_SECOND_MODEL"]
    if os.environ.get("RLGATE_REVIEW_MODELS"):
        s.models.review = [m.strip() for m in os.environ["RLGATE_REVIEW_MODELS"].split(",")
                           if m.strip()]

    if backend and backend != "auto":
        s.environment.type = backend
    for kv in env_kwargs or []:
        k, v = parse_kv(kv)
        s.environment.kwargs[k] = v
    if model:
        s.models.judge = model

    if s.environment.type not in ENV_TYPES:
        raise SettingsError(f"environment type {s.environment.type!r} is not one of "
                            f"{', '.join(ENV_TYPES)}")
    return s


def _short(p: Path) -> str:
    try:
        rel = p.resolve().relative_to(Path.cwd().resolve())
        return "./" + rel.as_posix()
    except ValueError:
        home = Path.home()
        try:
            return "~/" + p.resolve().relative_to(home).as_posix()
        except ValueError:
            return str(p)


def _apply_file(s: Settings, path: Path) -> None:
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"{path}: invalid TOML: {e}") from None
    unknown = set(data) - SECTIONS
    if unknown:
        raise SettingsError(f"{path}: unknown section(s) {sorted(unknown)}; expected "
                            + ", ".join(f"[{x}]" for x in sorted(SECTIONS)))
    _apply_checks(s.checks, data, path)
    env = dict(data.get("environment", {}))
    if "type" in env:
        s.environment.type = str(env.pop("type"))
    s.environment.kwargs.update(env)

    models = dict(data.get("models", {}))
    known = {f.name for f in fields(ModelSettings)} - {"extra", "info"}
    info = models.pop("info", {})
    s.models.info = {**s.models.info, **info} if isinstance(info, dict) else info
    for k in list(models):
        if k in known:
            setattr(s.models, k, models.pop(k))
    s.models.extra.update(models)   # anything else goes to litellm as-is
    _check_models(s.models, path)


def _check_models(m: ModelSettings, path: Path) -> None:
    if isinstance(m.review, str):
        m.review = [m.review]
    if not isinstance(m.review, list) or not all(isinstance(x, str) and x for x in m.review):
        raise SettingsError(f'{path}: [models] review must be a list of models, e.g. '
                            '["openai/<model>", "gemini/<model>"]')
    if not isinstance(m.votes, int) or isinstance(m.votes, bool) or m.votes < 1:
        raise SettingsError(f"{path}: [models] votes must be a whole number of at least 1")
    if m.budget_usd is not None and (not isinstance(m.budget_usd, (int, float))
                                     or m.budget_usd <= 0):
        raise SettingsError(f"{path}: [models] budget_usd must be a positive number of "
                            "dollars")
    if not isinstance(m.min_confidence, (int, float)) or not 0 <= m.min_confidence <= 1:
        raise SettingsError(f"{path}: [models] min_confidence must be between 0 and 1")
    if not isinstance(m.info, dict) or not all(
            isinstance(k, str) and "/" in k and isinstance(v, dict) for k, v in m.info.items()):
        raise SettingsError(f'{path}: [models.info] needs one table per model, e.g. '
                            '[models.info."openrouter/<maker>/<model>"] with '
                            'input_cost_per_token = 0.000003')
    for k, v in m.info.items():
        for f in ("input_cost_per_token", "output_cost_per_token"):
            if f in v and (not isinstance(v[f], (int, float)) or isinstance(v[f], bool)
                           or v[f] < 0):
                raise SettingsError(f"{path}: [models.info.\"{k}\"] {f} must be a number "
                                    "of dollars per token (0 or more)")
    for k in ("agent_turns", "agent_grades"):
        v = getattr(m, k)
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise SettingsError(f"{path}: [models] {k} must be a whole number of at least 1")


DEPTHS = ("fast", "full")
SECTIONS = {"environment", "models", "checks", "thresholds", "waivers"}


def _apply_checks(c: CheckSettings, data: dict, path: Path) -> None:
    skip = (data.get("checks") or {}).get("skip", [])
    if not isinstance(skip, list) or not all(isinstance(x, str) for x in skip):
        raise SettingsError(f'{path}: [checks] skip must be a list of names, e.g. ["build"]')
    c.skip = list(dict.fromkeys(c.skip + skip))
    checks = data.get("checks") or {}
    if "workers" in checks:
        w = checks["workers"]
        if not isinstance(w, int) or w < 1:
            raise SettingsError(f"{path}: [checks] workers must be a whole number of at least 1")
        c.workers = w
    if "depth" in checks:
        if checks["depth"] not in DEPTHS:
            raise SettingsError(f'{path}: [checks] depth must be "fast" or "full"')
        c.depth = checks["depth"]
    if "parallel" in checks:
        if not isinstance(checks["parallel"], bool):
            raise SettingsError(f"{path}: [checks] parallel must be true or false")
        c.parallel = checks["parallel"]
    if "layout" in checks:
        if not isinstance(checks["layout"], str) or not checks["layout"].strip():
            raise SettingsError(f'{path}: [checks] layout must be a preset name, a mapping '
                                'file or an adapter, e.g. "mimo"')
        lay = checks["layout"].strip()
        rel = path.parent / lay
        c.layout = str(rel) if not Path(lay).is_absolute() and rel.is_file() else lay
    runs = (data.get("thresholds") or {}).get("runs")
    if runs is not None:
        if not isinstance(runs, int) or runs < 1:
            raise SettingsError(f"{path}: [thresholds] runs must be a whole number of at "
                                "least 1")
        c.runs = runs


def task_checks(base: CheckSettings, task_root: Path,
                applied: list[Path] | None = None) -> CheckSettings:
    """``[checks]`` / ``[thresholds]`` from the rlgate.toml next to a task, on top of the
    settings already loaded (``applied``: files already read, not read twice)."""
    c = CheckSettings(list(base.skip), base.runs, base.workers, base.depth, base.parallel,
                      base.layout)
    path = task_root / CONFIG_NAME
    done = {p.resolve() for p in (applied if applied is not None
                                  else [Path(CONFIG_NAME)])}
    if path.is_file() and path.resolve() not in done:
        try:
            data = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as e:
            raise SettingsError(f"{path}: invalid TOML: {e}") from None
        _apply_checks(c, data, path)
    return c


def parse_kv(kv: str) -> tuple[str, object]:
    """``cpus=2`` → ("cpus", 2); values parse as JSON when they can (numbers, booleans,
    lists), else stay strings: ``image=python:3.12`` → ("image", "python:3.12")."""
    if "=" not in kv:
        raise SettingsError(f"expected key=value, got {kv!r}")
    k, v = kv.split("=", 1)
    k = k.strip().replace("-", "_")
    if not k:
        raise SettingsError(f"empty key in {kv!r}")
    try:
        return k, json.loads(v)
    except json.JSONDecodeError:
        return k, v
