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
            res = env.exec("git fsck --unreachable --no-reflogs 2>/dev/null | head -n 2000",
                           timeout=300)
            refs = env.exec(_REFS, timeout=300)
        out: list[Finding] = []
        kinds: dict[str, int] = {}
        sample = ""
        for ln in res.stdout.splitlines():
            parts = ln.split()
            if len(parts) >= 3 and parts[0] == "unreachable":
                kinds[parts[1]] = kinds.get(parts[1], 0) + 1
                if parts[1] == "commit" and not sample:
                    sample = parts[2][:12]
        if kinds.get("commit"):
            out.append(self._f(
                f"{kinds['commit']} unreachable git commit(s) on disk (e.g. {sample})",
                "history was deleted but not pruned; readable via the object store",
                "git fsck --unreachable --no-reflogs",
                _FIX))
        elif kinds:
            what = ", ".join(f"{n} {k}{'s' if n > 1 else ''}" for k, n in sorted(kinds.items()))
            out.append(self._f(
                f"Unreachable git objects on disk ({what})",
                "files or trees no commit points to are still in the object store; "
                "`git cat-file -p <id>` reads them, and one may hold the fix",
                "git fsck --unreachable --no-reflogs", _FIX, proven=False))

        later, other = [], []
        for ln in refs.stdout.splitlines():
            parts = ln.split()
            if len(parts) == 4 and parts[0] in ("later", "other"):
                (later if parts[0] == "later" else other).append(
                    (parts[1], int(parts[2]), int(parts[3])))
        base_time = next((int(ln.split()[1]) for ln in refs.stdout.splitlines()
                          if ln.startswith("base ")), 0)
        if later:
            ref, n, _ = max(later, key=lambda r: r[1])
            out.append(self._f(
                f"{ref} holds {n} commit(s) after the task's starting point"
                + (f" (+{len(later) - 1} more refs)" if len(later) > 1 else ""),
                "a branch, tag or stash still points past HEAD; the post-change history is "
                "one `git log` away",
                f"git log --oneline HEAD..{ref}", _FIX_REFS))
        if other:
            commits = sum(n for _, n, _ in other)
            newest = max(t for _, _, t in other)
            ref = max(other, key=lambda r: r[2])[0]
            when = ("newer than the starting commit" if base_time and newest > base_time
                    else "none newer than the starting commit")
            out.append(self._f(
                f"{len(other)} ref(s) hold commits that aren't in the task's history "
                f"(e.g. {ref}; {when})",
                f"`git log --all` reaches {commits} commit(s) outside the agent's checkout "
                "(other branches, tags or remotes, e.g. the upstream project's). The fix "
                "may be among them; without a reference solution rlgate can't tell",
                f"git log --all --oneline --not HEAD | head; git log -1 {ref}",
                _FIX_REFS, proven=False))
        return out

    def _f(self, title: str, detail: str, replay: str, fix: str, *,
           proven: bool = True) -> Finding:
        return Finding(
            probe_id=self.meta.id,
            title=title,
            severity=Severity.HIGH,
            verdict=VerdictTier.CONFIRMED if proven else VerdictTier.REVIEW,
            location=".git",
            evidence=Evidence(kind="reproduced_exploit", detail=detail, replay=replay),
            fix_hint=fix,
        )


_FIX = ("git reflog expire --expire=now --all && git gc --prune=now; or vendor a clean "
        "snapshot at the task base commit.")
_FIX_REFS = ("delete every ref outside the starting commit's history (branches, tags, "
             "remotes, stash), expire the reflog and gc; or ship a fresh clone at the base "
             "commit with no other refs.")

# Refs whose commit isn't in HEAD's history: "later <ref> <commits> <newest-unix-time>" for
# descendants of HEAD (history after the task's start), "other <ref> ..." for the rest
# (side branches, an upstream project behind an orphan task-base commit). One cheap count
# first: a repo with nothing outside HEAD's history costs a single command. The newest 200
# refs only, so an upstream clone with thousands of tags stays bounded.
_REFS = (
    "git rev-parse -q --verify HEAD >/dev/null 2>&1 || exit 0; "
    "echo \"base $(git log -1 --format=%ct HEAD)\"; "
    "[ \"$(git rev-list --all --not HEAD --count 2>/dev/null)\" = 0 ] && exit 0; "
    "h=$(git rev-parse HEAD); "
    "git for-each-ref --sort=-committerdate --format='%(refname)' 2>/dev/null "
    "| head -n 200 | while read -r r; do "
    "c=$(git rev-parse \"$r^{commit}\" 2>/dev/null) || continue; "
    "[ \"$c\" = \"$h\" ] && continue; "
    "git merge-base --is-ancestor \"$c\" \"$h\" 2>/dev/null && continue; "
    "n=$(git rev-list --count \"$c\" --not \"$h\"); "
    "t=$(git log -1 --format=%ct $(git rev-list \"$c\" --not \"$h\" | head -n 1)); "
    "if git merge-base --is-ancestor \"$h\" \"$c\" 2>/dev/null; then k=later; else k=other; fi; "
    "echo \"$k $r $n $t\"; done")
