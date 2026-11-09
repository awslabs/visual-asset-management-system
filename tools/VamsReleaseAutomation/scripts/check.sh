#!/usr/bin/env bash
# Package self-check. Runs offline (no AWS calls, no network):
#   1. engine unit tests (pytest, engine/tests)
#   2. adapter guard-hook tests (adapters/shared/test_vra_guard.py)
#   3. self-check script tests (scripts/tests), IAM template and CI lane tests (iam/tests, cicd/tests)
#   4. YAML / JSON parse of every file
#   5. `vra ledger validate` on ledger-template/ and its GovCloud variant
#   6. engine copies identical to reviewed files (iam/actions, path-policy)
#   7. every `vra ...` command in the Markdown matches the real CLI
#   8. the agent policy is one set: engine human-only actions, skill list, MCP tools, guard, Kiro agents,
#      Claude Code settings; every workflow step the guard blocks is handed to a person
#   9. the Kiro agent files validate and are in the universal format (only when kiro-cli is installed)
#  10. FILES.md up to date
#  11. relative Markdown links and GitHub source links resolve
#  12. customer-neutrality + inclusive-language scan (placeholder accounts only, no `arn:aws:` in
#      code and deployable templates)
#
# Steps 4, 7 and 10-12 scan this directory plus the package's own files elsewhere in the repository
# (its documentation pages, Kiro steering document, Claude Code command and CI workflow), named
# below by explicit path. They never scan the rest of the repository.
#
# Usage: scripts/check.sh            (uses `python3`, or $PYTHON)
#        PYTHON=/path/to/venv/bin/python scripts/check.sh
# Needs the package installed with its dev extras: pip install -e '.[dev]'
# Optional: VAMS_REPO=<VAMS clone> verifies links into VAMS pages outside these paths (read-only
# `git ls-tree`); VRA_NEUTRAL_TERMS_FILE=<file> adds private terms to the neutrality scan.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "${here}/.." && pwd)"
repo="$(cd "${root}/../.." && pwd)"
py="${PYTHON:-python3}"
fail=0

docs="${repo}/documentation/docusaurus-site/docs/deployment"
with=(
  --with "${docs}/release-automation"
  --with "${docs}/productionizing"
  --with "${repo}/.kiro/steering/RELEASE_AUTOMATION_WORKFLOW.md"
  --with "${repo}/.claude/commands/release-automation.md"
)
# Standalone workflow; once the job lives in .github/workflows/ci.yml this file does not exist.
if [ -f "${repo}/.github/workflows/release-automation.yml" ]; then
  with+=(--with "${repo}/.github/workflows/release-automation.yml")
fi

step() {
  local name="$1"
  shift
  echo "==> ${name}"
  if "$@"; then
    echo "    ok"
  else
    echo "    FAILED: ${name}"
    fail=1
  fi
}

cd "${root}" || exit 1
# keep tests hermetic: never reach a real AWS endpoint
export AWS_ENDPOINT_URL="http://127.0.0.1:9"
export AWS_ACCESS_KEY_ID="testing" AWS_SECRET_ACCESS_KEY="testing" AWS_SESSION_TOKEN="testing"
export AWS_DEFAULT_REGION="us-east-1"

step "engine tests" "${py}" -m pytest -p no:cacheprovider engine/tests
step "guard tests" "${py}" -m pytest -p no:cacheprovider adapters/shared

# The guard's floor is Python 3.9 (the hooks run the host's python3; Amazon Linux 2023 ships 3.9): probe it
# with VRA_GUARD_OLDEST_PYTHON, else /usr/bin/python3 when that is older than the dev interpreter.
oldest_guard() {
  local old="${VRA_GUARD_OLDEST_PYTHON:-/usr/bin/python3}" rc
  if [ ! -x "${old}" ]; then
    echo "    skipped (no ${old})"
    return 0
  fi
  echo "    $("${old}" --version 2>&1)"
  printf '%s' '{"tool_input":{"command":"env -u VRA_AGENT aws s3 ls"}}' | "${old}" adapters/shared/vra-guard.py \
    > /dev/null 2>&1
  rc=$?
  [ "${rc}" -eq 2 ] || { echo "    the guard did not block on ${old} (exit ${rc})"; return 1; }
  printf '%s' '{"tool_input":{"command":"vra status"}}' | "${old}" adapters/shared/vra-guard.py \
    || { echo "    the guard blocked vra status on ${old}"; return 1; }
}
step "guard on the oldest python3" oldest_guard
step "script tests" "${py}" -m pytest -p no:cacheprovider scripts/tests
step "iam and ci tests" "${py}" -m pytest -p no:cacheprovider iam/tests cicd/tests
step "parse yaml/json" "${py}" scripts/check_files.py "${root}" parse "${with[@]}"
step "ledger-template validate" "${py}" scripts/check_files.py "${root}" ledger
step "engine copies identical" "${py}" scripts/check_files.py "${root}" identical
step "doc commands match CLI" "${py}" scripts/check_doc_commands.py "${root}" "${with[@]}"
step "agent policy consistent" "${py}" scripts/check_agent_policy.py "${root}"

# Kiro agent files load in both agent engines (only when kiro-cli is installed)
kiro_agents() {
  local f
  for f in adapters/kiro/.kiro/agents/*.json; do
    kiro-cli agent validate --path "${f}" > /dev/null 2>&1 || { echo "    invalid: ${f}"; return 1; }
  done
  kiro-cli agent upgrade --dry-run adapters/kiro/.kiro/agents 2>&1 | grep -q '^0 would upgrade' \
    || { echo "    not in the universal format (run kiro-cli agent upgrade)"; return 1; }
}
if command -v kiro-cli > /dev/null 2>&1; then
  step "kiro agents load" kiro_agents
else
  echo "==> kiro agents load"
  echo "    skipped (kiro-cli not installed)"
fi
step "FILES.md up to date" "${py}" scripts/gen_index.py "${root}" --check
step "markdown links" "${py}" scripts/check_files.py "${root}" links "${with[@]}"
step "neutrality scan" "${py}" scripts/check_files.py "${root}" neutral "${with[@]}"

if [ "${fail}" -ne 0 ]; then
  echo "check: FAILED"
  exit 1
fi
echo "check: all green"
