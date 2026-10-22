"""The audit-bank CLI renders an explained report (and summary/json) over a live bank."""

from __future__ import annotations

import json

from rlgate.cli import main


def test_report_format_is_explanatory(capsys):
    rc = main(["audit-bank", "examples/taskbank", "-k", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Verdicts:" in out and "CONFIRMED = reproduced/proven" in out
    assert "weak_grader" in out and "unguarded fix hunk" in out
    assert "why:" in out and "fix:" in out            # each finding is explained
    assert "Summary" in out


def test_summary_format_is_tabular(capsys):
    main(["audit-bank", "examples/taskbank", "-k", "1", "--format", "summary"])
    out = capsys.readouterr().out
    assert "top finding" in out and "weak_grader" in out


def test_json_format_is_machine_readable(capsys):
    main(["audit-bank", "examples/taskbank", "-k", "1", "--format", "json"])
    data = json.loads(capsys.readouterr().out)
    by_id = {d["task"]: d for d in data}
    assert by_id["clean"]["findings"] == []
    assert any(f["verdict"] == "confirmed" for f in by_id["weak_grader"]["findings"])
