"""Declarative task-bank mapping: turn ANY benchmark layout into ``Task``s with a small config,
no per-benchmark Python. A mapping has two parts:

  enumerate: how to find the task set
    dirs:    <glob> | [<glob>, ...]   # dir-per-task (first glob that matches wins)
    records: <glob> | "<file>"        # row-per-task (jsonl / csv); or point target at the file

  map: each Task field <- a source expression
    "<template>"                      # literal, with {dir.name} / {field.<col>} substitution
    {file: "<relpath>"}               # raw contents of a file under the task dir
    {find: {file: "<relpath>", key: "<name>"}}   # recursive key lookup in a json/toml/yaml file
    {field: "<col>"}                  # a column from the current record
    {first_existing: ["<relpath>", ...]}         # absolute path of the first that exists
    {first_existing_file: [...]}      # contents of the first file that exists
    {literal: <value>}

Fields: id, instruction, workspace, gold_patch, grader_run, grader_image, languages, source.
Everything downstream consumes the resulting ``Task`` and gates on capabilities — the mapping is
the only benchmark-specific thing, and it is data, not code.
"""

from __future__ import annotations

import csv
import json
import re
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import yaml

from rlgate.core.task import GraderSpec, Patch, Task

_TPL = re.compile(r"\{([^}]+)\}")


@dataclass
class _Ctx:
    vars: dict[str, object]
    base: Path


def _tpl(s: str, variables: dict[str, object]) -> str:
    return _TPL.sub(lambda m: str(variables.get(m.group(1), "")), s)


def _find_key(obj: object, key: str) -> object | None:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


def _parse_structured(path: Path) -> object:
    text = path.read_text(errors="ignore")
    if path.suffix == ".toml":
        return tomllib.loads(text)
    if path.suffix in (".yaml", ".yml"):
        return yaml.safe_load(text)
    return json.loads(text)


@dataclass
class BankMapping:
    name: str
    enumerate: dict
    map: dict

    @classmethod
    def from_dict(cls, d: dict, name: str | None = None) -> BankMapping:
        return cls(name=d.get("name", name or "bank"),
                   enumerate=d.get("enumerate", {}), map=d.get("map", {}))

    @classmethod
    def from_file(cls, path: str | Path) -> BankMapping:
        p = Path(path)
        return cls.from_dict(yaml.safe_load(p.read_text()), name=p.stem)

    # -- resolution -----------------------------------------------------------------

    def resolve_all(self, target: str | Path) -> Iterator[Task]:
        target = Path(target)
        if "dirs" in self.enumerate:
            yield from self._from_dirs(target)
        elif "records" in self.enumerate or target.is_file():
            yield from self._from_records(target)

    def _from_dirs(self, target: Path) -> Iterator[Task]:
        globs = self.enumerate["dirs"]
        globs = [globs] if isinstance(globs, str) else globs
        matched: list[Path] = []
        for g in globs:
            matched = sorted(p for p in target.glob(g) if p.is_dir())
            if matched:
                break
        for d in matched:
            if d.name.startswith((".", "_")):
                continue
            task = self._build(_Ctx(vars={"dir.name": d.name}, base=d))
            if task is not None:
                yield task

    def _from_records(self, target: Path) -> Iterator[Task]:
        files = [target] if target.is_file() else sorted(
            target.glob(self.enumerate.get("records", "*.jsonl")))
        for f in files:
            for row in _read_records(f):
                variables = {f"field.{k}": v for k, v in row.items()}
                task = self._build(_Ctx(vars=variables, base=f.parent))
                if task is not None:
                    yield task

    def _build(self, ctx: _Ctx) -> Task | None:
        m = self.map
        task_id = str(self._resolve(m.get("id", "{dir.name}"), ctx) or ctx.base.name)
        workspace = self._resolve(m.get("workspace", "."), ctx) or "."
        repo_dir = (ctx.base / str(workspace)).resolve()
        gold_text = self._resolve(m.get("gold_patch"), ctx)
        grader = GraderSpec(
            run=str(self._resolve(m.get("grader_run"), ctx) or "true"),
            image=_opt_str(self._resolve(m.get("grader_image"), ctx)),
        )
        return Task(
            id=task_id,
            source=self.name,
            repo_dir=repo_dir,
            grader=grader,
            instruction=_opt_str(self._resolve(m.get("instruction"), ctx)),
            gold_patch=Patch(str(gold_text)) if gold_text else None,
            languages=frozenset(_as_langs(self._resolve(m.get("languages"), ctx))),
            meta={"instance_id": task_id},
        )

    def _resolve(self, src: object, ctx: _Ctx) -> object | None:
        if src is None:
            return None
        if isinstance(src, str):
            return _tpl(src, ctx.vars)
        if not isinstance(src, dict):
            return src
        if "literal" in src:
            return src["literal"]
        if "template" in src:
            return _tpl(src["template"], ctx.vars)
        if "field" in src:
            return ctx.vars.get(f"field.{src['field']}")
        if "file" in src:
            p = ctx.base / _tpl(src["file"], ctx.vars)
            return p.read_text(errors="ignore") if p.is_file() else None
        if "find" in src:
            spec = src["find"]
            p = ctx.base / _tpl(spec["file"], ctx.vars)
            if not p.is_file():
                return None
            try:
                return _find_key(_parse_structured(p), spec["key"])
            except (ValueError, OSError):
                return None
        if "first_existing" in src:
            for rel in src["first_existing"]:
                p = ctx.base / _tpl(rel, ctx.vars)
                if p.exists():
                    return str(p.resolve())
            return None
        if "first_existing_file" in src:
            for rel in src["first_existing_file"]:
                p = ctx.base / _tpl(rel, ctx.vars)
                if p.is_file():
                    return p.read_text(errors="ignore")
            return None
        return None


def _opt_str(v: object) -> str | None:
    return str(v) if v not in (None, "") else None


def _as_langs(v: object) -> list[str]:
    if not v:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [part for part in re.split(r"[,\s]+", str(v)) if part]


def _read_records(f: Path) -> Iterator[dict]:
    if f.suffix == ".csv":
        yield from csv.DictReader(f.read_text().splitlines())
        return
    for line in f.read_text().splitlines():      # jsonl
        line = line.strip()
        if line:
            yield json.loads(line)
