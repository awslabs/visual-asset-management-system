#!/usr/bin/env bash
# One cron entry point for every agent system. Runs one `vra watch` cycle with no agent, then calls the
# chosen agent only when its exit code needs judgment, with the same branching for every agent:
#
#   0          done (watch wrote its report)
#   20, 30, 1  agent triage: conflicts the path policy allows, test-failure classification, report text
#   10, 40     notify only (IT request, rollback failure); no agent
#
# The agent gets the vra MCP server with writes on and the approval-gated tier OFF, so nothing in this path
# can reach an approval-gated environment (vra watch refuses to as well).
#
# Usage:  vra-cron.sh --agent claude|codex|kiro|cursor|none
# Cron:   17 6 * * *  vra-bot  VRA_PACKAGE=/opt/vra/VamsReleaseAutomation /opt/vra/VamsReleaseAutomation/adapters/shared/vra-cron.sh --agent kiro
#
# Environment: VRA_PACKAGE (package root; default: two levels above this script), VRA_HOME (shared home;
# default: the one `vra setup` registered for this user), VRA_CRON_OUT (output dir; default
# ~/.cache/vra/cron).
set -u

agent="none"
while [ $# -gt 0 ]; do
  case "$1" in
    --agent) agent="${2:-}"; shift 2 ;;
    --agent=*) agent="${1#--agent=}"; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "vra-cron: unknown argument $1" >&2; exit 2 ;;
  esac
done
case "${agent}" in claude|codex|kiro|cursor|none) ;; *) echo "vra-cron: --agent must be claude, codex, kiro, cursor or none" >&2; exit 2 ;; esac

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VRA_PACKAGE="${VRA_PACKAGE:-$(cd "${here}/../.." && pwd)}"
out="${VRA_CRON_OUT:-${HOME}/.cache/vra/cron}"
mkdir -p "${out}"

# Everything in this script runs as an agent: the engine refuses every human-only action, and with
# VRA_AGENT_SESSION set it never accepts a typed confirmation either (only an approval record).
export VRA_AGENT=1 VRA_AGENT_SESSION="cron-${agent}"
export VRA_MCP_ENABLE_WRITES=true VRA_MCP_ENABLE_GATED=false

# Canary: the guard must refuse a direct AWS call before any agent runs headless. A guard that cannot run
# (no python3, a moved package, a crash) stops the triage instead of leaving the agent unguarded.
canary() {
  local probe='{"tool_input":{"command":"aws sts get-caller-identity"}}'
  local rc
  printf '%s' "${probe}" | python3 "${VRA_PACKAGE}/adapters/shared/vra-guard.py" > /dev/null 2>&1
  rc=$?
  if [ "${rc}" -eq 0 ]; then
    echo "vra-cron: canary failed: the guard allowed a direct aws call; no agent triage" >&2
    return 1
  fi
  if [ "${rc}" -ne 2 ]; then
    echo "vra-cron: canary failed: the guard did not run (exit ${rc}); no agent triage" >&2
    return 1
  fi
  if [ "${agent}" = "cursor" ]; then
    # headless Cursor is allowlist-only (cli.json permissions.allow; no --force): refuse to start with an
    # allowlist that admits every command or lacks the aws / cdk denies
    python3 - "${VRA_PACKAGE}/adapters/cursor/.cursor/cli.json" <<'PY' || return 1
import json, sys
try:
    perms = json.load(open(sys.argv[1]))["permissions"]
except Exception as exc:  # noqa: BLE001
    sys.exit(f"vra-cron: canary failed: {sys.argv[1]} unreadable ({exc}); no agent triage")
allow, deny = perms.get("allow") or [], perms.get("deny") or []
if any(a in ("Shell(*)", "Shell(*:*)", "*") for a in allow):
    sys.exit("vra-cron: canary failed: cli.json allows every shell command; no agent triage")
if not {"Shell(aws)", "Shell(cdk)"} <= set(deny):
    sys.exit("vra-cron: canary failed: cli.json does not deny aws and cdk; no agent triage")
PY
  fi
  return 0
}

vra --non-interactive --json watch > "${out}/last-watch.json" 2> "${out}/last-watch.log"
rc=$?

prompt="Read skill/SKILL.md. vra watch exited ${rc}; its JSON envelope is in ${out}/last-watch.json. \
Follow the skill for exit ${rc} in headless mode: use the vra MCP tools, stop at every [HUMAN] step and \
write the question into the run report, then call the vra report tool for that run."

triage() {
  cd "${VRA_PACKAGE}" || return 1
  case "${agent}" in
    claude)
      claude -p --agent vra-release-operator --permission-mode dontAsk \
        --mcp-config "${VRA_PACKAGE}/adapters/claude/mcp.cron.json" --strict-mcp-config \
        --output-format json "${prompt}" > "${out}/last-triage.json" 2> "${out}/last-triage.log"
      ;;
    codex)
      codex exec --profile vra-cron -c 'mcp_servers.vra.env.VRA_MCP_ENABLE_GATED="false"' \
        "${prompt}" > "${out}/last-triage.txt" 2> "${out}/last-triage.log"
      ;;
    kiro)
      kiro-cli chat --no-interactive --trust-workspace --agent vra-cron-operator \
        "${prompt}" > "${out}/last-triage.txt" 2> "${out}/last-triage.log"
      ;;
    cursor)
      # Cursor CLI (`agent`; older installs: `cursor-agent`). The workspace's .cursor/mcp.json reads
      # VRA_MCP_ENABLE_GATED (false above). No --force: in print mode only what .cursor/cli.json
      # permissions.allow lists runs (an allowlist), because whether the CLI runs project hooks is not proven.
      local bin="${VRA_CURSOR_AGENT:-agent}"
      command -v "${bin}" > /dev/null 2>&1 || bin="cursor-agent"
      "${bin}" -p --trust --approve-mcps --workspace "${VRA_PACKAGE}" --output-format json \
        "${prompt}" > "${out}/last-triage.json" 2> "${out}/last-triage.log"
      ;;
    none) return 0 ;;
  esac
}

case "${rc}" in
  0) ;;
  20|30|1)
    if [ "${agent}" != "none" ] && ! canary; then
      vra --non-interactive --json report --notify > /dev/null 2>> "${out}/last-watch.log"
    else
      triage || echo "vra-cron: ${agent} triage exited $?" >&2
    fi
    ;;
  10|40)
    vra --non-interactive --json report --notify > /dev/null 2>> "${out}/last-watch.log"
    ;;
esac
exit "${rc}"
