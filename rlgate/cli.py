"""`rlgate` CLI. Stdlib argparse only — no runtime CLI deps."""

from __future__ import annotations

import argparse
import sys

from rlgate import __version__
from rlgate.config import load_dotenv
from rlgate.core.registry import discover
from rlgate.core.report import to_json, to_markdown


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # pick up DAYTONA_* (and anything else) from a local .env
    parser = argparse.ArgumentParser(prog="rlgate", description=__doc__)
    parser.add_argument("--version", action="version", version=f"rlgate {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list-probes", help="show all discovered probes")
    p_list.add_argument("--family", help="filter by family")

    p_audit = sub.add_parser("audit", help="audit an environment (path to descriptor or dir)")
    p_audit.add_argument("target")
    p_audit.add_argument("-k", type=int, default=5, help="baseline repetitions (default 5)")
    p_audit.add_argument("--backend", choices=["auto", "local", "docker", "daytona"],
                         default="auto",
                         help="sandbox backend (default auto: daytona for container tasks)")
    p_audit.add_argument("--probe", action="append", help="restrict to these probe ids")
    p_audit.add_argument("--format", choices=["md", "json"], default="md")
    p_audit.add_argument("--gate", action="store_true",
                         help="exit non-zero on any FIX/DROP (CI mode)")

    from rlgate.ingest.presets import preset_names
    p_bank = sub.add_parser("audit-bank", help="audit a whole task bank")
    p_bank.add_argument("target", help="dir of task.rlgate.yaml, a benchmark root, or a .jsonl")
    p_bank.add_argument("--preset", choices=preset_names(),
                        help="bundled benchmark mapping (else discover task.rlgate.yaml)")
    p_bank.add_argument("--mapping", help="a bank.rlgate.yaml mapping for a custom layout")
    p_bank.add_argument("--adapter", help="an rlgate.adapters entry-point name (escape hatch)")
    p_bank.add_argument("--limit", type=int, help="audit at most this many tasks")
    p_bank.add_argument("--format", choices=["report", "summary", "json"], default="report",
                        help="report: explained findings (default); summary: one-line table; json")
    p_bank.add_argument("--flagged-only", action="store_true",
                        help="hide tasks with no findings")
    p_bank.add_argument("--static", action="store_true",
                        help="static probes only — no sandbox/baseline (offline over real data)")
    p_bank.add_argument("-k", type=int, default=5)
    p_bank.add_argument("--backend", choices=["auto", "local", "docker", "daytona"],
                        default="auto")
    p_bank.add_argument("--probe", action="append", help="restrict to these probe ids")

    args = parser.parse_args(argv)

    if args.cmd == "list-probes":
        return _list_probes(args.family)
    if args.cmd == "audit":
        return _audit(args)
    if args.cmd == "audit-bank":
        return _audit_bank(args)
    return 2


def _list_probes(family: str | None) -> int:
    probes = discover()
    for pid in sorted(probes):
        meta = probes[pid].meta
        if family and meta.family != family:
            continue
        reqs = ",".join(c.value for c in meta.requires) or "-"
        print(f"{pid:42} {meta.kind.value:7} {meta.severity.name:8} needs[{reqs}]  {meta.doc}")
    return 0


def _audit(args) -> int:
    from rlgate.core.report import eqs, triage
    from rlgate.ingest import spec
    from rlgate.pipeline import audit

    task = spec.load(args.target)
    result = audit(task, k=args.k, only=set(args.probe) if args.probe else None,
                   backend=args.backend)

    if args.format == "json":
        print(to_json(result.task_id, result.findings, result.baseline_sane))
    else:
        print(to_markdown(result.task_id, result.findings, result.baseline_sane))

    if args.gate:
        t = triage(result.findings, result.baseline_sane)
        if t.value != "KEEP":
            print(f"\nGATE: {t.value} (eqs={eqs(result.findings, result.baseline_sane):.2f})",
                  file=sys.stderr)
            return 1
    return 0


def _audit_bank(args) -> int:
    from rlgate.core.report import eqs, triage
    from rlgate.ingest.bank import load_bank
    from rlgate.pipeline import audit

    tasks = load_bank(args.target, preset=args.preset, mapping=args.mapping,
                      adapter=args.adapter, limit=args.limit)
    if not tasks:
        print("no tasks found", file=sys.stderr)
        return 1

    only = set(args.probe) if args.probe else None
    results: list[tuple[str, object, Exception | None]] = []
    for task in tasks:
        try:
            results.append((task.id, audit(task, k=args.k, only=only, backend=args.backend,
                                            static_only=args.static), None))
        except Exception as e:  # noqa: BLE001 - one bad task must not sink the bank run
            results.append((task.id, None, e))

    if args.format == "json":
        print(_bank_json(results, args.static))
        return 0
    if args.format == "summary":
        _bank_summary(results, args.static)
        return 0
    _bank_report(results, args, eqs, triage)
    return 0


def _tier_label(v) -> str:
    from rlgate.core.finding import VerdictTier
    return {VerdictTier.CONFIRMED: "CONFIRMED", VerdictTier.REVIEW: "REVIEW",
            VerdictTier.DEFERRED: "DEFERRED"}[v]


def _bank_report(results, args, eqs, triage) -> None:
    mode = "static (on-disk probes only, no container)" if args.static else f"live ({args.backend})"
    src = f"preset: {args.preset}" if args.preset else (
        f"mapping: {args.mapping}" if args.mapping else "discover task.rlgate.yaml")
    print("rlgate task-bank audit")
    print(f"  bank:   {args.target}")
    print(f"  {src}   mode: {mode}")
    print(f"  tasks:  {len(results)}\n")
    print("Verdicts:  CONFIRMED = reproduced/proven   "
          "REVIEW = deterministic signal, needs human or LLM judgment")
    if not args.static:
        print("Triage:    KEEP = clean   FIX = fixable findings   DROP = broken baseline / critical")
    print()

    totals = {"CONFIRMED": 0, "REVIEW": 0, "DEFERRED": 0}
    families: dict[str, int] = {}
    flagged = errors = 0
    for tid, result, err in results:
        if err is not None:
            errors += 1
            print(f"=== {tid} ===  ERROR: {type(err).__name__}: {err}\n")
            continue
        findings = result.findings
        for f in findings:
            totals[_tier_label(f.verdict)] += 1
            families[f.probe_id.split(".")[0]] = families.get(f.probe_id.split(".")[0], 0) + 1
        if findings:
            flagged += 1
        elif args.flagged_only:
            continue

        if args.static:
            status = f"{len(findings)} finding(s) · baseline: not checked (static)"
        else:
            status = (f"{triage(findings, result.baseline_sane).value} · "
                      f"EQS {eqs(findings, result.baseline_sane):.2f} · "
                      f"baseline {'OK' if result.baseline_sane else 'BROKEN'}")
        print(f"=== {tid} ===  {status}")
        if not findings:
            print("  (no findings)\n")
            continue
        for f in sorted(findings, key=lambda x: x.severity, reverse=True):
            at = f" @ {f.location}" if f.location else ""
            print(f"  [{_tier_label(f.verdict)}] {f.severity.name}  {f.probe_id}{at}")
            print(f"      {f.title}")
            print(f"      why: {f.evidence.detail}")
            if f.fix_hint:
                print(f"      fix: {f.fix_hint}")
        print()

    print("Summary")
    print(f"  {len(results)} tasks | {flagged} flagged"
          + (f" | {errors} errored" if errors else ""))
    print(f"  findings: {totals['CONFIRMED']} CONFIRMED, {totals['REVIEW']} REVIEW"
          + (f", {totals['DEFERRED']} DEFERRED" if totals['DEFERRED'] else ""))
    if families:
        print("  by family: " + ", ".join(f"{k} {v}" for k, v in sorted(families.items())))
    if args.static and flagged:
        print("  next: re-run flagged tasks live (drop --static, add --backend daytona) "
              "to run the execution probes.")


def _bank_summary(results, static: bool) -> None:
    from rlgate.core.finding import VerdictTier
    print(f"{'task':40} {'baseline':9} {'CONF':>4} {'REV':>4}  top finding")
    print("-" * 90)
    tc = tr = flagged = 0
    for tid, result, err in results:
        if err is not None:
            print(f"{tid[:40]:40} {'ERROR':9} {'-':>4} {'-':>4}  {type(err).__name__}")
            continue
        conf = [f for f in result.findings if f.verdict is VerdictTier.CONFIRMED]
        rev = [f for f in result.findings if f.verdict is VerdictTier.REVIEW]
        tc += len(conf)
        tr += len(rev)
        flagged += bool(conf or rev)
        base = "static" if static else ("ok" if result.baseline_sane else "BROKEN")
        top = (conf + rev)[0].title[:48] if (conf or rev) else ""
        print(f"{tid[:40]:40} {base:9} {len(conf):>4} {len(rev):>4}  {top}")
    print("-" * 90)
    print(f"{len(results)} tasks | {flagged} flagged | {tc} CONFIRMED, {tr} REVIEW")


def _bank_json(results, static: bool) -> str:
    import json as _json

    from rlgate.core.report import _finding_json

    out = []
    for tid, result, err in results:
        if err is not None:
            out.append({"task": tid, "error": f"{type(err).__name__}: {err}"})
            continue
        out.append({
            "task": tid,
            "baseline_sane": result.baseline_sane,
            "mode": "static" if static else "live",
            "findings": [_finding_json(f) for f in result.findings],
        })
    return _json.dumps(out, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
