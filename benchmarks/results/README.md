# rlgate results on the labelled benchmarks

Each folder is one scored run of rlgate on a tier of [the labelled tasks](../README.md),
published by `benchmarks/score.py --publish`. Local paths are removed: `<benchmarks>` is the
folder `fetch.py` wrote, `<runs>` the `--jobs-dir`.

    <rlgate version>/<tier>-<no-llm | deep-review>/
      SCORE.md                    recall, controls, and every task with its outcome
      score.json                  the same, machine-readable
      tasks/<entry>/report.md     rlgate's report for that task
      tasks/<entry>/result.json   its findings, with the proof of each

| Run | rlgate | Recall | Controls with a false positive | Could not run |
|---|---|---|---|---|
| (none yet) | | | | |
