"""§3.3 — the reference fix survives in git: an unreachable object (the MiMo/SQLGlot leak), a
branch / tag / stash that still points past the task's starting commit, or — whatever the
history's shape — the exact post-change version of a file the fix or the hidden tests touch,
already in the object store (e.g. upstream history above a "task base" commit that removed
the feature).

We read the object store directly as the auditor, so an agent-side "git commands blocked"
guard is irrelevant — if the object is on disk, the answer is on disk."""

from __future__ import annotations

import base64
import shlex

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
        needs_baseline=False,      # inspects the environment only
        doc="Unreachable git objects (deleted-but-unpruned branches) may hold the fix.",
    )

    def run(self, task: Task, ctx: ProbeContext) -> list[Finding]:
        # what the AGENT can read: its own environment, not the verifier's
        try:
            env = ctx.sandbox.agent_env()
        except NotImplementedError:
            env = ctx.sandbox.overlay(None)
        with env:
            res = env.exec(_FSCK, timeout=300)
            refs = env.exec(_REFS, timeout=300)
            known = [(label, hit) for label, text in _changes(task)
                     for hit in _known_versions(env, text)]
        out: list[Finding] = []
        if known:
            label, (sha, path, commit) = known[0]
            where = (f"commit {commit}" if commit else
                     "an object no branch points to")
            more = f" (+{len(known) - 1} more file(s))" if len(known) > 1 else ""
            out.append(self._f(
                f"The changed version of {path} is already in git ({where}){more}",
                f"with {label} applied, {path} is byte-identical to object {sha[:12]}, which "
                "is already in the repository's object store: the change the task asks for "
                "is in git, readable with `git log -p` or `git cat-file`",
                f"git log --all --oneline --find-object={sha}; git cat-file -p {sha}",
                _FIX_HISTORY))
        kinds: dict[str, int] = {}
        sample = ""
        for ln in res.stdout.splitlines():
            parts = ln.split()
            if len(parts) == 3 and parts[0] == "count":            # awk's totals
                kinds[parts[1]] = int(parts[2])
            elif len(parts) == 2 and parts[0] == "sample":
                sample = parts[1][:12]
            elif len(parts) >= 3 and parts[0] == "unreachable":    # no awk: a capped list
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
        return self._one(out)

    @staticmethod
    def _one(found: list[Finding]) -> list[Finding]:
        """One finding per task: the git store is a single leak, however many ways it shows.
        The strongest sign leads (in the order found: the fix's own file, refs past HEAD,
        unreachable commits, other refs); the rest are listed after it."""
        if len(found) <= 1:
            return found
        proven = [f for f in found if f.verdict is VerdictTier.CONFIRMED]
        lead = (proven or found)[0]
        rest = [f for f in found if f is not lead]
        lead.evidence = Evidence(
            kind=lead.evidence.kind,
            detail=lead.evidence.detail + ". Also: " + "; ".join(
                f.title[0].lower() + f.title[1:] for f in rest),
            replay=lead.evidence.replay)
        return [lead]

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


# Unreachable objects, counted per type. --connectivity-only skips re-hashing every object
# (seconds instead of minutes on a large pack); awk counts without a cap.
_FSCK = ("git fsck --unreachable --no-reflogs --connectivity-only 2>/dev/null | "
         "if command -v awk >/dev/null 2>&1; then awk '$1==\"unreachable\" {n[$2]++; "
         "if ($2==\"commit\" && s==\"\") s=$3} END {for (k in n) print \"count\", k, n[k]; "
         "if (s!=\"\") print \"sample\", s}'; else head -n 2000; fi")

_FIX_HISTORY = ("ship the repository without the history that holds the change: a fresh "
                "single-commit snapshot of the starting tree (no other refs, no packs from the "
                "upstream clone), then expire the reflog and gc.")
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


_MAX_PATCH = 200_000      # bytes; larger patches are skipped (command-line size)


def _changes(task: Task) -> list[tuple[str, str]]:
    """(label, unified diff) for each post-change view rlgate knows: the reference fix, and
    any patch file among the held-out tests (the graded tests usually come from the same
    upstream commit as the fix)."""
    out: list[tuple[str, str]] = []
    if task.gold_patch is not None:
        out.append(("the reference fix", task.gold_patch.text))
    for root, prefix in task.test_roots[1:]:
        for f in sorted([*root.rglob("*.diff"), *root.rglob("*.patch")])[:5]:
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            if "\n+++ " in text and ("\n--- " in text or text.startswith("--- ")):
                out.append((f"the hidden test patch ({prefix}{f.relative_to(root)})", text))
    return [(label, t) for label, t in out if 0 < len(t.encode()) <= _MAX_PATCH]


def _post_paths(text: str) -> list[str]:
    from unidiff.patch import PatchSet
    try:
        ps = PatchSet(text)
    except Exception:  # noqa: BLE001 - an unparsable patch has nothing to check
        return []
    paths = []
    for pf in ps:
        if pf.is_removed_file:
            continue
        p = pf.target_file.removeprefix("b/")
        if p and p != "/dev/null":
            paths.append(p)
    return paths


def _known_versions(env, text: str) -> list[tuple[str, str, str]]:
    """(blob id, path, commit or "") for each file whose post-change version is already in
    the object store. Applies the patch to the work tree only (``git apply`` and
    ``git hash-object`` without -w write no objects) and reverts it afterwards."""
    paths = _post_paths(text)
    if not paths:
        return []
    b64 = base64.b64encode(text.encode()).decode()
    quoted = " ".join(shlex.quote(p) for p in paths[:50])
    script = (
        "git rev-parse -q --verify HEAD >/dev/null 2>&1 || exit 0; "
        "f=$(mktemp); echo " + b64 + " | base64 -d > \"$f\"; "
        "git apply --whitespace=nowarn \"$f\" 2>/dev/null || { rm -f \"$f\"; exit 0; }; "
        "T=; command -v timeout >/dev/null && T='timeout 120'; "
        "for p in " + quoted + "; do [ -f \"$p\" ] || continue; "
        "s=$(git hash-object -- \"$p\") || continue; "
        "[ \"$s\" = \"$(git rev-parse -q --verify \"HEAD:$p\" 2>/dev/null)\" ] && continue; "
        "git cat-file -e \"$s\" 2>/dev/null || continue; "
        "c=$($T git log --all -1 --format=%h --find-object=\"$s\" 2>/dev/null); "
        "echo \"have $s $c $p\"; done; "
        "git apply -R --whitespace=nowarn \"$f\" 2>/dev/null; rm -f \"$f\"")
    res = env.exec(script, timeout=600)
    hits = []
    for ln in res.stdout.splitlines():
        parts = ln.split(" ", 3)
        if len(parts) == 4 and parts[0] == "have":
            hits.append((parts[1], parts[3], parts[2]))   # commit is "" when unreachable
    return hits
