"""Native loader for Harbor-format task directories (Terminal-Bench 2, and any benchmark
packaged the same way). No Harbor dependency: the format is read directly.

    <task>/
      task.toml          # [environment] [verifier] [agent] [solution], artifacts
      instruction.md     # the agent's prompt
      environment/       # Dockerfile and/or docker-compose.yaml (+ build context)
      tests/             # test.sh (+ Dockerfile / docker-compose.yaml for a separate grader)
      solution/          # solve.sh (+ any files it uses, e.g. a patch)

What it maps to (generic ``Task`` fields — nothing downstream knows this is Harbor):
  - the workspace is the agent image's workdir → ``Task.workspace_env``; ``repo_dir`` is the
    ``environment/`` build context until a sandbox pulls the real workdir out;
  - grading follows ``[verifier] environment_mode``:
      shared   — tests uploaded to ``/tests`` just before grading, in the agent's environment;
      separate — a fresh grader environment (``[verifier.environment] docker_image``, else
                 ``tests/`` with its own Dockerfile/compose and tests baked in, else a fresh
                 copy of the agent environment + uploaded tests); only ``artifacts`` cross over,
                 after the ``[[verifier.collect]]`` commands have run where the agent worked;
  - the grader is ``/tests/test.sh``; the reward is what it writes to
    ``/logs/verifier/reward.json`` (preferred) or ``reward.txt``;
  - the oracle is ``solution/solve.sh`` run in the workspace; when it just applies a patch
    file that ships beside it, that file is also the gold patch directly.

Shapes this can't run faithfully (multi-step tasks, Windows tasks, artifacts collected from a
sidecar service) are loaded for static checks with the reason in ``meta["unsupported"]``,
and live audits refuse them with that reason rather than produce wrong verdicts.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

import yaml

from rlgate.core.task import EnvSpec, GraderSpec, Patch, ReportSpec, Task

CONFIG = "task.toml"
CONVENTION_ARTIFACTS = "/logs/artifacts"
REWARD_FILES = ("/logs/verifier/reward.json", "/logs/verifier/reward.txt")
_COMPOSE = ("docker-compose.yaml", "docker-compose.yml", "compose.yaml", "compose.yml")
_PATCH_SUFFIXES = (".patch", ".diff")
_LANG_EXT = {".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
             ".jsx": "javascript", ".go": "go", ".rs": "rust", ".java": "java",
             ".kt": "kotlin", ".rb": "ruby", ".c": "c", ".cc": "cpp", ".cpp": "cpp",
             ".cs": "csharp", ".php": "php", ".scala": "scala", ".swift": "swift"}


class HarborFormatError(ValueError):
    pass


def is_task_dir(path: str | Path) -> bool:
    p = Path(path)
    return p.is_dir() and (p / CONFIG).is_file() and (
        (p / "instruction.md").is_file() or (p / "tests").is_dir())


def load_bank(root: str | Path, limit: int | None = None) -> Iterator[Task]:
    """Every Harbor task directly under ``root`` (or ``root/tasks``), or ``root`` itself."""
    root = Path(root)
    if is_task_dir(root):
        yield load(root)
        return
    for base in (root / "tasks", root):
        dirs = sorted(d for d in base.iterdir()
                      if d.is_dir() and not d.name.startswith((".", "_")) and is_task_dir(d)) \
            if base.is_dir() else []
        if dirs:
            for i, d in enumerate(dirs):
                if limit is not None and i >= limit:
                    return
                yield load(d)
            return


def load(task_dir: str | Path) -> Task:
    d = Path(task_dir).resolve()
    if not (d / CONFIG).is_file():
        raise HarborFormatError(f"{d}: no {CONFIG}")
    try:
        cfg = tomllib.loads((d / CONFIG).read_text())
    except tomllib.TOMLDecodeError as e:
        raise HarborFormatError(f"{d / CONFIG}: {e}") from e

    unsupported: list[str] = []
    env_cfg = cfg.get("environment") or {}
    ver_cfg = cfg.get("verifier") or {}
    agent_cfg = cfg.get("agent") or {}
    sol_cfg = cfg.get("solution") or {}

    if cfg.get("steps"):
        unsupported.append("multi-step task ([[steps]]) — rlgate grades single-step tasks")
    if str(env_cfg.get("os", "linux")).lower() == "windows":
        unsupported.append("Windows task — rlgate runs Linux containers only")
    gpus = max(_count(env_cfg.get("gpus")),
               _count((ver_cfg.get("environment") or {}).get("gpus")))
    if gpus:
        unsupported.append(f"needs {gpus} GPU{'s' if gpus > 1 else ''} — rlgate's sandboxes "
                           "have none, so its runs wouldn't show how the task behaves")

    env_dir = d / "environment"
    tests_dir = d / "tests"
    workspace_env = _env_spec(env_cfg, env_dir, unsupported, role="environment")
    if workspace_env is not None:
        workspace_env = _replace(workspace_env, agent_network=_agent_network(env_cfg, agent_cfg))

    mode = ver_cfg.get("environment_mode") or (
        "separate" if ver_cfg.get("environment") else "shared")
    if mode not in ("shared", "separate"):
        raise HarborFormatError(f"{d}: unknown [verifier] environment_mode {mode!r}")

    grader_env: EnvSpec | None = None
    bundled = False
    if mode == "separate":
        v_env_cfg = {**env_cfg, **(ver_cfg.get("environment") or {})}
        if (ver_cfg.get("environment") or {}).get("docker_image"):
            grader_env = _spec_from(v_env_cfg, image=v_env_cfg["docker_image"])
            bundled = True
        elif tests_dir.is_dir() and (_compose_file(tests_dir) or
                                     (tests_dir / "Dockerfile").is_file()):
            grader_env = _env_spec({k: v for k, v in v_env_cfg.items() if k != "docker_image"},
                                   tests_dir, unsupported, role="tests")
            bundled = True
        else:                                    # a fresh copy of the agent environment
            grader_env = workspace_env

    test_script = _first_existing(tests_dir, ("test.sh",))
    if test_script is None and not bundled:
        unsupported.append("no tests/test.sh")

    raw_artifacts = cfg.get("artifacts") or []
    artifacts = _artifacts(raw_artifacts, unsupported)
    if mode == "separate":                   # the convention dir always crosses over
        artifacts = tuple(dict.fromkeys((*artifacts, CONVENTION_ARTIFACTS)))
    if mode == "separate" and not raw_artifacts:
        unsupported.append("separate verifier but no artifacts declared — nothing the agent "
                           "does could reach the grader")

    grader = GraderSpec(
        run="chmod +x /tests/test.sh 2>/dev/null; /tests/test.sh",
        image=str(env_dir) if (env_dir / "Dockerfile").is_file() else env_cfg.get("docker_image"),
        verdict="reward_file",
        reward_files=REWARD_FILES,
        env=grader_env,
        artifacts=artifacts if mode == "separate" else (),
        inject=() if bundled else ((str(tests_dir), "/tests"),),
        collect=_collect(ver_cfg.get("collect") or []),
        timeout_s=int(float(ver_cfg.get("timeout_sec", 600))),
        report=_report_spec(tests_dir),
        env_vars=tuple(_resolve_env(ver_cfg.get("env") or {}).items()),
    )

    solution = d / "solution"
    solve = _first_existing(solution, ("solve.sh",))
    oracle = (str(solution), solve.name) if solve else None
    gold = _gold_patch(solution, solve)

    instruction = (d / "instruction.md").read_text(errors="replace") \
        if (d / "instruction.md").is_file() else None
    pkg = cfg.get("task") or {}
    meta = {
        "format": "harbor",
        "name": pkg.get("name", d.name),
        "verifier_mode": mode,
        "agent_timeout_s": float(agent_cfg.get("timeout_sec") or 3600),
        "solution_env": _resolve_env(sol_cfg.get("env") or {}),
        "metadata": cfg.get("metadata") or {},
        # what task.toml itself declares (None = left to the harness's default)
        "limits": {
            "cpus": env_cfg.get("cpus"),
            "memory": env_cfg.get("memory_mb") or env_cfg.get("memory"),
            "agent timeout": agent_cfg.get("timeout_sec"),
            "verifier timeout": ver_cfg.get("timeout_sec"),
        },
        "config_file": str(d / CONFIG),
    }
    if unsupported:
        meta["unsupported"] = unsupported
    issues = [_sentence(u) for u in unsupported if u.startswith(_DEFECTS) or
              any(k in u for k in _DEFECT_PARTS)] + _format_issues(d, test_script, solve)
    if issues:
        meta["format_issues"] = issues

    return Task(
        id=d.name,
        source="harbor",
        repo_dir=env_dir if env_dir.is_dir() else d,
        grader=grader,
        instruction=instruction,
        gold_patch=Patch(gold) if gold else None,
        languages=frozenset(_languages(env_dir)),
        meta=meta,
        origin=str(d),
        workspace_env=workspace_env,
        oracle=oracle,
    )


# -- environments ------------------------------------------------------------------------


def _env_spec(cfg: dict, directory: Path, unsupported: list[str], role: str) -> EnvSpec | None:
    if cfg.get("docker_image"):                  # Harbor prefers a prebuilt image
        spec = _spec_from(cfg, image=cfg["docker_image"])
        if spec.workdir is None and (directory / "Dockerfile").is_file():
            spec = _replace(spec, workdir=dockerfile_workdir(directory / "Dockerfile"))
        return spec
    compose = _compose_file(directory) if directory.is_dir() else None
    if compose is not None:
        workdir, svc_df = _compose_main(compose, unsupported, role)
        spec = _spec_from(cfg, compose=compose)
        wd = cfg.get("workdir") or workdir or (dockerfile_workdir(svc_df) if svc_df else None)
        return _replace(spec, workdir=wd)
    if (directory / "Dockerfile").is_file():
        spec = _spec_from(cfg, dockerfile=directory / "Dockerfile")
        return _replace(spec, workdir=cfg.get("workdir")
                        or dockerfile_workdir(directory / "Dockerfile"))
    unsupported.append(f"{role}/ has no Dockerfile or docker-compose.yaml and no docker_image")
    return None


_NO_NETWORK = frozenset({"none", "disabled", "no_network", "off", "isolated", "blocked"})


def _network_off(cfg: dict) -> bool | None:
    """True/False when the table turns the network off/on; None when it doesn't say."""
    net = cfg.get("allow_internet")
    if isinstance(net, bool):
        return not net
    mode = str(cfg.get("network_mode") or "").strip().lower().replace("-", "_")
    if not mode:
        return None
    return mode in _NO_NETWORK


def _agent_network(env_cfg: dict, agent_cfg: dict) -> bool:
    """Whether the agent's runs have the network: ``[agent]`` decides, then
    ``[environment]``; Harbor's default is on."""
    for table in (agent_cfg, env_cfg):
        off = _network_off(table)
        if off is not None:
            return not off
    return True


def _spec_from(cfg: dict, **kw) -> EnvSpec:
    network = not _network_off(cfg)
    return EnvSpec(
        workdir=cfg.get("workdir"),
        cpus=float(cfg["cpus"]) if cfg.get("cpus") else None,
        memory_mb=_size_mb(cfg, "memory"),
        storage_mb=_size_mb(cfg, "storage"),
        build_timeout_s=float(cfg.get("build_timeout_sec", 600)),
        network=network,
        **kw,
    )


def _count(v: object) -> int:
    try:
        return max(0, int(v))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def _replace(spec: EnvSpec, **kw) -> EnvSpec:
    import dataclasses
    return dataclasses.replace(spec, **kw)


def _size_mb(cfg: dict, name: str) -> int | None:
    if cfg.get(f"{name}_mb"):
        return int(cfg[f"{name}_mb"])
    raw = cfg.get(name)
    if raw is None:
        return None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kKmMgGtT]?)i?[bB]?\s*", str(raw))
    if not m:
        return None
    mult = {"": 1 / (1024 * 1024), "k": 1 / 1024, "m": 1, "g": 1024, "t": 1024 * 1024}
    return int(float(m.group(1)) * mult[m.group(2).lower()])


def _compose_file(directory: Path) -> Path | None:
    for name in _COMPOSE:
        if (directory / name).is_file():
            return directory / name
    return None


def _compose_main(compose: Path, unsupported: list[str],
                  role: str) -> tuple[str | None, Path | None]:
    """(working_dir of the ``main`` service, the Dockerfile it builds from)."""
    try:
        doc = yaml.safe_load(compose.read_text()) or {}
    except yaml.YAMLError as e:
        unsupported.append(f"{role}/{compose.name} is not valid YAML: {e}")
        return None, None
    main = (doc.get("services") or {}).get("main")
    if not isinstance(main, dict):
        unsupported.append(f"{role}/{compose.name} has no `main` service to run commands in")
        return None, None
    build = main.get("build")
    df: Path | None = None
    if build is None and not main.get("image"):
        df = compose.parent / "Dockerfile"       # the harness's base file builds `main` here
    elif isinstance(build, str):
        df = compose.parent / build / "Dockerfile"
    elif isinstance(build, dict):
        ctx = compose.parent / str(build.get("context", "."))
        df = ctx / str(build.get("dockerfile", "Dockerfile"))
    return main.get("working_dir"), (df if df and df.is_file() else None)


def dockerfile_workdir(dockerfile: Path) -> str | None:
    """The effective WORKDIR at the end of the last build stage (relative WORKDIRs chain;
    ``$VAR``s defined by ENV/ARG in the stage are expanded when known)."""
    wd: PurePosixPath | None = None
    variables: dict[str, str] = {}
    text = re.sub(r"\\\r?\n", " ", dockerfile.read_text(errors="replace"))
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2 or parts[0].startswith("#"):
            continue
        op, arg = parts[0].upper(), parts[1].strip()
        if op == "FROM":
            wd, variables = None, {}
        elif op in ("ENV", "ARG"):
            for k, v in re.findall(r'([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|\S+)', arg):
                variables[k] = v.strip('"')
            single = arg.split(None, 1)
            if op == "ENV" and "=" not in single[0] and len(single) == 2:
                variables[single[0]] = single[1].strip('"')
        elif op == "WORKDIR":
            arg = re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?",
                         lambda m, v=variables: v.get(m.group(1), m.group(0)), arg.strip('"'))
            p = PurePosixPath(arg)
            wd = p if p.is_absolute() else (wd or PurePosixPath("/")) / p
    return str(wd) if wd is not None else None


# -- artifacts, gold, env ----------------------------------------------------------------


def _artifacts(raw: list, unsupported: list[str]) -> tuple[str, ...]:
    out: list[str] = []
    for a in raw:
        if isinstance(a, str):
            src = a
        elif isinstance(a, dict) and a.get("source"):
            src = str(a["source"])
            svc = a.get("service")
            if svc and svc != "main":
                unsupported.append(f"artifact {src} is collected from service {svc!r} — "
                                   "rlgate carries artifacts from the main service only")
                continue
        else:
            continue
        p = str(PurePosixPath(src))
        if not p.startswith("/"):
            unsupported.append(f"artifact {src!r} is not an absolute path")
            continue
        out.append(p)
    return tuple(dict.fromkeys(out))


def _collect(raw: list) -> tuple[tuple[str, int], ...]:
    """``[[verifier.collect]]``: commands the harness runs in the agent's environment once
    the agent is done, before artifacts cross to the verifier (e.g. ``git diff`` into
    ``/logs/artifacts/model.patch``). Without them the verifier sees nothing of the work."""
    out: list[tuple[str, int]] = []
    for c in raw if isinstance(raw, list) else [raw]:
        if isinstance(c, str):
            out.append((c, 300))
        elif isinstance(c, dict) and c.get("command"):
            out.append((str(c["command"]), int(float(c.get("timeout_sec") or 300))))
    return tuple(out)


def _gold_patch(solution: Path, solve: Path | None) -> str | None:
    """A patch file in solution/ that solve.sh actually applies (by name) is the gold
    patch as-is; otherwise the gold is derived by running solve.sh (see sandbox.staged)."""
    if not solution.is_dir() or solve is None:
        return None
    script = solve.read_text(errors="replace")
    for f in sorted(solution.iterdir()):
        if f.is_file() and f.suffix in _PATCH_SUFFIXES and f.name in script:
            text = f.read_text(errors="replace")
            if text.strip():
                return text
    return None


# Entries of ``unsupported`` that are defects in the task itself (the harness would fail on
# them too), as opposed to shapes the harness runs fine but rlgate can't reproduce yet.
_DEFECTS = ("no tests/test.sh", "separate verifier but no artifacts")
_DEFECT_PARTS = ("has no Dockerfile or docker-compose", "is not valid YAML",
                 "has no `main` service", "is not an absolute path")


def _sentence(s: str) -> str:
    s = s.replace(" — ", ": ")
    return (s[0].upper() + s[1:]) if s[:1].isalpha() and not s.startswith(("tests/",
                                                                          "environment/"))\
        else s


def _format_issues(d: Path, test_script: Path | None, solve: Path | None) -> list[str]:
    """Gaps that make a task run differently (or not at all) under its harness, though rlgate
    can still load it. Reported by the compatibility check (RG605)."""
    out = []
    if not (d / "instruction.md").is_file():
        out.append("No instruction.md: the agent gets no task statement")
    if solve is None:
        out.append("No solution/solve.sh: the reference solution can't be run, so nobody "
                   "can show the task is solvable")
    if test_script is not None and not _writes_reward(test_script.parent):
            out.append("Nothing in tests/ writes /logs/verifier/reward.txt or reward.json: "
                       "the harness would score every run as a failure")
    return out


_SCRIPT_EXT = {".sh", ".py", ".js", ".ts", ".rb", ".go", ".pl", ""}


def _writes_reward(tests: Path) -> bool:
    """Whether anything under tests/ writes the reward: a script naming reward.txt /
    reward.json (test.sh often hands off to verify.py), or Harbor's rewardkit (a
    reward.toml, or a script that runs it). Text search only; big files are skipped."""
    if (tests / "reward.toml").is_file():
        return True
    for f in tests.rglob("*"):
        if not f.is_file() or f.suffix not in _SCRIPT_EXT or f.stat().st_size > 2_000_000:
            continue
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        if _REWARD_WRITE.search(text):
            return True
    return False


_REWARD_WRITE = re.compile(r"reward\.(txt|json)|rewardkit")

# a per-test report the test script asks its runner for: pytest --ctrf PATH (Harbor's
# convention, /logs/verifier/ctrf.json) or --junitxml=PATH / --junit-xml PATH
_REPORT_FLAGS = (("ctrf", re.compile(r"--ctrf[= ]+[\"']?(/[^\s\"';]+)")),
                 ("junit", re.compile(r"--junit-?xml[= ]+[\"']?(/[^\s\"';]+)")))


def _report_spec(tests: Path) -> ReportSpec | None:
    """The per-test report the grader writes, when its scripts name one at an absolute path
    (fail-to-pass / pass-to-pass need it; the reward file stays the verdict)."""
    if not tests.is_dir():
        return None
    for f in sorted(tests.rglob("*")):
        if not f.is_file() or f.suffix not in _SCRIPT_EXT or f.stat().st_size > 2_000_000:
            continue
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for fmt, rx in _REPORT_FLAGS:
            m = rx.search(text)
            if m and "$" not in m.group(1):
                return ReportSpec(fmt, m.group(1))
    return None


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _resolve_env(env: dict) -> dict[str, str]:
    """``${VAR}`` / ``${VAR:-default}`` from the host environment, as Harbor does. An
    unset variable without a default resolves to empty (and the grader decides)."""
    return {str(k): _ENV_REF.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""),
                                 str(v)) for k, v in env.items()}


def _first_existing(directory: Path, names: tuple[str, ...]) -> Path | None:
    for n in names:
        if (directory / n).is_file():
            return directory / n
    return None


def _languages(root: Path, budget: int = 4000) -> set[str]:
    """Languages in the build context (bounded walk; dependency trees skipped)."""
    counts: dict[str, int] = {}
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [x for x in dirnames
                       if x not in {"node_modules", ".git", ".venv", "venv", "__pycache__",
                                    "dist", "build", ".next", "vendor"}]
        for f in filenames:
            lang = _LANG_EXT.get(os.path.splitext(f)[1])
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
            seen += 1
            if seen > budget:
                break
        if seen > budget:
            break
    return {lang for lang, n in counts.items() if n >= 2 or len(counts) == 1}
