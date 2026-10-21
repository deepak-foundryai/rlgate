# rlgate — Architecture & Codebase Structure

Design goals, in priority order:

1. **One check = one file.** Every probe is a self-contained module in a family folder,
   auto-discovered — no central list to edit, no merge conflicts between contributors.
2. **Everything pluggable, not just probes.** Mutators, adapters, sandbox backends, and
   report parsers all use the *same* registry pattern, so the engine extends in every
   direction.
3. **Every contribution ships with a fixture.** A probe PR includes a known-bad task it fires
   on *and* a clean task it must stay silent on. CI runs them. This is what keeps an
   open-source probe bank trustworthy as it grows.
4. **Honest metadata on every probe.** Each probe declares whether it's `GENERIC` (free with
   the engine) or `PATTERN` (per-grader library), whether it `needs_live`, and its verdict
   tier — so reports never overclaim (see PLAN.md §3.6–§3.8, §7 caveats).

Stack: **Python 3.12+**, `uv`, `ruff` + `pyright`, `pytest`. MIT-licensed core.

---

## 1. Directory tree

```
rlgate/
├── pyproject.toml              # deps, entry points, tool config
├── README.md
├── PLAN.md                     # the methodology (already written)
├── ARCHITECTURE.md             # this file
├── CONTRIBUTING.md             # how to add a probe / adapter (the 2-file flow)
├── LICENSE                     # MIT
├── .github/
│   └── workflows/ci.yml        # lint + type + unit + fixture gate
│
├── src/rlgate/
│   ├── __init__.py
│   ├── cli.py                  # `rlgate audit <task>`, `list-probes`, `explain`
│   ├── config.py               # TOML/env config, profiles (fast / full / ci)
│   ├── pipeline.py             # orchestration: adapt → baseline → probes → report → gate
│   │
│   ├── core/                   # stable contracts — changes here are breaking, reviewed hard
│   │   ├── task.py             # Task: the normalized env schema
│   │   ├── finding.py          # Finding, Severity, VerdictTier, Evidence, Replay
│   │   ├── probe.py            # Probe ABC + ProbeMeta (family, kind, needs_live, ...)
│   │   ├── context.py          # ProbeContext: sandbox handle, baseline, shared caches
│   │   ├── registry.py         # @register + discovery (scan + entry points)
│   │   └── report.py           # EQS, KEEP/FIX/DROP triage, JSON + Markdown renderers
│   │
│   ├── sandbox/                # execution substrate (pluggable backend)
│   │   ├── base.py             # Sandbox ABC: build, snapshot, overlay, exec, perms, pkill
│   │   ├── docker.py
│   │   ├── daytona.py
│   │   ├── local.py            # dev-only, no isolation guarantees
│   │   └── parsers/            # native report parsers (pluggable)
│   │       ├── base.py         # ReportParser ABC -> list[TestResult]
│   │       ├── junit.py        # Python / generic
│   │       ├── trx.py          # C# / .NET
│   │       ├── vitest_json.py  # TypeScript
│   │       ├── go_json.py      # Go `-json`
│   │       └── cpp_exit.py     # C++ exit-code corpus
│   │
│   ├── baseline/               # Phase B — runs once per task, feeds every probe
│   │   ├── invariant.py        # oracle==1.0 / nop==0.0 @ k=5, flaky detection
│   │   └── pertest.py          # per-test capture → test→region map (for subset execution)
│   │
│   ├── variants/               # §3.4 machinery, shared by grader-strength probes
│   │   ├── engine.py           # generate → build-gate classify → run on SUBSET → score
│   │   ├── revert.py           # gold-minus-one-hunk generator
│   │   ├── stub.py             # body-gutting generator
│   │   ├── classify.py         # {did-not-build, failed-all, discriminated}
│   │   ├── equivalence.py      # mutation non-equivalence confirmation
│   │   └── mutators/           # AST mutators, one file per language (pluggable)
│   │       ├── base.py         # Mutator ABC
│   │       ├── python.py
│   │       ├── go.py
│   │       ├── cpp.py
│   │       └── typescript.py
│   │
│   ├── probes/                 # ★ the heart: one check per file, auto-discovered
│   │   ├── forgery/            # §3.1  (GENERIC)
│   │   │   ├── unrestored_inputs.py
│   │   │   ├── world_readable_tests.py
│   │   │   ├── forgeable_report.py
│   │   │   ├── fail_closed_on_missing.py
│   │   │   ├── detached_child_race.py      # needs_live=True
│   │   │   ├── answer_in_artifact.py
│   │   │   └── graded_source_tamper.py
│   │   ├── hygiene/            # §3.2  (GENERIC, static)
│   │   │   ├── no_bind_mounts.py
│   │   │   ├── pinned_deps.py
│   │   │   ├── no_bare_nproc.py
│   │   │   ├── environment_mode_separate.py
│   │   │   ├── no_build_time_service.py
│   │   │   └── gold_patch_roundtrip.py
│   │   ├── leakage/            # §3.3  (GENERIC)
│   │   │   ├── git_later_history.py
│   │   │   ├── git_unreachable_objects.py
│   │   │   ├── mtime_clustering.py
│   │   │   ├── answer_in_filename.py
│   │   │   └── cache_scan.py
│   │   ├── grader_strength/    # §3.4  (GENERIC — thin wrappers over variants/engine)
│   │   │   ├── revert_probe.py
│   │   │   ├── stub_probe.py
│   │   │   ├── mutation_probe.py
│   │   │   └── heldout_redundancy.py
│   │   ├── reward_shape/       # §3.5  (GENERIC but FORMAT-SPECIFIC linter; A1–A7)
│   │   │   ├── _schema.py       # reads a ScoringSchema (ingest/schemas); requires SCORING_SPEC
│   │   │   ├── a1_heldout_no_gradient.py
│   │   │   ├── a2_cosmetic_milestones.py
│   │   │   ├── a3_inconsistent_weights.py
│   │   │   ├── a4_p2p_multiplier_cliff.py
│   │   │   ├── a5_lint_measures_nothing.py
│   │   │   ├── a6_hack_flag_direction.py
│   │   │   └── a7_stale_metadata.py
│   │   ├── test_quality/       # §3.9  (GENERIC static)
│   │   │   ├── exact_string_pinning.py
│   │   │   ├── call_count_assertions.py
│   │   │   ├── trivial_transcription.py
│   │   │   └── discarded_instructed_work.py
│   │   └── patterns/           # §3.6–§3.8  (PATTERN library — per-grader, grows over time)
│   │       ├── structural/     # "what does the grader check?"
│   │       ├── ablation/       # "gold only where checked, degenerate elsewhere"
│   │       └── fault_injection/# compile-error / transient-fault injection
│   │
│   └── ingest/                 # ANY env → Task (generic-first, presets optional)
│       ├── spec.py             # ★ declarative descriptor: describe any env in config, NO code
│       ├── base.py             # Loader ABC (for when config isn't enough)
│       ├── schemas/            # scoring-spec schema plugins (capability SCORING_SPEC)
│       │   ├── base.py         # ScoringSchema ABC — reward_shape probes read THIS, not a format
│       │   └── harbor_scorepy.py   # one example schema; add others as plugins
│       └── presets/            # OPTIONAL convenience loaders for popular layouts
│           ├── swebench.py     # thin: dataset row → spec.py
│           ├── harbor.py
│           ├── karotte.py
│           └── mimo.py
│
├── tests/
│   ├── conftest.py
│   ├── fixtures/               # ★ known-bad + known-clean tasks (the §7 catalog, realized)
│   │   ├── forgery/
│   │   │   └── unrestored_package_json/   # a task that IS forgeable + an oracle that isn't
│   │   ├── leakage/
│   │   ├── grader_strength/
│   │   └── ...
│   ├── core/                   # contract tests
│   └── probes/                 # one test module per probe, driven by fixtures
│
└── docs/
    ├── writing-a-probe.md
    ├── writing-an-adapter.md
    ├── writing-a-pattern.md
    └── probe-catalog.md        # generated from ProbeMeta (CI keeps it in sync)
```

---

## 2. The core contracts (what a contributor codes against)

### `core/task.py` — the normalized environment
Every adapter produces this; every probe consumes it. The single interop point.

```python
class Capability(Enum):
    """What a task *provides* — probes require these, never a benchmark name."""
    GOLD_PATCH     = "gold_patch"      # a reference fix with addressable hunks
    GIT_REPO       = "git_repo"        # workspace is a git checkout
    CONTAINER      = "container"       # grader runs in a container we control
    SCORING_SPEC   = "scoring_spec"    # a declared reward formula (any schema plugin)
    PER_TEST       = "per_test"        # grader emits per-test results (junit/trx/go-json/…)
    INSTRUCTION    = "instruction"     # an agent-facing prompt exists
    # extend freely — adding a capability is additive

@dataclass(frozen=True)
class Task:
    id: str
    source: str                        # where it came from (free-form; NOT a probe switch)
    instruction: str | None
    repo_dir: Path                     # the task's on-disk workspace (as shipped)
    grader: GraderSpec                 # how to score a submission (cmd, image, report fmt)
    gold_patch: Patch | None = None    # present → GOLD_PATCH capability
    scoring_spec: ScoringSpec | None = None   # present → SCORING_SPEC capability
    languages: frozenset[str] = frozenset()   # drives language gating
    meta: dict = field(default_factory=dict)  # source-specific extras; generic probes never read it

    @cached_property
    def capabilities(self) -> frozenset[Capability]:
        """Derived from what's actually present — the single thing probes gate on."""
        caps = set()
        if self.gold_patch:   caps.add(Capability.GOLD_PATCH)
        if self.scoring_spec: caps.add(Capability.SCORING_SPEC)
        if self.instruction:  caps.add(Capability.INSTRUCTION)
        if (self.repo_dir / ".git").exists(): caps.add(Capability.GIT_REPO)
        caps |= self.grader.capabilities         # CONTAINER, PER_TEST, …
        return frozenset(caps)
```

### `core/finding.py` — what every probe returns
```python
class Severity(IntEnum):   LOW=1; MEDIUM=2; HIGH=3; CRITICAL=4
class VerdictTier(Enum):   CONFIRMED="confirmed"; REVIEW="review"; DEFERRED="deferred"

@dataclass
class Evidence:
    kind: Literal["reproduced_exploit","code_line","variant","perms","diff"]
    detail: str
    replay: str | None        # exact command to reproduce the finding
    artifacts: list[Path] = field(default_factory=list)

@dataclass
class Finding:
    probe_id: str
    title: str
    severity: Severity
    verdict: VerdictTier
    location: str | None      # file:line the finding anchors to
    evidence: Evidence
    fix_hint: str | None      # the actionable remediation (FIX, not just DROP)
```

### `core/probe.py` — the one interface every check implements
```python
class ProbeKind(Enum):   GENERIC="generic"; PATTERN="pattern"   # honest §7 marker

@dataclass(frozen=True)
class ProbeMeta:
    id: str                            # stable, e.g. "forgery.unrestored_inputs"
    family: str                        # "forgery" | "leakage" | ...
    kind: ProbeKind                    # GENERIC (free) vs PATTERN (per-grader)
    severity: Severity
    needs_live: bool = False           # requires a live run (e.g. race probes)
    languages: frozenset[str] | None = None   # None = language-agnostic
    requires: frozenset[Capability] = frozenset()   # task must provide ALL of these
    doc: str = ""                      # one-line summary → probe-catalog.md

class Probe(ABC):
    meta: ProbeMeta                    # class attribute

    def applicable(self, task: Task) -> bool:
        """Default: task has every capability in meta.requires, and languages match.
        Probes gate on CAPABILITIES, never on task.source / benchmark identity."""
        return (self.meta.requires <= task.capabilities and
                (self.meta.languages is None or self.meta.languages & task.languages))

    @abstractmethod
    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        """Deterministic. Use ctx.sandbox / ctx.baseline / ctx.variants. No network
        unless declared. Returns zero or more Findings."""
```

### `core/context.py` — shared, expensive state (built once, handed to every probe)
```python
@dataclass
class ProbeContext:
    sandbox: Sandbox                   # snapshot + copy-on-write overlays
    baseline: Baseline                 # oracle/nop @ k=5 result + per-test→region map
    variants: VariantEngine            # revert/stub/mutation, build-gated, subset-run
    cache: ResultCache                 # content-hashed; variant reruns are free
    config: Config
```

### `core/registry.py` — why there's no central list to edit
```python
_PROBES: dict[str, type[Probe]] = {}

def register(cls: type[Probe]) -> type[Probe]:
    if cls.meta.id in _PROBES:
        raise ValueError(f"duplicate probe id {cls.meta.id}")
    _PROBES[cls.meta.id] = cls
    return cls

def discover() -> list[type[Probe]]:
    # 1. import every module under rlgate.probes.* (in-repo contributions)
    # 2. load third-party probes via entry points: group="rlgate.probes"
    ...
```

A contributor drops a file in `probes/<family>/` with `@register` on the class. Discovery
imports it; it appears in `list-probes`, the catalog, and every audit. External packages can
ship probes via the `rlgate.probes` entry-point group without touching this repo at all.

### Ingestion is generic-first (this is what makes it benchmark-agnostic)
rlgate is **not** built for a fixed list of benchmarks. The default way to onboard *any*
environment is a **declarative descriptor** — you describe it once, in config, with no code:

```yaml
# task.rlgate.yaml  — works for ANY environment
instruction_path: TASK.md          # or inline
workspace: ./repo                  # the as-shipped workspace
gold_patch: ./solution/fix.patch   # optional → enables GOLD_PATCH probes
grader:
  image: ./Dockerfile              # or an existing image
  run: "bash run_tests.sh"
  report: {format: junit, path: /out/results.xml}   # → PER_TEST capability
scoring_spec:                      # optional → enables reward_shape probes
  schema: harbor_scorepy
  path: ./score.py
```

`ingest/spec.py` turns that into a `Task`; its `capabilities` are **derived from which keys
are present**. No gold patch → no revert probe, silently. No `scoring_spec` → no reward_shape,
silently. Nothing in any probe mentions the benchmark.

- **`presets/`** are *optional* thin loaders — e.g. `swebench.py` just maps a dataset row onto
  the same descriptor so you don't hand-write YAML for a known layout. A preset is a
  convenience, never a dependency: deleting all of them leaves a fully working tool driven by
  config.
- **`schemas/`** is how reward-shape stays general: `reward_shape` probes read a
  `ScoringSchema` interface, not a specific `score.py`. Harbor's format is one schema plugin;
  a new reward format = one schema file, and A1–A7 apply to it if it exposes the relevant
  fields.

### `sandbox/base.py` and `ingest/base.py`
Same shape: small ABCs, registered the same way. `Sandbox` exposes `build`, `snapshot`,
`overlay(patch)`, `exec`, `read_perms`, `pkill_user` — enough for forgery/race probes. A
`Loader` (only needed when config can't express the env) exposes
`discover(source) -> Iterable[Task]`.

---

## 2a. Determinism guarantees (rules for anyone writing a generator or operator)

Separate two things both called "deterministic":

- **Variant *generation*** (producing a hunk-subtracted patch / mutant) — **must be a pure
  function** of pinned inputs. Guaranteed by construction; see rules below.
- **Variant *scoring*** (running the grader) — **not** inherently deterministic (flaky tests,
  timing, parallelism, network). Handled separately: baseline at **k=5**, flaky checks flagged
  as unfair tests, network isolation, pinned deps. A finding is reported only when generation
  is reproducible **and** the verdict is stable across the k runs.

**Generation rules (enforced; a PR that breaks one fails review):**

1. **Canonicalize + hash the gold patch once.** Normalize to a canonical unified diff (fixed
   `-U3` context, normalized whitespace/EOL) and record `gold_patch_sha256`. Hunk
   decomposition is then byte-identical across machines and git versions.
2. **Address hunks by structure, not search.** A hunk is `(file, hunk_index)` from the parsed
   diff — no heuristics. Reverse-apply exactly one hunk with **zero fuzz (`-C0`)**: it either
   applies cleanly (one result) or is bucketed as `could-not-generate` (deterministically).
3. **No RNG in generators.** Revert/stub are direct text ops. The mutation generator walks the
   AST in **canonical source-position order** and applies operators in **fixed registry
   order**, one mutant per `(node, operator)`. If a per-task cap is hit, select by a
   **deterministic stable sort**, never `random.sample`. A seed is recorded for audit, but
   nothing draws from it.
4. **Content-addressed IDs.** `variant_id = hash(gold_patch_sha256 + file + hunk_index +
   operator_version)`. Same inputs → same ID → cache hit → exact replay.
5. **Versioned operator set.** Operators are a frozen, ordered registry (`operators@vN`).
   Adding/changing an operator **bumps the version**; it never silently alters an existing
   variant.

**Run manifest** (written per audit, hashed, attached to findings so they replay): gold sha,
patch canonicalization params, git-apply flags, `operators@vN`, tool/image versions, k, seed,
and the resolved probe set with their `ProbeMeta`. This is the generation-side analogue of a
campaign manifest — it makes every finding reproducible from the manifest alone.

---

## 3. A complete example probe (the shape every contributor copies)

`src/rlgate/probes/forgery/world_readable_tests.py`:
```python
from rlgate.core.probe import Probe, ProbeMeta, ProbeKind
from rlgate.core.finding import Finding, Severity, VerdictTier, Evidence
from rlgate.core.registry import register
from rlgate.core.task import Task
from rlgate.core.context import ProbeContext

@register
class WorldReadableTests(Probe):
    meta = ProbeMeta(
        id="forgery.world_readable_tests",
        family="forgery",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        doc="Held-out sources / scorer are readable by the candidate UID (not chmod 700).",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        perms = ctx.sandbox.read_perms("/tests")
        if perms.world_readable:
            return [Finding(
                probe_id=self.meta.id,
                title="/tests is world-readable; held-out names leak at runtime",
                severity=Severity.HIGH,
                verdict=VerdictTier.CONFIRMED,
                location="/tests",
                evidence=Evidence(
                    kind="perms", detail=f"mode={perms.mode:o}",
                    replay="rlgate audit <task> --probe forgery.world_readable_tests",
                ),
                fix_hint="chmod 700 /tests; delete graded sources after build.",
            )]
        return []
```

Its fixture (ships in the same PR): `tests/fixtures/forgery/world_readable_tests/` with a
`bad/` task (mode 755) the probe must flag and an `oracle/` task (mode 700) it must not.
`tests/probes/test_world_readable_tests.py` runs both.

---

## 4. The contribution flow (CONTRIBUTING.md, in one line)

> **Add a probe = add two files.** `probes/<family>/<name>.py` (the `@register`ed class) and
> `tests/fixtures/<family>/<name>/` (a `bad/` it flags + an `oracle/` it stays silent on). No
> central registry, no pipeline edits. CI runs your fixture; the catalog regenerates itself.

Same for adapters (`adapters/<name>.py` + a sample task) and patterns (`probes/patterns/.../`
+ the specific grader it targets, with a note on what it assumes).

---

## 5. How the pipeline wires it together (`pipeline.py`)

```
source ──ingest (descriptor, or optional preset)──▶ [Task]  (capabilities derived)
  Task ──baseline.invariant (k=5) + baseline.pertest──▶ Baseline      # Phase B, fail-fast
  Task + ProbeContext ──discover() ▸ applicable(capabilities) ▸ run──▶ [Finding]  # parallel
  [Finding] ──report.render──▶ EQS + KEEP/FIX/DROP + JSON/Markdown
                           └──▶ CI gate: exit non-zero on any release-blocker (PLAN §8a/§8b)
```

- **Baseline is fail-fast:** if `oracle != 1.0` or a check is flaky at k=5, we emit that
  finding and skip the probes that assume a sane baseline (they'd be noise).
- **Probes run in parallel** over the shared `ProbeContext`; the `VariantEngine` fans out
  variant runs across sandbox workers and caches by content hash.
- **`needs_live` probes** are grouped and run on a real container pass (never synthetic-only).
- **Profiles:** `--profile fast` skips live/variant-heavy probes for quick local feedback;
  `--profile ci` runs everything and enforces the go/no-go gate.

---

## 6. What stays out of core (so the project stays extensible)

- No probe imports another probe. Shared logic lives in `variants/`, `baseline/`, or a
  `core/` helper — never probe-to-probe coupling.
- Source-specific data rides in `Task.meta`; **generic probes never read `meta`** and never
  branch on `task.source` — they gate only on `Task.capabilities`.
- The LLM layer, when it comes, is just another probe `kind` (`ADJUDICATOR`) plus an
  `llm/` client module — it reuses this exact contract and the execution gate. Nothing in
  §2 changes to add it.
```
```

---

## 7. Milestone 0 skeleton (smallest thing that runs end-to-end)

To prove the architecture before breadth: `core/` contracts + `sandbox/docker.py` +
`baseline/invariant.py` + `ingest/spec.py` (+ a thin SWE-bench preset over it) + **three** probes
(`leakage.git_unreachable_objects`, `grader_strength.revert_probe`,
`forgery.world_readable_tests`) + their fixtures + the CI gate. That exercises every seam
(adapter, baseline, sandbox, variants, a static probe, a live-ish probe, reporting) with the
minimum code, so the next 40 probes are pure fill-in.
