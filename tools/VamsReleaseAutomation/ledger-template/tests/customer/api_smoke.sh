#!/usr/bin/env bash
# Example customer hook: minimal API smoke through vamscli, with a JUnit result.
# Inputs from vra: VRA_ENV, VRA_JUNIT_DIR, VAMSCLI_PROFILE (configured by vra from liveTest.*).
set -euo pipefail

junit="${VRA_JUNIT_DIR:?}/customer-api-smoke.xml"
start=$(date +%s)
status=0
output="$(vamscli --profile "${VAMSCLI_PROFILE:?}" database list --json-output 2>&1)" || status=$?
elapsed=$(( $(date +%s) - start ))

python3 - "$junit" "$status" "$elapsed" "$output" <<'PY'
import sys
from xml.sax.saxutils import escape
path, status, elapsed, output = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
failure = "" if status == 0 else f'<failure message="vamscli exit {status}">{escape(output[-2000:])}</failure>'
xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
       f'<testsuite name="customer.api-smoke" tests="1" failures="{0 if status == 0 else 1}">'
       f'<testcase classname="customer.api-smoke" name="database-list" time="{elapsed}">{failure}</testcase>'
       f'</testsuite>\n')
open(path, "w").write(xml)
PY

exit "${status}"
