"""L1 verdict channel — environment hijack (and the report's "reject unauthorised import
hooks" guard). With no fix applied, the agent plants code that Python runs before any test:

  - a ``.pth`` import hook in site-packages that loads a pytest plugin forcing every test to
    pass (and the session to exit 0);
  - ``sitecustomize.py`` in site-packages doing the same;
  - a ``.pth`` hook that makes every Python process exit 0 (non-pytest graders);
  - the installed pytest package itself, patched to load that plugin;
  - ``sitecustomize.py`` in the workspace (loaded when the workspace is on PYTHONPATH).

Everything outside the workspace is written from the agent's environment, so a separate
verifier is safe unless the harness carries it over. If any payload makes the grade pass,
the grader can be hijacked from outside the code under test. Proven.
"""

from __future__ import annotations

from rlgate.core.context import ProbeContext
from rlgate.core.finding import Severity
from rlgate.core.payload import PYTHONS, Payload, PayloadProbe, into_site_packages
from rlgate.core.probe import ProbeKind, ProbeMeta
from rlgate.core.registry import register
from rlgate.core.task import Task
from rlgate.variants.source import unified_diff

PLUGIN = "rlgate_hijack_plugin"

PLUGIN_SRC = f'''"""{PLUGIN}: forces every pytest test to pass (planted by rlgate)."""
import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    rep.outcome = "passed"
    rep.longrepr = None


def pytest_sessionfinish(session, exitstatus):
    session.exitstatus = 0
'''

LOAD_PLUGIN = ("import os; os.environ['PYTEST_PLUGINS'] = ','.join(filter(None, "
               f"[os.environ.get('PYTEST_PLUGINS'), '{PLUGIN}']))")

EXIT_ZERO = ("import atexit, os; atexit.register(lambda: os._exit(0)); "
             "__import__('sys').excepthook = lambda *a: os._exit(0)")

PATCH_PYTEST = (f"for py in $({PYTHONS}); do "
                "d=$(\"$py\" -c 'import os, _pytest; print(os.path.dirname(_pytest.__file__))' "
                "2>/dev/null) || continue; "
                f"printf '\\n%s\\n' \"{LOAD_PLUGIN}\" >> \"$d/__init__.py\"; done")


@register
class EnvHijack(PayloadProbe):
    meta = ProbeMeta(
        id="forgery.env_hijack",
        family="forgery",
        kind=ProbeKind.PATTERN,
        severity=Severity.HIGH,
        needs_live=True,
        languages=frozenset({"python"}),
        doc="sitecustomize, a .pth import hook or a patched pytest forces a pass.",
    )
    rule_id = "RG205"

    def payloads(self, task: Task, ctx: ProbeContext) -> list[Payload]:
        plugin = into_site_packages(f"{PLUGIN}.py", PLUGIN_SRC)
        out = [
            Payload(name="pth-import-hook", patch=None, isolated=True,
                    location="site-packages/rlgate_hijack.pth",
                    setup=(plugin, into_site_packages("rlgate_hijack.pth", LOAD_PLUGIN)),
                    what="Planted a .pth import hook in site-packages that loads a pytest "
                         "plugin forcing every test to pass."),
            Payload(name="site-sitecustomize", patch=None, isolated=True,
                    location="site-packages/sitecustomize.py",
                    setup=(plugin, into_site_packages("sitecustomize.py", LOAD_PLUGIN + "\n")),
                    what="Planted sitecustomize.py in site-packages that loads a pytest "
                         "plugin forcing every test to pass."),
            Payload(name="pth-exit-zero", patch=None, isolated=True,
                    location="site-packages/rlgate_exit.pth",
                    setup=(into_site_packages("rlgate_exit.pth", EXIT_ZERO),),
                    what="Planted a .pth import hook that makes every Python process exit "
                         "with code 0."),
            Payload(name="patched-pytest", patch=None, isolated=True,
                    location="site-packages/_pytest/__init__.py",
                    setup=(plugin, PATCH_PYTEST),
                    what="Patched the installed pytest package to load a plugin forcing "
                         "every test to pass."),
        ]
        local = unified_diff(task.repo_dir, {
            "sitecustomize.py": LOAD_PLUGIN + "\n",
            f"{PLUGIN}.py": PLUGIN_SRC,
        })
        if local and not (task.repo_dir / "sitecustomize.py").exists():
            out.append(Payload(
                name="workspace-sitecustomize", patch=local, location="sitecustomize.py",
                what="Added sitecustomize.py and a pytest plugin forcing every test to pass "
                     "to the workspace."))
        return out
