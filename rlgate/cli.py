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
    from rlgate.core.finding import VerdictTier
    from rlgate.ingest.bank import load_bank
    from rlgate.pipeline import audit

    tasks = load_bank(args.target, preset=args.preset, mapping=args.mapping,
                      adapter=args.adapter, limit=args.limit)
    if not tasks:
        print("no tasks found", file=sys.stderr)
        return 1

    only = set(args.probe) if args.probe else None
    totals = {VerdictTier.CONFIRMED: 0, VerdictTier.REVIEW: 0}
    print(f"{'task':40} {'baseline':9} {'CONF':>4} {'REV':>4}  top finding")
    print("-" * 90)
    flagged = 0
    for task in tasks:
        try:
            result = audit(task, k=args.k, only=only, backend=args.backend,
                           static_only=args.static)
        except Exception as e:  # noqa: BLE001 - one bad task must not sink the bank run
            print(f"{task.id[:40]:40} {'ERROR':9} {'-':>4} {'-':>4}  {type(e).__name__}: {e}")
            continue
        conf = [f for f in result.findings if f.verdict is VerdictTier.CONFIRMED]
        rev = [f for f in result.findings if f.verdict is VerdictTier.REVIEW]
        totals[VerdictTier.CONFIRMED] += len(conf)
        totals[VerdictTier.REVIEW] += len(rev)
        flagged += bool(conf or rev)
        base = "ok" if result.baseline_sane else ("static" if args.static else "BROKEN")
        top = (conf + rev)[0].title[:48] if (conf or rev) else ""
        print(f"{task.id[:40]:40} {base:9} {len(conf):>4} {len(rev):>4}  {top}")

    print("-" * 90)
    print(f"{len(tasks)} tasks | {flagged} flagged | "
          f"{totals[VerdictTier.CONFIRMED]} CONFIRMED, {totals[VerdictTier.REVIEW]} REVIEW")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
