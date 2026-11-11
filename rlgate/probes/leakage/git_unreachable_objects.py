"""§3.3 — the reference fix survives in git: an unreachable object (the MiMo/SQLGlot leak), or
a branch / tag / stash that still points past the task's starting commit.

We read the object store directly as the auditor, so an agent-side "git commands blocked"
guard is irrelevant — if the object is on disk, the answer is on disk."""

from __future__ import annotations

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Evidence, Finding, Severity, VerdictTier
from rlgate.core.probe import Probe, ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Capability, Task


@register
class GitUnreachableObjects(Probe):
    meta = ProbeMeta(
        id="leakage.git_unreachable_objects",
        family="leakage",
        kind=ProbeKind.GENERIC,
        severity=Severity.HIGH,
        needs_live=True,   # reads the object store inside a live overlay
        requires=frozenset({Capability.GIT_REPO}),
        doc="Unreachable git objects (deleted-but-unpruned branches) may hold the fix.",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        # what the AGENT can read: its own environment, not the verifier's
        try:
            env = ctx.sandbox.agent_env()
        except NotImplementedError:
            env = ctx.sandbox.overlay(None)
        with env:
            res = env.exec("git fsck --unreachable --no-reflogs 2>/dev/null | head -n 50")
            later = env.exec(_LATER, timeout=300)
        out: list[Finding] = []
        lines = [ln for ln in res.stdout.splitlines() if "unreachable commit" in ln]
        if lines:
            sample = lines[0].split()[-1][:12]
            out.append(self._f(
                f"{len(lines)} unreachable git commit(s) on disk (e.g. {sample})",
                "later history was deleted but not pruned; readable via the object store",
                "git fsck --unreachable --no-reflogs",
                "git reflog expire --expire=now --all && git gc --prune=now; "
                "or vendor a clean snapshot at the task base commit."))
        refs = [ln.strip() for ln in later.stdout.splitlines() if ln.strip()]
        if refs:
            ref, n = refs[0].rsplit(" ", 1)
            out.append(self._f(
                f"{ref} holds {n} commit(s) after the task's starting point"
                + (f" (+{len(refs) - 1} more refs)" if len(refs) > 1 else ""),
                "a branch, tag or stash still points past HEAD; the post-change history is "
                "one `git log` away",
                f"git log --oneline HEAD..{ref}",
                "delete every ref past HEAD (branches, tags, stash), expire the reflog and "
                "gc; or ship a fresh clone at the base commit."))
        return out

    def _f(self, title: str, detail: str, replay: str, fix: str) -> Finding:
        return Finding(
            probe_id=self.meta.id,
            title=title,
            severity=Severity.HIGH,
            verdict=VerdictTier.CONFIRMED,
            location=".git",
            evidence=Evidence(kind="reproduced_exploit", detail=detail, replay=replay),
            fix_hint=fix,
        )


# every ref (branch, tag, stash, remote) holding commits that are descendants of HEAD:
# "<ref> <count>" per line
_LATER = (
    "git rev-parse -q --verify HEAD >/dev/null 2>&1 || exit 0; "
    "git for-each-ref --format='%(refname)' 2>/dev/null | while read -r r; do "
    "c=$(git rev-parse \"$r^{commit}\" 2>/dev/null) || continue; "
    "[ \"$c\" = \"$(git rev-parse HEAD)\" ] && continue; "
    "git merge-base --is-ancestor HEAD \"$c\" 2>/dev/null || continue; "
    "echo \"$r $(git rev-list --count HEAD..\"$c\")\"; done")
