# rlgate in CI

## GitHub Action

```yaml
# .github/workflows/rlgate.yml
name: rlgate
on: [pull_request]
permissions:
  contents: read
  security-events: write     # to show findings as pull-request annotations
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: deepak-foundryai/rlgate@main
        with:
          path: ./tasks
          args: --quick          # drop this for a full run (needs Docker on the runner)
```

The job fails with rlgate's exit code: 0 ready, 1 needs fixes, 2 broken or could not run.
Findings are uploaded as SARIF, so they appear as annotations on the pull request, and the
QA card is written to the job summary.

## pre-commit

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/deepak-foundryai/rlgate
    rev: main
    hooks:
      - id: rlgate-quick
        args: [./tasks]
```

## Badge

```bash
rlgate check ./tasks --quick
rlgate report -o rlgate-badge.svg      # rlgate | ready / needs fixes / broken
```

Commit the SVG and reference it from the README: `![rlgate](rlgate-badge.svg)`.
