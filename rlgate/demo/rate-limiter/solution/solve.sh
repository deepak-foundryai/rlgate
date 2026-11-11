#!/bin/bash
set -euo pipefail
cd /app
patch -p1 < /solution/patch.diff
