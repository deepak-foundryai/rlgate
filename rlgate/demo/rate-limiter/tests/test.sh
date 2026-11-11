#!/bin/bash
# Runs the held-out tests from /app (the agent's workspace) and writes the reward.
mkdir -p /logs/verifier
cd /app
if python -m pytest -q /tests/test_outputs.py; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
