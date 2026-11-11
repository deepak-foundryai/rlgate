"""One ``--deep-review`` run's L2 review: the judge panel, the calls, what they cost.

Built once per run and handed to the review probes (``ProbeContext.review``); each probe asks
for its artifact or question and gets back the valid negatives. Results are shared, so the
statement probe and the determinability probe can both use the second pass without paying
twice, and the same call is never made twice in one run (or across runs: answers are cached
on disk under ``$RLGATE_STATE_DIR/llm``, keyed by model, seed and the exact prompt).

The model is the user's choice, any LiteLLM ``provider/model``: ``[models] review`` in
rlgate.toml, ``RLGATE_REVIEW_MODELS``, or ``--model`` (repeatable) on ``rlgate check``.
Judges from the family of the model that wrote the task are left out (task metadata
``author_model``, else ``[models] author``).

Fail closed: a call that fails after retries, a judge that skips a criterion, or a spent
budget raises ``CouldNotJudge``; the check is then shown as not finished, never as passed.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from rlgate.core.probe import CouldNotJudge, NotApplicable
from rlgate.core.task import Task

if TYPE_CHECKING:
    from rlgate.agentic.judges import Audit
from rlgate.llm import LLM, ChatTurn, LLMError, LLMResult, family, register_models
from rlgate.llm import chat as llm_chat
from rlgate.llm import price as llm_price
from rlgate.review import judges as J
from rlgate.review.bundle import Bundle, build
from rlgate.review.cite import Negative, why_invalid

OUTPUT_TOKENS = 1500          # what one judge answer is assumed to cost, for the estimate
AGENT_TURN_GROWTH = 1200      # tokens an L3 conversation grows by per turn (estimate)


class Caller(Protocol):
    def complete(self, prompt: str, *, system: str | None = ..., schema: dict | None = ...,
                 schema_name: str = ..., seed: int | None = ...) -> LLMResult: ...


@dataclass
class Verdicts:
    """What the judges said about one artifact or question, after the citation checks."""
    check: str
    valid: list[Negative] = field(default_factory=list)
    discarded: Counter = field(default_factory=Counter)      # reason → count
    clean: list[str] = field(default_factory=list)           # criteria judged clean
    calls: int = 0
    failed: list[str] = field(default_factory=list)          # votes that errored
    models: list[str] = field(default_factory=list)

    @property
    def note(self) -> str:
        """One line for the report: how many calls, which models, what was discarded."""
        bits = [(f"{self.calls} call{'s' if self.calls != 1 else ''} "
                f"({', '.join(dict.fromkeys(self.models))})")]
        if self.discarded:
            bits.append("discarded " + ", ".join(f"{n} ({why})" for why, n in
                                                sorted(self.discarded.items())))
        if self.failed:
            bits.append(f"{len(self.failed)} vote(s) failed")
        return "; ".join(bits)


@dataclass
class Rewrite:
    text: str
    path: Path
    clean: bool                        # the rewrite passed re-review
    remaining: list[str]               # problems the re-review still found


@dataclass
class Estimate:
    calls: int
    input_tokens: int
    output_tokens: int
    usd: float | None                  # None: a model's price is unknown to LiteLLM
    unpriced: list[str] = field(default_factory=list)


class Session:
    def __init__(self, models: list[str], *, votes: int = 3, min_confidence: float = 0.5,
                 author: str | None = None, budget_usd: float | None = None,
                 client: Callable[[str], Caller] | None = None,
                 cache_dir: Path | None = None, out_dir: Path | None = None,
                 agent: str | None = None, agent_turns: int = 30,
                 agent_grades: int = 6) -> None:
        if not models:
            raise ValueError("the review needs at least one model")
        self.models = list(dict.fromkeys(models))
        self.votes = votes
        self.min_confidence = min_confidence
        self.author = author
        self.budget_usd = budget_usd
        self.agent = agent                    # L3: the model that drives the agent
        self.agent_turns = agent_turns
        self.agent_grades = agent_grades
        self._client = client or (lambda m: LLM(model=m))
        self._clients: dict[str, Caller] = {}
        self.cache_dir = cache_dir
        self.out_dir = out_dir
        self.spent = 0.0
        self.calls = 0
        self.cached = 0
        self._lock = threading.Lock()
        self._memo: dict[tuple, Any] = {}
        self._keylocks: dict[tuple, threading.Lock] = {}

    @classmethod
    def from_settings(cls, ms, *, models: list[str] | None = None) -> Session:
        from rlgate.core.repro import state_dir

        register_models(ms.info)
        panel = models or ms.panel()
        base = {"timeout_s": ms.timeout_s, "max_retries": ms.max_retries,
                    "max_tokens": ms.max_tokens, "extra": dict(ms.extra)}
        return cls(panel, votes=ms.votes, min_confidence=ms.min_confidence,
                   author=ms.author, budget_usd=ms.budget_usd,
                   client=lambda m: LLM(model=m, **base),
                   cache_dir=state_dir() / "llm", out_dir=state_dir() / "review",
                   agent=ms.agent, agent_turns=ms.agent_turns,
                   agent_grades=ms.agent_grades)

    # -- who judges ----------------------------------------------------------------------

    def author_of(self, task: Task) -> str | None:
        md = task.meta.get("metadata") or {}
        for k in ("author_model", "generator_model", "authoring_model"):
            v = md.get(k) or task.meta.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return self.author

    def panel(self, task: Task) -> list[str]:
        """The models allowed to judge this task: none from its author's family."""
        author = self.author_of(task)
        if author is None:
            return list(self.models)
        fam = family(author)
        ok = [m for m in self.models if family(m) != fam]
        if not ok:
            raise CouldNotJudge(
                f"every review model is from the {fam} family, which wrote this task "
                f"({author}); add a model from another family to [models] review or pass "
                "--model")
        return ok

    def agent_model(self, task: Task) -> str:
        """The L3 agent: ``[models] agent``, else the first judge allowed on this task;
        never from the author's family."""
        allowed = self.panel(task)
        if self.agent is None:
            return allowed[0]
        author = self.author_of(task)
        if author is not None and family(self.agent) == family(author):
            raise CouldNotJudge(f"the L3 agent {self.agent} is from the {family(author)} "
                                f"family, which wrote this task ({author}); set "
                                "[models] agent to another family")
        return self.agent

    def problems(self) -> list[str]:
        """Models that can't be called from here, with the fix (no network)."""
        out = []
        for m in dict.fromkeys(self.models + ([self.agent] if self.agent else [])):
            c = self._get(m)
            check = getattr(c, "check", None)
            reason = check() if check else None
            if reason:
                out.append(reason)
        return out

    # -- the two passes ------------------------------------------------------------------

    def bundle(self, task: Task) -> Bundle:
        return self._once(("bundle", _tid(task)), lambda: build(task))

    def first(self, task: Task, artifact: str) -> Verdicts:
        b = self.bundle(task)
        _applies(artifact, b)
        return self._once(("first", _tid(task), artifact, b.digest),
                          lambda: self._first(task, artifact, b))

    def second(self, task: Task, question: str) -> Verdicts:
        b = self.bundle(task)
        _applies(question, b)
        return self._once(("second", _tid(task), question, b.digest),
                          lambda: self._second(task, question, b, self.votes))

    def rewrite(self, task: Task) -> Rewrite | None:
        """Fixable statement problems (first-pass statement criteria, determinability) →
        a rewritten statement, then re-reviewed. None when there is nothing to fix."""
        return self._once(("rewrite", _tid(task)), lambda: self._rewrite(task))

    def _first(self, task: Task, artifact: str, b: Bundle) -> Verdicts:
        model = self.panel(task)[0]
        v = Verdicts(artifact, models=[model])
        res = self._ask(model, J.first_prompt(artifact, b.render(repo=False)),
                        J.first_schema(artifact), seed=1)
        v.calls = 1
        crit = {c.name: c for c in J.FIRST[artifact]}
        entries = res.data["results"]
        missing = sorted(set(crit) - {e["criterion"] for e in entries})
        if missing:
            raise CouldNotJudge(f"{model} did not judge {', '.join(missing)}")
        for e in entries:
            c = crit[e["criterion"]]
            if e["verdict"] == "pass":
                v.clean.append(c.name)
                continue
            self._keep(v, Negative(c.name, e["gap"], e["quote"], e["quote_from"], e["test"],
                                   _conf(e["confidence"]), e["fix"], model), c, b)
        v.clean = [n for n in dict.fromkeys(v.clean) if not any(x.check == n
                                                                 for x in v.valid)]
        return v

    def _second(self, task: Task, question: str, b: Bundle, votes: int) -> Verdicts:
        from rlgate.variants.engine import pmap

        panel = self.panel(task)
        c = J.SECOND[question]
        prompt = J.second_prompt(question, b.hidden.render(repo=True))
        schema = J.second_schema(question)
        v = Verdicts(question)

        def vote(i: int) -> tuple[str, LLMResult | Exception]:
            model = panel[i % len(panel)]
            try:
                return model, self._ask(model, prompt, schema, seed=i + 1)
            except CouldNotJudge as e:
                return model, e

        for model, res in pmap(vote, list(range(votes)), votes):
            v.models.append(model)
            if isinstance(res, Exception):
                v.failed.append(f"{model}: {res}")
                continue
            v.calls += 1
            if res.data["verdict"] != "problem":
                continue
            if not res.data["negatives"]:
                v.discarded["a problem with no citation"] += 1
            for e in res.data["negatives"]:
                self._keep(v, Negative(question, e["gap"], e["quote"], e["quote_from"],
                                       e["test"], _conf(e["confidence"]), e["detail"], model),
                           c, b)
        if v.failed and not v.valid:          # fail closed: a missing vote isn't a pass
            raise CouldNotJudge("; ".join(v.failed))
        return v

    def _keep(self, v: Verdicts, n: Negative, c: J.Criterion, b: Bundle) -> None:
        if n.source not in c.sources:          # quoted from a file this check can't cite
            n.source = c.sources[0]
        best = None
        for src in dict.fromkeys((n.source, *c.sources)):
            why = why_invalid(replace(n, source=src), b, min_confidence=self.min_confidence,
                              needs_test=c.needs_test)
            if why is None:
                n.source = src
                v.valid.append(n)
                return
            best = best or why
        v.discarded[best or "invalid"] += 1

    def _rewrite(self, task: Task) -> Rewrite | None:
        b = self.bundle(task)
        if not b.statement.strip():
            return None
        found = []
        for name, fn in (("statement", self.first), ("determinability", self.second)):
            try:
                found += fn(task, name).valid
            except NotApplicable:
                continue
        if not found:
            return None
        model = self.panel(task)[0]
        problems = [f"{n.gap} (cited: \"{n.quote.strip()[:200]}\")" for n in found]
        res = self._ask(model, J.rewrite_prompt(b.hidden.render(repo=True), problems),
                        J.REWRITE_SCHEMA, seed=1, name="rewrite")
        text = res.data["statement"].strip() + "\n"
        nb = replace(b, statement=text)
        again = self._first(task, "statement", nb).valid
        try:
            _applies("determinability", nb)
            again += self._second(task, "determinability", nb, 1).valid
        except NotApplicable:
            pass
        from rlgate.core.repro import state_dir

        path = (self.out_dir or state_dir() / "review") / _safe(task.id) / \
            "instruction.rewritten.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return Rewrite(text, path, not again, [n.gap for n in again])

    # -- L3: agentic audits ------------------------------------------------------------------

    @staticmethod
    def groups(v: Verdicts) -> list[list[Negative]]:
        """Valid negatives, one group per distinct problem (same check, test and quote),
        most votes first: what L2 reports, and the leads L3 gets (ids L1, L2, …)."""
        out: dict[tuple, list[Negative]] = {}
        for n in v.valid:
            out.setdefault(n.key, []).append(n)
        return sorted(out.values(), key=lambda g: (-len(g), -max(x.confidence for x in g)))

    def leads(self, task: Task, question: str) -> list[tuple[str, str]]:
        """L2's second-pass problems for ``question``, as (lead id, description)."""
        try:
            v = self.second(task, question)
        except (CouldNotJudge, NotApplicable):
            return []
        out = []
        for i, g in enumerate(self.groups(v)):
            n = max(g, key=lambda x: x.confidence)
            what = n.gap.strip()
            if n.test:
                what += f" (test {n.test}: \"{n.quote.strip()[:160]}\")"
            if question == "weak_pass" and n.fix.strip():
                what += f" Shortcut: {n.fix.strip()}"
            out.append((f"L{i + 1}", what))
        return out

    def strength(self, task: Task, ctx) -> Audit:
        """L3 verifier-strength audit (too weak?), run once per task."""
        return self._once(("strength", _tid(task)), lambda: self._audit(task, ctx, "strength"))

    def ambiguity(self, task: Task, ctx) -> Audit:
        """L3 ambiguity audit (too strict?), run once per task."""
        return self._once(("ambiguity", _tid(task)),
                          lambda: self._audit(task, ctx, "ambiguity"))

    def _audit(self, task: Task, ctx, kind: str) -> Audit:
        from rlgate.agentic import loop
        from rlgate.agentic.bench import Bench
        from rlgate.agentic.judges import AmbiguityJudge, StrengthJudge

        if getattr(ctx, "sandbox", None) is None:
            raise NotApplicable("needs a full run: the agent runs code in the sandbox")
        if task.gold_patch is None:
            raise NotApplicable("needs a reference fix to test the agent's requirement "
                                "checks against")
        if not (task.instruction or "").strip():
            raise NotApplicable("the task has no statement")
        model = self.agent_model(task)
        b = self.bundle(task)
        if kind == "strength":
            judge = StrengthJudge(Bench(task, ctx), self.leads(task, "weak_pass"),
                                  self.agent_grades)
        else:
            if not b.tests:
                raise NotApplicable("no test files were found to read")
            judge = AmbiguityJudge(Bench(task, ctx, tests=dict(b.tests)),
                                   self.leads(task, "determinability"), self.agent_grades,
                                   ctx.baseline.per_test)
        start = loop.dossier(judge.bench, list(b.repo))
        try:
            loop.run(judge, lambda m, t: self._turn(model, m, t),
                     loop.prompt(judge, task.instruction or "", b.tree, start),
                     max_turns=self.agent_turns)
        except CouldNotJudge:
            if judge.audit.bypasses or judge.audit.alternatives:
                judge.audit.log.append("stopped early")   # proofs so far still stand
                return judge.audit
            raise
        return judge.audit

    # -- L4: rubric attacks -----------------------------------------------------------------

    def rubric_attacks(self, task: Task, rubric) -> list:
        """L4 for rubric-graded tasks: a white-box and a black-box attacker each write one
        deliverable; a rubric judge and a goal judge grade it independently."""
        return self._once(("rubric", _tid(task)), lambda: self._rubric(task, rubric))

    def _rubric(self, task: Task, rubric) -> list:
        from rlgate.review import rubric as RB

        panel = self.panel(task)
        attacker, rjudge = panel[0], panel[0]
        gjudge = panel[1 % len(panel)]               # a second model when there is one
        statement = (task.instruction or "").strip()
        listing = RB.listing(rubric)
        out = []
        for name, prompt in (
                ("white-box", RB.WHITE.format(statement=statement, rubric=listing,
                                              path=rubric.path, passing=RB.PASS)),
                ("black-box", RB.BLACK.format(statement=statement))):
            a = self._ask(attacker, prompt, RB.ATTACK_SCHEMA, seed=1, name="attack",
                          system=RB.SYSTEM).data
            d = a["deliverable"]
            graded = self._ask(rjudge, RB.RUBRIC_JUDGE.format(
                statement=statement, rubric=listing, deliverable=d),
                RB.rubric_schema(rubric), seed=2, name="rubric", system=RB.SYSTEM).data
            met = {e["id"]: bool(e["met"]) for e in graded["criteria"]}
            missing = [c.id for c in rubric.criteria if c.id not in met]
            if missing:
                raise CouldNotJudge(f"{rjudge} did not grade {', '.join(missing)}")
            goal = self._ask(gjudge, RB.GOAL_JUDGE.format(statement=statement,
                                                          deliverable=d),
                             RB.GOAL_SCHEMA, seed=3, name="goal", system=RB.SYSTEM).data
            out.append(RB.Attack(name, d, a["how"], RB.score(rubric, met), met,
                                 {e["id"]: e["reason"] for e in graded["criteria"]},
                                 bool(goal["achieved"]), goal["missing"],
                                 _conf(goal["confidence"]), rjudge, gjudge))
        return out

    # -- L4: the hack agent ----------------------------------------------------------------

    def hack(self, task: Task, ctx) -> list:
        """L4: one short hack run per taxonomy slice, in parallel, each in its own
        container. Returns the runs (``agentic.hack.HackRun``); the probe proves or drops
        every claim by execution."""
        return self._once(("hack", _tid(task)), lambda: self._hack(task, ctx))

    def _hack(self, task: Task, ctx) -> list:
        from rlgate.agentic import hack as H
        from rlgate.agentic import loop
        from rlgate.variants.engine import pmap

        model = self.agent_model(task)
        tree = self.bundle(task).tree
        submits = max(1, min(self.agent_grades, 3))

        def one(slice_: str):
            agent = H.HackAgent(task, ctx.sandbox, slice_, max_submits=submits)
            try:
                loop.run(agent, lambda m, t: self._turn(model, m, t),  # type: ignore[arg-type]
                         H.prompt(task, slice_, tree, submits=submits,
                                  shells=agent.max_shells),
                         max_turns=self.agent_turns, system=H.SYSTEM)
            except CouldNotJudge as e:
                agent.run.summary = f"stopped: {e}"
            finally:
                agent.close()
            return agent.run

        return pmap(one, list(H.SLICES), len(H.SLICES))

    def _turn(self, model: str, messages: list[dict], tools: list[dict]) -> ChatTurn:
        with self._lock:
            if self.budget_usd is not None and self.spent >= self.budget_usd:
                raise CouldNotJudge(f"the LLM budget of ${self.budget_usd:.2f} is spent "
                                    "([models] budget_usd)")
        c = self._get(model)
        try:
            own = getattr(c, "chat", None)        # a scripted client brings its own
            t = own(messages, tools) if own else llm_chat(c, messages, tools)  # type: ignore[arg-type]
        except LLMError as e:
            raise CouldNotJudge(str(e)) from e
        with self._lock:
            self.calls += 1
            self.spent += t.cost_usd or 0.0
        return t

    # -- calls -----------------------------------------------------------------------------

    def _get(self, model: str) -> Caller:
        with self._lock:
            if model not in self._clients:
                self._clients[model] = self._client(model)
            return self._clients[model]

    def _ask(self, model: str, prompt: str, schema: dict, *, seed: int,
             name: str = "review", system: str = J.SYSTEM) -> LLMResult:
        key = hashlib.sha256(json.dumps([model, seed, system, prompt, schema],
                                        sort_keys=True).encode()).hexdigest()
        hit = self._load(key)
        if hit is not None:
            with self._lock:
                self.cached += 1
            return hit
        with self._lock:
            if self.budget_usd is not None and self.spent >= self.budget_usd:
                raise CouldNotJudge(f"the LLM budget of ${self.budget_usd:.2f} is spent "
                                    "([models] budget_usd)")
        try:
            res = self._get(model).complete(prompt, system=system, schema=schema,
                                            schema_name=name, seed=seed)
        except LLMError as e:
            raise CouldNotJudge(str(e)) from e
        with self._lock:
            self.calls += 1
            self.spent += res.cost_usd or 0.0
        self._save(key, res)
        return res

    def _load(self, key: str) -> LLMResult | None:
        if self.cache_dir is None:
            return None
        p = self.cache_dir / f"{key}.json"
        try:
            d = json.loads(p.read_text())
            return LLMResult(d["text"], d["data"], d["model"], d["input_tokens"],
                             d["output_tokens"], 0.0, d.get("mode", "cached"))
        except (OSError, ValueError, KeyError):
            return None

    def _save(self, key: str, res: LLMResult) -> None:
        if self.cache_dir is None or res.data is None:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / f"{key}.json").write_text(json.dumps({
            "text": res.text, "data": res.data, "model": res.model,
            "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
            "cost_usd": res.cost_usd, "mode": res.mode}))

    def _once(self, key: tuple, fn: Callable[[], Any]) -> Any:
        """Compute ``fn`` once per key, even with steps running in parallel threads; a
        failure is remembered too, so a second caller gets the same answer."""
        with self._lock:
            lock = self._keylocks.setdefault(key, threading.Lock())
        with lock:
            if key not in self._memo:
                try:
                    self._memo[key] = ("ok", fn())
                except (CouldNotJudge, NotApplicable) as e:
                    self._memo[key] = ("err", e)
            kind, val = self._memo[key]
        if kind == "err":
            raise val
        return val

    # -- cost ------------------------------------------------------------------------------

    def estimate(self, tasks: list[Task], *, agentic: bool = False) -> Estimate:
        """Calls, tokens and dollars for reviewing ``tasks`` (no network; LiteLLM's price
        table). Rewrites are not counted: they happen only when a statement needs one.
        ``agentic``: add the two L3 audits and the three L4 hack runs per task at their
        turn limit (an upper bound: most runs finish earlier)."""
        calls = tin = tout = 0
        usd = 0.0
        unpriced: set[str] = set()
        sys_tokens = len(J.SYSTEM) // 4
        for task in tasks:
            b = self.bundle(task)
            try:
                panel = self.panel(task)
            except CouldNotJudge:
                continue
            plan: list[tuple[str, int]] = []      # (model, prompt tokens)
            first = len(b.render(repo=False)) // 4 + sys_tokens + 400
            for a in J.ARTIFACTS:
                if _applies_quiet(a, b):
                    plan.append((panel[0], first))
            second = len(b.hidden.render(repo=True)) // 4 + sys_tokens + 400
            for q in J.QUESTIONS:
                if _applies_quiet(q, b):
                    plan += [(panel[i % len(panel)], second) for i in range(self.votes)]
            if agentic and task.gold_patch is not None and b.statement.strip():
                model = self.agent or panel[0]
                base = (len(b.statement) + len("\n".join(b.tree[:200]))) // 4 + 1500
                # L3: two audits (one without tests to read); L4: three hack runs (on a
                # container backend). Old tool outputs are masked, so a turn's prompt stops
                # growing after about KEEP + STEP tool results.
                from rlgate.agentic.loop import KEEP, STEP
                runs = (2 if b.tests else 1) + 3
                for _ in range(runs):
                    for t in range(self.agent_turns):
                        plan.append((model, base + AGENT_TURN_GROWTH * min(t, KEEP + STEP)))
            for model, tokens in plan:
                calls += 1
                tin += tokens
                tout += OUTPUT_TOKENS
                price = llm_price(model, tokens, OUTPUT_TOKENS)
                if price is None:
                    unpriced.add(model)
                else:
                    usd += price
        return Estimate(calls, tin, tout, None if unpriced else usd, sorted(unpriced))


def _applies(check: str, b: Bundle) -> None:
    if check in ("statement", "determinability", "enforcement") and not b.statement.strip():
        raise NotApplicable("the task has no statement")
    if check in ("tests", "determinability", "enforcement", "weak_pass") and not b.tests:
        raise NotApplicable("no test files were found to review")
    if check == "reference" and not b.reference:
        raise NotApplicable("the task has no reference fix")


def _applies_quiet(check: str, b: Bundle) -> bool:
    try:
        _applies(check, b)
    except NotApplicable:
        return False
    return True


def _conf(x: Any) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def _tid(task: Task) -> str:
    return task.origin or task.id


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name) or "task"
