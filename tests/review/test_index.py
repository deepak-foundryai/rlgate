"""The L3 agents navigate by structure: a cached symbol index, outline / find_symbol /
read_symbol / find_refs tools, a 120-line read window, and old tool outputs masked."""

from __future__ import annotations

from types import SimpleNamespace

from rlgate.agentic import loop
from rlgate.agentic.bench import Bench
from rlgate.agentic.index import build, parse
from rlgate.core.task import GraderSpec, Task
from rlgate.llm import wire

PY = '''RATE = 3

class Cart:
    def __init__(self):
        self.items = []

    def total(self, discount=0):
        return sum(self.items) - discount


def helper(x):
    return Cart().total(x)
'''

JS = '''export class Store {
  constructor() {}
  add(item) {
    return item;
  }
}
export function load(path) {
  return path;
}
export const save = async (x) => x;
'''


def _bench(tmp_path) -> Bench:
    repo = tmp_path / "repo"
    (repo / "shop").mkdir(parents=True)
    (repo / "shop" / "cart.py").write_text(PY)
    (repo / "web").mkdir()
    (repo / "web" / "store.js").write_text(JS)
    (repo / "big.txt").write_text("\n".join(f"line {i}" for i in range(1, 501)))
    task = Task(id="t", source="test", repo_dir=repo, instruction="x",
                grader=GraderSpec(run="pytest", verdict="exit_code"))
    return Bench(task, SimpleNamespace(sandbox=None))


def test_python_and_js_definitions_are_indexed(tmp_path):
    py = {(s.name, s.kind, s.parent) for s in parse("a.py", PY)}
    assert {("RATE", "constant", ""), ("Cart", "class", ""), ("total", "method", "Cart"),
            ("helper", "function", "")} <= py
    js = {(s.name, s.kind) for s in parse("a.js", JS)}
    assert {("Store", "class"), ("add", "method"), ("load", "function"),
            ("save", "function")} <= js


def test_the_index_is_cached_by_content(tmp_path, monkeypatch):
    b = _bench(tmp_path)
    cache = tmp_path / "cache"
    first = build(b.repo, cache)
    assert len(list(cache.glob("*.json"))) == 1
    again = build(b.repo, cache)
    assert [s.name for s in again.symbols] == [s.name for s in first.symbols]
    (b.repo / "shop" / "new.py").write_text("def fresh():\n    pass\n")
    assert build(b.repo, cache).defs("fresh")
    assert len(list(cache.glob("*.json"))) == 2


def test_structure_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("RLGATE_STATE_DIR", str(tmp_path / "state"))
    b = _bench(tmp_path)
    out = b.call("outline", {"path": "shop/cart.py"})
    assert "3-8  class Cart:" in out and "def total(self, discount=0):" in out
    assert "shop/cart.py" in b.call("outline", {"path": "."})
    assert "shop/cart.py:7-8" in b.call("find_symbol", {"name": "Cart.total"})
    body = b.call("read_symbol", {"name": "helper"})
    assert "return Cart().total(x)" in body and "class Cart" not in body
    refs = b.call("find_refs", {"name": "Cart"})
    assert refs.count("shop/cart.py") == 2
    window = b.call("read_file", {"path": "big.txt"})
    assert "line 120" in window and "line 121" not in window and "380 more lines" in window
    # an edited file is outlined from the candidate, not the index
    b.call("write_file", {"path": "shop/cart.py", "content": PY + "\ndef extra():\n    pass\n"})
    assert "def extra():" in b.call("outline", {"path": "shop/cart.py"})


def test_old_tool_outputs_are_masked_in_batches():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for i in range(loop.KEEP + loop.STEP):
        msgs.append({"role": "tool", "name": "read_file", "content": f"out {i}"})
    assert loop.mask(msgs) == 0                       # not yet: still within the batch
    msgs.append({"role": "tool", "name": "read_file", "content": "newest"})
    n = loop.mask(msgs)
    assert n == loop.STEP + 1
    tools = [m for m in msgs if m["role"] == "tool"]
    assert all("output removed" in m["content"] for m in tools[:n])
    assert [m["content"] for m in tools[n:]][-1] == "newest"
    assert loop.mask(msgs) == 0                       # stable until the next batch
    sent = wire(msgs, "openai/gpt-x")
    assert not any("_masked" in m for m in sent)


def test_anthropic_prefix_is_marked_for_caching():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"}]
    sent = wire(msgs, "anthropic/claude-z")
    assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent[1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent[2]["content"] == "a"
    assert wire(msgs, "openai/gpt-x")[0]["content"] == "s"
