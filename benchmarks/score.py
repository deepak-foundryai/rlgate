#!/usr/bin/env python3
"""Score rlgate runs against the labels in benchmarks/, and publish a cleaned copy.

    python benchmarks/score.py ~/rlgate-bench-runs/smoke-nollm --tier smoke
    python benchmarks/score.py ~/rlgate-bench-runs/smoke-nollm --tier smoke \\
        --publish benchmarks/results/0.1.1/smoke-no-llm

The argument is one rlgate run folder or a folder of them (a --jobs-dir). Each task in a
run is matched to its label by entry id (the folder name fetch.py gives it) or, for SWE
rows, by the upstream instance id when only one label in that benchmark has it.

Scoring (benchmarks/README.md):
  positive  found      a rule in expected_rules is reported, proven or needs-your-judgment
            warning    an expected rule is reported only as a warning
            missed     rlgate ran the task and reported none of them
            not run    rlgate could not check it (the error is kept)
            absent     not in these runs (not fetched, or not run yet)
            deep-only  every expected rule needs --deep-review and the run had none
  control   clean      no rule in clean_for fired
            failed     a rule in clean_for fired: a false positive

--publish writes SCORE.md, score.json and tasks/<entry>/{report.md,result.json} with local
paths removed, ready to commit. It refuses if a local path is left in anything it writes.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
TIERS = ("smoke", "core", "all")
LOCAL = re.compile(r"(/Users/|/home/|/private/|/var/folders/|[A-Za-z]:\\\\Users\\\\)")


# -- labels ---------------------------------------------------------------------------------

def load_labels() -> list[dict]:
    out = []
    for f in sorted(HERE.glob("*/labels.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for t in d.get("tasks") or []:
            t["folder"] = f.parent.name
            out.append(t)
    return out


def upstream_id(t: dict) -> str | None:
    f = t.get("fetch") or {}
    return f.get("id") or (Path(f["path"]).name if f.get("path") else None)


def deep_only(t: dict) -> bool:
    return "deep-review" in str(t.get("needs") or "")


# -- runs -----------------------------------------------------------------------------------

def run_dirs(arg: Path) -> list[Path]:
    if (arg / "result.json").exists():
        return [arg]
    return sorted(p.parent for p in arg.glob("*/result.json"))


def task_results(run: Path) -> tuple[dict, list[tuple[dict, Path | None]]]:
    cfg = json.loads((run / "config.json").read_text()) if (run / "config.json").exists() else {}
    top = json.loads((run / "result.json").read_text())
    results = top.get("results", [top])
    out = []
    for r in results:
        d = run / "tasks" / _slug(r["task"])
        out.append((r, d if d.exists() else None))
    return cfg, out


def _slug(s: str) -> str:            # as rlgate.core.jobs._slug
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-.")[:60] or "run"


def match(task_id: str, folder_hint: str, labels: list[dict]) -> dict | None:
    by_id = [t for t in labels if t["id"] == task_id]
    if by_id:
        return by_id[0]
    ups = [t for t in labels if upstream_id(t) == task_id]
    if folder_hint:
        ups = [t for t in ups if t["folder"] == folder_hint] or ups
    return ups[0] if len(ups) == 1 else None


# -- scoring --------------------------------------------------------------------------------

def score(label: dict, res: dict | None, deep: bool) -> dict:
    row = {"id": label["id"], "benchmark": label["folder"], "polarity": label["polarity"],
           "category": label.get("category", ""), "confidence": label.get("confidence", ""),
           "label": label.get("label", ""), "expected": label.get("expected_rules") or [],
           "clean_for": label.get("clean_for") or [], "deep_only": deep_only(label),
           "source": [s.get("url") for s in label.get("source") or []]}
    if res is None:
        return {**row, "outcome": "absent", "why": "not in these runs"}
    if "error" in res:
        return {**row, "outcome": "not run", "why": res["error"], "verdict": res.get("verdict")}
    findings = [f for f in res.get("findings") or [] if f.get("status") != "accepted"]
    strong = {f["rule"] for f in findings if f.get("kind") in ("proven", "judgment")}
    weak = {f["rule"] for f in findings if f.get("kind") == "warning"}
    row.update(verdict=res.get("verdict"), reported=sorted(strong | weak))
    if label["polarity"] == "negative":
        bad = sorted(set(row["clean_for"]) & strong)
        return {**row, "outcome": "failed" if bad else "clean", "fired": bad}
    exp = set(row["expected"])
    if exp & strong:
        return {**row, "outcome": "found", "matched": sorted(exp & strong)}
    if row["deep_only"] and not deep:
        return {**row, "outcome": "deep-only", "why": "needs --deep-review"}
    if exp & weak:
        return {**row, "outcome": "warning", "matched": sorted(exp & weak)}
    return {**row, "outcome": "missed"}


def summarise(rows: list[dict]) -> dict:
    pos = [r for r in rows if r["polarity"] == "positive"]
    ctl = [r for r in rows if r["polarity"] == "negative"]
    scored = [r for r in pos if r["outcome"] in ("found", "warning", "missed")]
    found = sum(r["outcome"] == "found" for r in scored)
    hi = [r for r in scored if r["confidence"] == "high"]
    return {
        "positives": len(pos), "controls": len(ctl),
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "recall": round(found / len(scored), 3) if scored else None,
        "recall_high_confidence": (round(sum(r["outcome"] == "found" for r in hi) / len(hi), 3)
                                   if hi else None),
        "scored_positives": len(scored),
        "controls_failed": sum(r["outcome"] == "failed" for r in ctl),
        "controls_scored": sum(r["outcome"] in ("clean", "failed") for r in ctl),
    }


# -- output ---------------------------------------------------------------------------------

def markdown(rows: list[dict], summ: dict, meta: dict) -> str:
    def pct(x):
        return "-" if x is None else f"{x:.0%}"
    mode = " with --deep-review" if meta["deep"] else " (no LLM)"
    oc = summ["outcomes"]
    found = f"({oc.get('found', 0)} of {summ['scored_positives']})"
    ctl = f"{summ['controls_failed']} of {summ['controls_scored']}"
    L = [f"# rlgate {meta['rlgate']} on the {meta['tier']} tier{mode}", "",
         (f"Scored {meta['date']}. Labels: `benchmarks/*/labels.yaml`. Scoring rules: "
         "[benchmarks/README.md](../../../README.md#score)."), "",
         "| | |", "|---|---|",
         f"| Recall (flawed tasks rlgate ran) | **{pct(summ['recall'])}** {found} |",
         f"| Recall, high-confidence labels | {pct(summ['recall_high_confidence'])} |",
         f"| Controls with a false positive | {ctl} |",
         f"| Could not run (layout or backend) | {oc.get('not run', 0)} |",
         f"| Needs --deep-review (not scored here) | {oc.get('deep-only', 0)} |",
         f"| In the tier but not in these runs | {oc.get('absent', 0)} |",
         "", "## By benchmark", "",
         ("| Benchmark | Found | Warning only | Missed | Could not run | Deep-only | Absent "
         "| Controls clean |"), "|---|---|---|---|---|---|---|---|"]
    by = defaultdict(list)
    for r in rows:
        by[r["benchmark"]].append(r)
    for b, rs in sorted(by.items()):
        c = Counter(r["outcome"] for r in rs)
        ctl = [r for r in rs if r["polarity"] == "negative"]
        L.append(f"| {b} | {c['found']} | {c['warning']} | {c['missed']} | {c['not run']} | "
                 f"{c['deep-only']} | {c['absent']} | {c['clean']} of {len(ctl)} |")
    L += ["", "## By category", "", "| Category | Found | Scored |", "|---|---|---|"]
    cat = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["polarity"] == "positive" and r["outcome"] in ("found", "warning", "missed"):
            cat[r["category"]][1] += 1
            cat[r["category"]][0] += r["outcome"] == "found"
    L += [f"| {k} | {a} | {n} |" for k, (a, n) in sorted(cat.items())]
    L += ["", "## Every task", "",
          "| Entry | Kind | Category | Expected | rlgate reported | Outcome |",
          "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["benchmark"], r["id"])):
        kind = "control" if r["polarity"] == "negative" else "flawed"
        exp = ", ".join(r["clean_for"] if kind == "control" else r["expected"])
        rep = ", ".join(r.get("reported") or []) or "-"
        why = r.get("why") if r["outcome"] == "not run" else None
        out = r["outcome"] + (f": {why}" if why else "")
        link = f"[{r['id']}](tasks/{r['id']}/report.md)" if r.get("published") else r["id"]
        L.append(f"| {link} | {kind} | {r['category']} | {exp} | {rep} | "
                 f"{out.replace('|', '/')[:160]} |")
    note = ("Expected for a control lists the rules that must not fire. Findings outside a "
            "flawed task's expected rules are unlabelled, not false positives: the audits "
            "only report what they looked for.")
    L += ["", note, ""]
    return "\n".join(L)


def scrub(text: str, roots: list[str]) -> str:
    for root, name in sorted(((r, n) for r, n in roots), key=lambda x: -len(x[0])):
        text = text.replace(root.rstrip("/") + "/", name + "/").replace(root.rstrip("/"), name)
    home = str(Path.home())
    return text.replace(home, "~")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", type=Path, nargs="+", help="run folders, or a --jobs-dir")
    ap.add_argument("--tier", choices=TIERS, default="smoke")
    ap.add_argument("--only-run", action="store_true",
                    help="score only labels found in the runs (skip 'not in these runs')")
    ap.add_argument("--publish", type=Path, help="write a cleaned copy here (in the repo)")
    a = ap.parse_args()

    labels = [t for t in load_labels() if a.tier in (t.get("tiers") or [])]
    seen: dict[str, tuple[dict, Path | None]] = {}
    unmatched, versions, deep_flags, roots = [], set(), set(), []
    for arg in a.runs:
        for run in run_dirs(arg.expanduser()):
            cfg, results = task_results(run)
            opts = cfg.get("options") or {}
            deep_flags.add(bool(opts.get("deep_review")))
            target = cfg.get("target", "")
            if target:
                roots.append((str(Path(target).parent), "<benchmarks>"))
            roots.append((str(run.parent), "<runs>"))
            hint = Path(target).name if target else ""
            for res, d in results:
                versions.add(res.get("rlgate", "?"))
                lab = match(res["task"], hint, labels)
                if lab is None:
                    unmatched.append(f"{run.name}: {res['task']}")
                    continue
                seen[lab["id"]] = (res, d)
    if len(deep_flags) > 1:
        print("! runs mix --deep-review and plain runs; score them separately", file=sys.stderr)
    deep = any(deep_flags)
    pool = [t for t in labels if t["id"] in seen] if a.only_run else labels
    rows = [score(t, seen.get(t["id"], (None, None))[0], deep) for t in pool]
    summ = summarise(rows)
    from datetime import date
    meta = {"rlgate": ", ".join(sorted(versions)) or "?", "tier": a.tier, "deep": deep,
            "date": date.today().isoformat(), "unmatched": unmatched}

    if a.publish:
        out = a.publish
        if out.exists():
            shutil.rmtree(out)
        (out / "tasks").mkdir(parents=True)
        for r in rows:
            res, d = seen.get(r["id"], (None, None))
            if res is None:
                continue
            td = out / "tasks" / r["id"]
            td.mkdir()
            (td / "result.json").write_text(scrub(json.dumps(res, indent=2), roots) + "\n")
            if d is not None and (d / "report.md").exists():
                (td / "report.md").write_text(scrub((d / "report.md").read_text(), roots))
            r["published"] = (td / "report.md").exists()
        (out / "SCORE.md").write_text(scrub(markdown(rows, summ, meta), roots))
        (out / "score.json").write_text(scrub(json.dumps(
            {"meta": meta, "summary": summ, "tasks": rows}, indent=2), roots) + "\n")
        leaks = [str(p) for p in out.rglob("*") if p.is_file() and LOCAL.search(p.read_text())]
        if leaks:
            shutil.rmtree(out)
            print("refused: local paths left in " + ", ".join(leaks), file=sys.stderr)
            return 1
        print(f"wrote {out}/SCORE.md")
    else:
        print(markdown(rows, summ, meta))
    for u in unmatched:
        print(f"  ? no label for {u}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
