"""The user-facing surface: commands and output.

Runs the real CLI against examples/taskbank (host tasks: full runs need no Docker) and the
bundled demo task (a Harbor task: quick runs).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from rlgate.cli import main

BANK = "examples/taskbank"
WEAK = "examples/taskbank/weak_grader"
CLEAN = "examples/taskbank/clean"
DEMO = Path("rlgate/demo/rate-limiter")
STEPS = ["Step 1 Build", "Step 2 Baseline", "Step 3 Leaks", "Step 4 Grader",
         "Step 5 Cheating", "Step 6 Fairness"]
FORBIDDEN = re.compile(r"\b(static|live|CONFIRMED|REVIEW|KEEP|DROP|EQS)\b")


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


@pytest.fixture
def demo(tmp_path, monkeypatch) -> str:
    monkeypatch.chdir(tmp_path)
    shutil.copytree(Path(__file__).resolve().parents[2] / DEMO, tmp_path / "rate-limiter")
    return "./rate-limiter"


# -- the check output ----------------------------------------------------------------------

def _flat(text: str) -> str:
    return " ".join(text.split())


def test_fail_fast_stops_at_the_first_failure(capsys):
    code, out, err = run(capsys, "check", WEAK, "--runs", "1", "-x")
    assert code == 1, out + err                         # needs fixes
    flat = _flat(out)
    assert f"rlgate check {WEAK}" in out and "full run" in out
    for step in STEPS:
        assert step in flat
    assert "Step 1 Build ✓ passed task builds" in flat
    assert "Step 2 Baseline ✓ passed gold passes 1/1, do-nothing fails 1/1" in flat
    assert "Step 4 Grader ✗ failed" in flat
    assert "Step 5 Cheating - not run fix step 4 first" in flat
    assert "Step 6 Fairness - not run fix step 4 first" in flat
    assert "STOPPED AT STEP 4 of 6 · NEEDS FIXES" in flat
    assert "RG101 A part of the fix is not tested PROVEN · high" in flat
    assert f"More: rlgate explain RG101 {WEAK}" in flat
    assert f"After fixing, run: rlgate check {WEAK} (resumes at step 4)" in flat
    assert not FORBIDDEN.search(out)


def test_later_steps_run_by_default_and_every_finding_says_how_to_explain_it(capsys):
    code, out, _ = run(capsys, "check", WEAK, "--runs", "1")
    flat = _flat(out)
    assert code == 1
    assert "not run" not in flat and "Step 5 Cheating" in flat
    assert "ALL 6 STEPS RUN · STEPS 4, 5 FAILED · NEEDS FIXES" in flat
    for rid in ("RG101", "RG104", "RG202", "RG204"):          # 3 in full, the rest listed
        assert f"rlgate explain {rid} {WEAK}" in flat, rid
    assert not FORBIDDEN.search(out)
    # the old flag still works, as a no-op with a notice
    _, out2, err = run(capsys, "check", WEAK, "--runs", "1", "--keep-going")
    assert "ALL 6 STEPS RUN" in _flat(out2) and "now the default" in _flat(err)


def test_clean_task_is_ready_and_exits_0(capsys):
    code, out, _ = run(capsys, "check", CLEAN, "--runs", "1")
    assert code == 0
    assert "ALL 6 STEPS PASSED · READY" in _flat(out)


def test_step_runs_one_step_and_from_starts_at_one(capsys):
    run(capsys, "check", CLEAN, "--runs", "1")
    _, out, err = run(capsys, "check", CLEAN, "--runs", "1", "--only", "leaks")
    flat = _flat(out)
    assert "Step 3 Leaks ✓ passed" in flat
    assert "Step 4 Grader - not run not part of this run" in flat
    assert "from the last run" in flat                  # steps 1-2 reused, unchanged
    assert "resuming at step 3" in _flat(err)
    _, out, _ = run(capsys, "check", CLEAN, "--runs", "1", "--from", "cheating")
    flat = _flat(out)
    assert "Step 5 Cheating ✓ passed" in flat and "Step 6 Fairness" in flat
    _, out, _ = run(capsys, "check", CLEAN, "--runs", "1", "--only", "3")   # numbers too
    assert "Step 3 Leaks ✓ passed" in _flat(out)
    code, _, err = run(capsys, "check", CLEAN, "--from", "9")
    assert code == 2 and "no such step" in _flat(err)
    code, out, err = run(capsys, "check", CLEAN, "--runs", "1", "--step", "3")  # old name
    assert "Step 3 Leaks ✓ passed" in _flat(out) and "--only N" in _flat(err)


def test_recheck_after_a_fix_reuses_unchanged_steps(tmp_path, capsys):
    task = tmp_path / "weak_grader"
    shutil.copytree(Path(__file__).resolve().parents[2] / WEAK, task)
    _, out, _ = run(capsys, "check", str(task), "--runs", "1", "-x")
    assert "STOPPED AT STEP 4" in _flat(out)
    # the fix: the grader now also checks g(), on held-out inputs, pinning the type, with a
    # reward file (the clean example's grader). It changes the tests, so step 2 (which
    # grades with them) must run again; step 1 (the build) didn't change and is reused.
    clean = Path(__file__).resolve().parents[2] / CLEAN
    (task / "repo" / "check.py").unlink()
    shutil.copytree(clean / "hidden", task / "hidden")
    (task / "task.rlgate.yaml").write_text(
        (clean / "task.rlgate.yaml").read_text().replace("id: clean", "id: weak_grader"))
    _, out, err = run(capsys, "check", str(task), "--runs", "1")
    flat, ferr = _flat(out), _flat(err)
    assert "Step 1 unchanged since the last check" in ferr
    assert "step 2 runs again because its files changed" in ferr
    assert "Step 1 Build ✓ passed task builds (from the last run)" in flat
    assert "Step 4 Grader ✓ passed" in flat
    # nothing changed now, but a different selection: steps 1-3 come from the last run
    _, out, err = run(capsys, "check", str(task), "--runs", "1", "--from", "4")
    assert "resuming at step 4" in _flat(err)
    assert "Step 3 Leaks ✓ passed 2 checks clean (from the last run)" in _flat(out)


def test_full_run_shows_per_check_progress_with_timings(capsys):
    _, _, err = run(capsys, "check", WEAK, "--runs", "1", "--fresh")
    assert "Run the reference solution and a do-nothing submission" in err
    assert re.search(r"✓ Remove each part of the fix; the grader must fail\s+\d", err)


def test_quick_run_says_why_checks_were_skipped(capsys):
    code, out, _ = run(capsys, "check", WEAK, "--quick")
    assert "quick run" in out
    assert "partly checked" in out and "the rest needs a full run" in out
    assert "NOT FULLY CHECKED" in out
    assert not FORBIDDEN.search(out)
    assert code in (0, 1)


def test_ascii_fallback_and_no_colour(capsys, monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    _, out, _ = run(capsys, "check", WEAK, "--quick", "--ascii", "--no-color")
    assert "[skip]" in out and "\x1b[" not in out and "✓" not in out


def test_unchanged_task_comes_from_cache(capsys):
    run(capsys, "check", CLEAN, "--runs", "1")
    _, out, _ = run(capsys, "check", CLEAN, "--runs", "1")
    assert "cached" in out


def test_only_runs_named_steps(capsys):
    _, out, _ = run(capsys, "check", WEAK, "--quick", "--only", "leaks")
    flat = _flat(out)
    assert "Step 3 Leaks" in flat and "Step 5 Cheating - not run not part of this run" in flat


def test_unknown_step_says_what_exists(capsys):
    code, _, err = run(capsys, "check", WEAK, "--only", "nope")
    assert code == 2 and "build, baseline," in err and "Next:" in err


# -- the finding format, explain, rules ---------------------------------------------------

def test_show_has_the_four_parts(capsys):
    run(capsys, "check", WEAK, "--runs", "1")
    code, out, _ = run(capsys, "show", "RG101", WEAK)
    assert code == 0
    assert "RG101" in out and "PROVEN · high" in out
    for part in ("What:", "Why:", "Proof:", "Fix:"):
        assert part in out
    assert f"rlgate repro RG101 {WEAK}" in out


def test_show_before_check_says_next_step(capsys):
    code, _, err = run(capsys, "show", "RG101", WEAK)
    assert code == 2 and f"Next: rlgate check {WEAK}" in err


def test_explain_every_rule_has_bad_and_good_examples(capsys):
    from rlgate.core import rules

    for rid in rules.RULES:
        assert main(["explain", rid]) == 0
        out = capsys.readouterr().out
        assert "Bad" in out and "Good" in out and "Fix:" in out, rid


def test_mistyped_rule_and_command_get_suggestions(capsys):
    code, _, err = run(capsys, "explain", "RG2O1")
    assert code == 2 and "Did you mean RG201?" in err
    code, _, err = run(capsys, "chek", WEAK)
    assert code == 2 and "Did you mean 'check'" in err


# -- waivers ------------------------------------------------------------------------------

def test_waive_records_and_shows_accepted_with_reason(demo, capsys):
    Path(demo, "environment/app/limiter.py.orig").unlink()     # so the run reaches step 6
    run(capsys, "check", demo, "--quick")
    code, out, _ = run(capsys, "waive", "RG403", demo, "--reason", "message is the spec",
                       "--author", "Tester")
    assert code == 0 and "accepted, with reason" in out
    toml = Path(demo, "rlgate.toml").read_text()
    assert 'rule = "RG403"' in toml and 'author = "Tester"' in toml
    _, out, _ = run(capsys, "check", demo, "--quick")
    assert 'RG403 accepted, with reason: "message is the spec"' in out
    _, out, _ = run(capsys, "waivers", demo)
    assert "RG403" in out and "accepted" in out


def test_waiver_is_reraised_when_its_file_changes(demo, capsys):
    Path(demo, "environment/app/limiter.py.orig").unlink()
    run(capsys, "check", demo, "--quick")
    run(capsys, "waive", "RG403", demo, "--reason", "ok", "--author", "T")
    with open(Path(demo, "tests/test_outputs.py"), "a") as fh:
        fh.write("\n# changed\n")
    _, out, _ = run(capsys, "check", demo, "--quick")
    assert "The waiver for RG403 was re-raised" in out
    _, out, _ = run(capsys, "waivers", demo)
    assert "re-raised" in out


def test_proven_high_severity_cannot_be_waived(demo, capsys):
    run(capsys, "check", demo, "--quick")
    code, _, err = run(capsys, "waive", "RG303", demo, "--reason", "x")
    assert code == 2 and "can only be fixed" in err


# -- the fix loop -------------------------------------------------------------------------

def test_failed_reruns_only_what_failed(demo, capsys):
    run(capsys, "check", demo, "--quick")
    Path(demo, "environment/app/limiter.py.orig").unlink()
    _, out, _ = run(capsys, "check", demo, "--quick", "--failed")
    assert "Patch leftover" not in out and "Step 3 Leaks" in _flat(out)
    _, out, _ = run(capsys, "show", "RG601", demo)        # untouched findings are kept
    assert "RG601" in out


# -- formats + exit codes -----------------------------------------------------------------

def test_json_has_a_stable_schema(capsys):
    _, out, _ = run(capsys, "check", WEAK, "--runs", "1", "--format", "json")
    d = json.loads(out)
    assert d["schema"] == "rlgate.check/1" and d["verdict"] == "needs_fixes"
    assert d["exit_code"] == 1 and 0 <= d["quality_score"] <= 1
    assert {"proven", "to_review", "warnings", "accepted"} == set(d["counts"])
    f = d["findings"][0]
    assert {"rule", "title", "what", "why", "proof", "fix", "kind", "severity"} <= set(f)


def test_markdown_is_the_qa_card(capsys):
    _, out, _ = run(capsys, "check", WEAK, "--runs", "1", "--format", "markdown")
    assert out.startswith("# QA card: weak_grader")
    for field in ("Verdict", "Build gates", "Grader probes", "Static review", "Dedup",
                  "Provenance", "Check trust"):
        assert f"| {field} |" in out


def test_sarif_for_github(capsys):
    _, out, _ = run(capsys, "check", WEAK, "--runs", "1", "--format", "sarif")
    d = json.loads(out)
    assert d["version"] == "2.1.0"
    res = d["runs"][0]["results"]
    assert res and res[0]["ruleId"].startswith("RG") and res[0]["level"] in (
        "error", "warning", "note")


def test_bank_view_rows_and_common_problems(capsys):
    code, out, _ = run(capsys, "check", BANK, "--runs", "1")
    assert code == 1
    for col in ("Task", "Verdict", "Proven", "To review", "Quality score"):
        assert col in out
    assert "weak_grader" in out and "clean" in out and "Most common problems" in out
    assert "1 ready" in out


def test_bank_html_report_has_filters_and_no_server(tmp_path, capsys):
    out_file = tmp_path / "r.html"
    code, _, _ = run(capsys, "check", BANK, "--quick", "-o", str(out_file))
    assert code in (0, 1)
    html = out_file.read_text(encoding="utf-8")
    assert "<!doctype html>" in html
    for f in ('id="fv"', 'id="fr"', 'id="ft"'):              # verdict, rule, task type
        assert f in html
    assert "<script src" not in html and "<link" not in html
    blob = html[html.index("const D = ") + len("const D = "):].replace("\\u003c", "<")
    data, _ = json.JSONDecoder().raw_decode(blob)
    assert data["schema"] == "rlgate.bank/1" and data["tasks"] == 2


def test_report_rerenders_the_last_result_without_rerunning(capsys):
    run(capsys, "check", WEAK, "--runs", "1")
    code, out, _ = run(capsys, "report", "--format", "json")
    assert code == 1 and json.loads(out)["task"] == "weak_grader"


def test_missing_target_exits_2_with_next_step(capsys):
    code, _, err = run(capsys, "check", "./nope")
    assert code == 2 and "doesn't exist" in err and "Next:" in err


# -- help + housekeeping -------------------------------------------------------------------

def test_no_arguments_prints_start_here(capsys):
    code, out, _ = run(capsys)
    assert code == 0 and "rlgate demo" in out and "rlgate check ./task" in out


def test_version_includes_python_and_docker(capsys):
    code, out, _ = run(capsys, "--version")
    assert code == 0 and "Python" in out and "Docker" in out


def test_command_help_has_examples(capsys):
    for cmd in ("check", "explain", "repro", "waive", "demo", "doctor", "init", "report",
                "clean", "selftest"):
        code, out, _ = run(capsys, cmd, "--help")
        assert code == 0 and "Examples" in out, cmd


def test_global_flags_work_after_the_command(capsys):
    code, out, err = run(capsys, "check", WEAK, "--quick", "--quiet", "--no-color")
    assert code in (0, 1) and "NOT FULLY CHECKED" in out and err == ""


def test_init_and_cache_clear(tmp_path, capsys):
    code, out, _ = run(capsys, "init", str(tmp_path))
    assert code == 0 and (tmp_path / "rlgate.toml").is_file()
    text = (tmp_path / "rlgate.toml").read_text()
    assert "[checks]" in text and "[thresholds]" in text and "[[waivers]]" in text
    import tomllib
    tomllib.loads(text)
    code, _, err = run(capsys, "init", str(tmp_path))
    assert code == 2 and "already exists" in err
    code, out, _ = run(capsys, "cache", "clear")
    assert code == 0 and "cleared" in out


def test_settings_checks_skip_leaves_a_group_out(tmp_path, capsys):
    task = tmp_path / "t"
    shutil.copytree(WEAK, task)
    (task / "rlgate.toml").write_text('[checks]\nskip = ["leaked"]\n')
    _, out, _ = run(capsys, "check", str(task), "--quick")
    assert "skipped in rlgate.toml [checks]" in out


def test_demo_shows_real_findings(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code, out, _ = run(capsys, "demo")
    assert code == 1 and "rlgate check ./rate-limiter" in out
    flat = _flat(out)
    assert "Step 1 Build ✓ partly checked" in flat and "4 warnings" in flat
    assert "Step 3 Leaks ✗ failed patch leftover: app/limiter.py.orig" in flat
    assert "STOPPED AT STEP 3 of 6 · NEEDS FIXES" in flat
    assert "RG303 Patch leftover files contain the fix" in flat
    assert (tmp_path / "rate-limiter" / "task.toml").is_file()


# -- speed: --parallel, --workers, --depth -------------------------------------------------

def test_parallel_runs_steps_3_to_6_together_and_reports_them_all(capsys):
    code, out, err = run(capsys, "check", WEAK, "--runs", "1", "--fresh", "--parallel")
    flat, ferr = _flat(out), _flat(err)
    assert "Running steps 3, 4, 5, 6 at the same time" in ferr
    for n in (3, 4, 5, 6):                          # one line per step as it finishes
        assert re.search(rf"Step {n} \w+ [✓✗] (finished|problems found) \d+ of \d+ checks",
                         ferr)
    assert "not run" not in flat                   # nothing stopped: every step reported
    assert re.search(r"ALL 6 STEPS RUN · STEPS? [\d, ]+ FAILED", flat)
    assert not FORBIDDEN.search(out) and code in (1, 2)


def test_bad_depth_and_workers_say_what_to_do(capsys):
    code, _, err = run(capsys, "check", WEAK, "--depth", "deep")
    assert code == 2 and "--depth takes fast or full" in _flat(err)
    code, _, err = run(capsys, "check", WEAK, "--workers", "0")
    assert code == 2 and "--workers takes a number" in _flat(err)


# -- the command surface: 8 commands, grouped check flags, old names as hidden aliases ----

def _cli():
    import typer

    from rlgate.cli import app
    return typer.main.get_command(app)


def test_main_help_lists_the_eight_commands_only():
    group = _cli()
    shown = {n for n, c in group.commands.items() if not c.hidden}
    assert shown == {"check", "explain", "repro", "waive", "report", "doctor", "init", "demo"}
    hidden = {n for n, c in group.commands.items() if c.hidden}
    assert {"show", "rules", "waivers", "cache", "planted", "clean", "selftest"} <= hidden
    root = {o for p in group.params if not getattr(p, "hidden", False) for o in p.opts}
    assert "--jobs" not in root and "--output" not in root       # now on check / report


def test_check_help_groups_its_flags():
    check = _cli().commands["check"]
    panel = {o: getattr(p, "rich_help_panel", None) for p in check.params for o in p.opts
             if not getattr(p, "hidden", False)}
    want = {"--only": "Scope", "--from": "Scope", "--failed": "Scope", "--fresh": "Scope",
            "--limit": "Scope", "--quick": "Depth", "--deep-review": "Depth",
            "--model": "Depth", "--yes": "Depth", "--exhaustive": "Depth", "--runs": "Depth",
            "--jobs": "Execution", "--backend": "Execution", "--backend-opt": "Execution",
            "--fail-fast": "Execution", "--format": "Output", "--output": "Output",
            "--config": "Config", "--layout": "Config"}
    for flag, group in want.items():
        assert panel.get(flag) == group, (flag, panel.get(flag))
    for old in ("--deep", "--keep-going", "--parallel", "--workers", "--depth", "--ek",
                "--preset", "--mapping", "--adapter", "--step"):
        assert old not in panel, old                                 # hidden aliases


def test_explain_lists_rules_explains_one_and_shows_a_finding(capsys):
    code, out, _ = run(capsys, "explain")
    assert code == 0 and "RG101" in out and "RG704" in out
    code, out, _ = run(capsys, "explain", "RG101")
    assert code == 0 and "RG101" in out
    run(capsys, "check", WEAK, "--runs", "1")
    code, out, _ = run(capsys, "explain", "RG101", WEAK)
    flat = _flat(out)
    assert code == 0 and "What:" in flat and "Proof:" in flat and "Fix:" in flat
    code, _, err = run(capsys, "show", "RG101", WEAK)             # old name
    assert code == 0 and "rlgate explain <rule> <task>" in _flat(err)
    code, _, err = run(capsys, "rules")
    assert code == 0 and "rlgate explain" in _flat(err)


def test_waive_list_and_remove(tmp_path, capsys):
    task = tmp_path / "weak_grader"
    shutil.copytree(Path(__file__).resolve().parents[2] / WEAK, task)
    (task / "rlgate.toml").write_text("[checks]\nskip = []\n\n[[waivers]]\nrule = \"RG404\"\n"
                                      "reason = \"pinned on purpose\"\nauthor = \"a\"\n"
                                      "date = \"2026-11-11\"\n")
    code, out, _ = run(capsys, "waive", "--list", str(task))
    assert code == 0 and "RG404" in out and "pinned on purpose" in out
    code, out, _ = run(capsys, "waive", "--remove", "RG404", str(task))
    assert code == 0 and "removed" in out
    text = (task / "rlgate.toml").read_text()
    assert "RG404" not in text and "[checks]" in text            # the rest is kept
    code, _, err = run(capsys, "waive", "--remove", "RG404", str(task))
    assert code == 2 and "no waiver" in _flat(err)
    code, _, err = run(capsys, "waivers", str(task))               # old name
    assert code == 0 and "waive --list" in _flat(err)


def test_old_flags_still_work_with_a_notice(capsys):
    code, _, err = run(capsys, "check", WEAK, "--quick", "--ek", "cpus=2")
    assert code in (0, 1) and "--backend-opt" in _flat(err)
    code, _, err = run(capsys, "check", WEAK, "--quick", "--depth", "full")
    assert code in (0, 1) and "--exhaustive" in _flat(err)
    code, _, err = run(capsys, "check", WEAK, "--quick", "--deep", "--model", "x")
    assert "--deep-review" in _flat(err)


def test_layout_takes_a_preset_a_mapping_file_or_an_adapter(tmp_path):
    from rlgate.cli import _layout
    assert _layout("mimo") == ("mimo", None, None)
    m = tmp_path / "bank.rlgate.yaml"
    m.write_text("tasks: {}\n")
    assert _layout(str(m)) == (None, str(m), None)
    assert _layout("mycorp-adapter") == (None, None, "mycorp-adapter")


def test_jobs_and_output_are_check_options(tmp_path, capsys):
    out = tmp_path / "bank.json"
    code, _, _ = run(capsys, "check", BANK, "--quick", "-j", "2", "-o", str(out))
    assert code in (0, 1, 2) and json.loads(out.read_text())["tasks"] >= 2
