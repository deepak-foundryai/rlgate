#!/usr/bin/env python3
"""Materialise the labelled benchmark tasks under benchmarks/ into a layout rlgate reads.

    python benchmarks/fetch.py all --tier smoke --dry-run      # what would be fetched + approx size (no network)
    python benchmarks/fetch.py terminal-bench-2 --tier core    # git exports of the Harbor task dirs
    python benchmarks/fetch.py swe-bench-verified --tier core --pull-images
    python benchmarks/fetch.py all --only tb2-fix-git,tb21-fix-git
    python benchmarks/fetch.py all --tier all --dry-run        # also lists the bulk label sets

Needs: python3, PyYAML, git. Optional: huggingface_hub (+ pyarrow for parquet), docker.
Output (default ./rlgate-benchmarks/, outside the repo):
  <out>/<benchmark>/<id>/                     Harbor / DeepSWE / TB dirs exported as-is (git archive of one path)
  <out>/<benchmark>/<source-benchmark>.jsonl  SWE-style rows (one per entry) + image_name column
  <out>/<benchmark>/<id>/task.rlgate.yaml     single-function tasks (MBPP, HumanEval, BigCodeBench)
  <out>/LOCK.json                             resolved git shas / HF revisions
  <out>/<benchmark>/bulk/<set>.ids.txt        ids selected from vendored bulk label files (--tier all)
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from collections import defaultdict
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required: pip install pyyaml")

HERE = Path(__file__).resolve().parent
TASKS = Path("rlgate-benchmarks")      # overridden by --out
CACHE = TASKS / ".cache"


# ------------------------------------------------------------------------------------------ helpers
def sh(cmd, cwd=None, capture=False):
    r = subprocess.run(cmd, cwd=cwd, check=True, text=not capture or None,
                       stdout=subprocess.PIPE if capture else None)
    return r.stdout


def load(benchmark):
    """Merge benchmarks/<b>/labels.yaml files into one manifest-like dict."""
    folders = sorted(p.parent.name for p in HERE.glob("*/labels.yaml") if p.parent.name != "review-only")
    if benchmark != "all":
        if benchmark not in folders:
            sys.exit(f"unknown benchmark {benchmark!r}; one of: all, {', '.join(folders)}")
        folders = [benchmark]
    m = {"tasks": [], "datasets": {}, "bulk": []}
    for f in folders:
        d = yaml.safe_load((HERE / f / "labels.yaml").read_text(encoding="utf-8"))
        for t in d.get("tasks") or []:
            t["folder"] = f
            m["tasks"].append(t)
        m["datasets"].update(d.get("datasets") or {})
        for b in d.get("bulk") or []:
            b["folder"] = f
            m["bulk"].append(b)
    return m


def select(manifest, args):
    only = set(filter(None, (args.only or "").split(",")))
    if only:
        return [t for t in manifest["tasks"] if t["id"] in only]
    return [t for t in manifest["tasks"] if args.tier in t["tiers"]]


def iid_1776(iid):
    return iid.replace("__", "_1776_").lower()


def image_for(ds, row_or_id, tag=None):
    pat = ds.get("image", "")
    if "{" not in pat:
        return None
    if isinstance(row_or_id, dict):
        row = row_or_id
        iid = str(row.get(ds["id_field"], ""))
    else:
        row, iid = {}, str(row_or_id or "")
    vals = defaultdict(str, row)
    vals.update(iid_1776=iid_1776(iid), tag=tag or ds.get("default_tag", "latest"))
    m = re.match(r"(.+?)__(.+)-(\d+)$", iid)
    if m:
        vals.update(org=m.group(1), repo=m.group(2), number=m.group(3))
    try:
        img = pat.format_map(vals)
    except (KeyError, ValueError):
        return None
    return img if img and "{" not in img and not img.endswith(":") else None


# --------------------------------------------------------------------------------------- dry run
def describe(t, ds_map):
    f = t["fetch"]
    if f["kind"] == "git":
        where = f.get("path") or f.get("path_glob")
        return f"git  {f['repo'].split('github.com/')[-1]}@{f['ref'][:12]}:{where}", f.get("approx_mb", 5), None
    ds = ds_map[f["dataset"]]
    sel = f.get("id") if f.get("id") is not None else (
        f"split={f.get('split')} row={f.get('row_index')}" if "row_index" in f else
        f"split={f.get('split')} same instance as {f.get('same_instance_as')}")
    img = image_for(ds, f.get("id"), f.get("image_tag")) if f.get("id") is not None else \
        (ds.get("image", "").replace("{tag}", f.get("image_tag", "")) + "  (resolved from row)")
    return f"hf   {ds['hf']}@{ds['revision']} [{sel}]", None, img


def dry_run(manifest, tasks, args):
    ds_map = manifest["datasets"]
    by_bench = defaultdict(list)
    for t in tasks:
        by_bench[t["folder"]].append(t)
    git_mb, ds_used, images = 0.0, set(), {}
    print(f"tier={args.tier}  entries={len(tasks)}  "
          f"(positives {sum(t['polarity'] == 'positive' for t in tasks)}, controls {sum(t['polarity'] == 'negative' for t in tasks)})\n")
    for bench, ts in sorted(by_bench.items()):
        print(f"[{bench}]  {len(ts)} entr{'y' if len(ts) == 1 else 'ies'}  -> {TASKS.name}/{bench}/")
        for t in ts:
            what, mb, img = describe(t, ds_map)
            tag = "CTRL" if t["polarity"] == "negative" else t["category"]
            extra = "  needs: deep-review" if t.get("needs") else ""
            print(f"  {t['id'][:78]:<78}  {tag:<16} {what}{extra}")
            if mb:
                git_mb += mb
            if t["fetch"]["kind"] == "hf_row":
                ds_used.add(t["fetch"]["dataset"])
                if t["fetch"].get("alt"):
                    ds_used.add(t["fetch"]["alt"])
                images[img or t["id"]] = ds_map[t["fetch"]["dataset"]].get("image_gb", 2.0)
            elif t["fetch"].get("image"):
                images[t["fetch"]["image"]] = 4.0
        print()
    ds_mb = sum(ds_map[d].get("approx_mb", 5) for d in ds_used)
    gated = [d for d in ds_used if ds_map[d].get("gated")]
    print("approx download (no images):")
    print(f"  git exports  ~{git_mb:.0f} MB (blobless partial clones; first clone of a repo adds its tree metadata)")
    print(f"  HF datasets  ~{ds_mb:.0f} MB over {len(ds_used)} dataset(s): {', '.join(sorted(ds_used)) or '-'}")
    if gated:
        print(f"  gated (accept terms on HF first): {', '.join(gated)}")
    print("images (only with --pull-images; Harbor tasks build from their Dockerfile at run time instead):")
    print(f"  {len(images)} SWE-style image(s), ~{sum(images.values()):.0f} GB uncompressed")
    if args.tier == "all" and not args.only:
        bulk_dry(manifest)


# ------------------------------------------------------------------------------------- bulk refs
OPS = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, "<": lambda a, b: a < b,
       "==": lambda a, b: a == b, "in": lambda a, b: a in b}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def _where(row, conds):
    for k, op, v in conds:
        a = _num(row.get(k))
        b = v if isinstance(v, list) else _num(v)
        if isinstance(a, str) and isinstance(b, (int, float)):
            return False
        try:
            if not OPS[op](a, b):
                return False
        except TypeError:
            return False
    return True


def _local_rows(path):
    p = Path(os.path.expanduser(path))
    if not p.exists():
        return None
    if p.suffix == ".csv":
        with open(p, newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))
    if p.suffix == ".jsonl":
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    if p.suffix == ".json":
        d = json.loads(p.read_text())
        return d.get("tasks", d) if isinstance(d, dict) else d
    if p.suffix == ".txt":
        return [{"id": l.strip()} for l in p.read_text().splitlines() if l.strip() and not l.startswith("#")]
    return None


def bulk_select(ref):
    """Return {rule_index: [ids]} for refs whose file is local and whose rules are mechanical."""
    files = ref.get("file", "")
    if not files.startswith("upstream/"):
        return None
    paths = [str(HERE / ref["folder"] / files)]
    rows = []
    for p in paths:
        r = _local_rows(p)
        if r:
            rows += r
    if not rows:
        return None
    idf, labf = ref.get("id_field", "id"), ref.get("label_field")
    rules = ref["rules"] if isinstance(ref["rules"], list) else []
    out = {}
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        if "where" in rule:
            hit = [r for r in rows if isinstance(r, dict) and _where(r, rule["where"])]
        elif "label_contains" in rule and labf:
            hit = [r for r in rows if any(p in [x.strip().split("-")[0] for x in str(r.get(labf, "")).split(",")]
                                          for p in rule["label_contains"])]
        elif "label" in rule and labf:
            hit = [r for r in rows if str(r.get(labf, "")).strip() == rule["label"]]
        elif rule.get("all") is True:
            hit = rows
        else:
            continue
        out[i] = [str(r.get(idf) or r.get("task") or r.get("id")) for r in hit]
    return out


def bulk_dry(manifest):
    print("\nbulk label sets (not expanded; vendored files counted, remote ones listed):")
    for ref in manifest["bulk"]:
        sel = bulk_select(ref)
        if sel is None:
            print(f"  {ref['folder'] + '/' + ref['id']:<50} {ref.get('count', '')}  [{ref.get('url', 'rule only')}]")
            continue
        parts = []
        for i, ids in sel.items():
            r = ref["rules"][i]
            parts.append(f"{r.get('category') or r.get('polarity')}={len(set(ids))}")
        print(f"  {ref['folder'] + '/' + ref['id']:<50} " + ", ".join(parts) + f"  [{ref['file']}]")


def bulk_write(manifest):
    for ref in manifest["bulk"]:
        sel = bulk_select(ref)
        if not sel:
            continue
        out = TASKS / ref["folder"] / "bulk"
        out.mkdir(parents=True, exist_ok=True)
        with open(out / f"{ref['id']}.ids.txt", "w") as fh:
            for i, ids in sel.items():
                r = ref["rules"][i]
                fh.write(f"# rule {i}: {json.dumps(r)}\n")
                fh.writelines(f"{x}\n" for x in sorted(set(ids)))


# ------------------------------------------------------------------------------------------- git
def git_cache(repo):
    d = CACHE / "git" / re.sub(r"[^\w.-]+", "_", repo.split("github.com/")[-1])
    if not d.exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        sh(["git", "clone", "--filter=blob:none", "--no-checkout", "--quiet", repo, str(d)])
    return d


def git_export(t, lock):
    f = t["fetch"]
    d = git_cache(f["repo"])
    ref = f["ref"]
    try:
        sha = sh(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=d, capture=True).decode().strip()
    except subprocess.CalledProcessError:
        sh(["git", "fetch", "--quiet", "--filter=blob:none", "origin", ref], cwd=d)
        sha = sh(["git", "rev-parse", "FETCH_HEAD"], cwd=d, capture=True).decode().strip()
    path = f.get("path")
    if not path:  # glob, e.g. tasks/<t>/*/original_task
        names = sh(["git", "ls-tree", "-r", "-d", "--name-only", sha, str(Path(f["path_glob"]).parts[0])],
                   cwd=d, capture=True).decode().split()
        hits = sorted(n for n in names if fnmatch.fnmatch(n, f["path_glob"]))
        if not hits:
            raise RuntimeError(f"no path matches {f['path_glob']} at {sha[:12]}")
        path = hits[0]
    dest = TASKS / t["folder"] / t["id"]
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    data = sh(["git", "archive", "--format=tar", sha, path], cwd=d, capture=True)
    strip = len(Path(path).parts)
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        for m in tf.getmembers():
            parts = Path(m.name).parts[strip:]
            if not parts or ".." in parts or m.issym() or m.islnk() and ".." in m.linkname:
                continue
            m.name = str(Path(*parts))
            tf.extract(m, dest)
    lock[t["id"]] = {"repo": f["repo"], "ref": ref, "sha": sha, "path": path}


# -------------------------------------------------------------------------------------------- HF
def hf_rows(ds, patterns, lock_key, lock):
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        sys.exit("huggingface_hub is needed for HF rows: pip install huggingface_hub pyarrow")
    local = snapshot_download(repo_id=ds["hf"], repo_type="dataset", revision=ds["revision"],
                              allow_patterns=patterns, cache_dir=str(CACHE / "hf"))
    lock[lock_key] = {"hf": ds["hf"], "revision": ds["revision"], "local": local}
    rows = []
    for p in sorted(Path(local).rglob("*")):
        if not any(fnmatch.fnmatch(str(p.relative_to(local)), pat) for pat in patterns):
            continue
        if p.suffix == ".parquet":
            import pyarrow.parquet as pq
            rows += pq.read_table(p).to_pylist()
        elif p.suffix == ".jsonl":
            rows += [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return rows


def patterns_for(ds, f):
    out = []
    for pat in ds["files"]:
        vals = defaultdict(lambda: "*", split=f.get("split", "*"), lang=f.get("lang", "*"))
        m = re.match(r"(.+?)__(.+)-(\d+)$", str(f.get("id") or ""))
        if m:
            vals.update(org=m.group(1), repo=m.group(2))
        out.append(pat.format_map(vals))
    return out


def find_row(rows, ds, f):
    idf = ds["id_field"]
    if f.get("id") is None and "row_index" in f:
        return rows[f["row_index"]]
    want = f["id"]
    if f.get("id_as_given"):  # "org/repo:pr-N"
        m = re.match(r"(.+)/(.+):pr-(\d+)", want)
        hit = [r for r in rows if str(r.get("org")) == m.group(1) and str(r.get("repo")) == m.group(2)
               and str(r.get("number")) == m.group(3)]
    elif f.get("match") == "suffix":
        hit = [r for r in rows if re.search(rf"(^|[_/-]){re.escape(want)}$", str(r.get(idf, "")))]
    else:
        hit = [r for r in rows if str(r.get(idf)) == str(want)]
    if len(hit) != 1:
        raise RuntimeError(f"{ds['hf']}: {len(hit)} rows match {want!r} (refusing to guess)")
    return hit[0]


SINGLE_FN = {"mbpp", "humaneval", "bigcodebench"}


def write_single_fn(t, row, ds):
    """Wrap a single-function problem as a task.rlgate.yaml task (gold = reference solution)."""
    dest = TASKS / t["folder"] / t["id"]
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "repo").mkdir(parents=True)
    (dest / "hidden").mkdir()
    (dest / "solution").mkdir()
    b = t["benchmark"]
    if b == "mbpp":
        prompt, code = row["text"], row["code"]
        tests = "\n".join([row.get("test_setup_code") or "", "from solution import *", "",
                           "def test_task():"] + [f"    {a}" for a in row["test_list"]]) + "\n"
    elif b == "humaneval":
        prompt, code = row["prompt"], row["prompt"] + row["canonical_solution"]
        tests = f"from solution import *\n{row['test']}\n\ndef test_task():\n    check({row['entry_point']})\n"
    else:  # bigcodebench
        prompt, code = row["complete_prompt"], row["complete_prompt"] + row["canonical_solution"]
        tests = "from solution import *\n" + row["test"] + "\n"
    (dest / "TASK.md").write_text(f"Write `solution.py` in the workspace.\n\n{prompt}\n")
    (dest / "repo" / ".keep").write_text("")
    lines = code.rstrip("\n").split("\n")
    (dest / "solution" / "fix.patch").write_text(
        f"--- /dev/null\n+++ b/solution.py\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{l}\n" for l in lines))
    (dest / "hidden" / "test_task.py").write_text(tests)
    (dest / "Dockerfile").write_text(f"FROM {ds['image'].split(' ')[0]}\nRUN pip install --no-cache-dir pytest==8.3.3\nWORKDIR /app\n")
    spec = {"id": t["id"], "instruction_path": "TASK.md", "workspace": "./repo", "gold_patch": "./solution/fix.patch",
            "grader": {"run": "python -m pytest -q .graded", "image": "./Dockerfile",
                       "tests": [{"from": "./hidden", "to": ".graded"}]},
            "languages": ["python"], "meta": {"external_audit": t["id"], "source_row": row.get(ds["id_field"])}}
    (dest / "task.rlgate.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))


def fetch_hf(tasks, manifest, lock, pull):
    ds_map = manifest["datasets"]
    groups = defaultdict(list)
    for t in tasks:
        groups[t["fetch"]["dataset"]].append(t)
    for dname, ts in groups.items():
        ds = ds_map[dname]
        cache = {}

        def rows_for(f, ds=ds, dname=dname, cache=cache):
            pats = tuple(patterns_for(ds, f))
            if pats not in cache:
                cache[pats] = hf_rows(ds, list(pats), f"hf:{dname}:{','.join(pats)}", lock)
            return cache[pats]

        out = defaultdict(list)
        for t in ts:
            f = t["fetch"]
            try:
                if f.get("same_instance_as"):
                    src = f["same_instance_as"]
                    pos = find_row(rows_for({"split": src["split"]}), ds, {"row_index": src["row_index"]})
                    row = find_row(rows_for(f), ds, {"id": pos[ds["id_field"]]})
                else:
                    try:
                        row = find_row(rows_for(f), ds, f)
                    except RuntimeError:
                        if not f.get("alt"):
                            raise
                        ds_alt = ds_map[f["alt"]]
                        row = find_row(hf_rows(ds_alt, ds_alt["files"], f"hf:{f['alt']}", lock), ds_alt, f)
            except Exception as e:  # noqa: BLE001 - keep going; report at the end
                print(f"  ! {t['id']}: {e}", file=sys.stderr)
                continue
            row = {k: v for k, v in row.items()}
            img = image_for(ds, row, f.get("image_tag"))
            if img:
                row["image_name"] = img
            row["external_audit_id"] = t["id"]
            lock[t["id"]] = {"dataset": dname, "row_id": row.get(ds["id_field"]), "image": img}
            if t["benchmark"] in SINGLE_FN:
                write_single_fn(t, row, ds)
            else:
                out[(t["folder"], t["benchmark"])].append(row)
            if pull and img:
                subprocess.run(["docker", "pull", img], check=False)
        for (folder, bench), rows in out.items():
            d = TASKS / folder
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{bench}.jsonl"
            existing = {}
            if p.exists():
                existing = {r["external_audit_id"]: r for r in map(json.loads, p.read_text().splitlines()) if r}
            existing.update({r["external_audit_id"]: r for r in rows})
            p.write_text("".join(json.dumps(r, default=str) + "\n" for r in existing.values()))


# ------------------------------------------------------------------------------------------- main
def main():
    global TASKS, CACHE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("benchmark", help="a folder under benchmarks/ (e.g. swe-bench-verified) or 'all'")
    ap.add_argument("--tier", choices=["smoke", "core", "all"], default="smoke")
    ap.add_argument("--only", help="comma-separated entry ids (overrides --tier)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be fetched and the approx size; no network")
    ap.add_argument("--pull-images", action="store_true", help="docker pull SWE-style images")
    ap.add_argument("--out", default="rlgate-benchmarks", help="output dir (default ./rlgate-benchmarks)")
    args = ap.parse_args()
    TASKS = Path(args.out).resolve()
    CACHE = TASKS / ".cache"

    manifest = load(args.benchmark)
    tasks = select(manifest, args)
    if not tasks and not (args.tier == "all" and manifest["bulk"]):
        sys.exit("nothing selected for this tier (bulk-only benchmark? try --tier all)")
    if args.dry_run:
        dry_run(manifest, tasks, args)
        return

    TASKS.mkdir(parents=True, exist_ok=True)
    lock_p = TASKS / "LOCK.json"
    lock = json.loads(lock_p.read_text()) if lock_p.exists() else {}
    for t in tasks:
        if t["fetch"]["kind"] == "git":
            print(f"git  {t['id']}")
            try:
                git_export(t, lock)
            except Exception as e:  # noqa: BLE001 - one task's failure never stops the rest
                print(f"  ! {t['id']}: {e}", file=sys.stderr)
            if args.pull_images and t["fetch"].get("image"):
                subprocess.run(["docker", "pull", t["fetch"]["image"]], check=False)
    fetch_hf([t for t in tasks if t["fetch"]["kind"] == "hf_row"], manifest, lock, args.pull_images)
    lock_p.write_text(json.dumps(lock, indent=1, sort_keys=True))
    if args.tier == "all" and not args.only:
        bulk_write(manifest)
    print(f"done -> {TASKS}")


if __name__ == "__main__":
    main()
