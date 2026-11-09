#!/usr/bin/env bash
# Install one agent adapter into the operator workspace (default: the package root, which every adapter
# expects as the agent's working directory). Copies files only; nothing outside the workspace changes.
# Existing files are kept unless --overwrite is given.
#
# Usage: adapters/install.sh claude|codex|kiro|cursor [--dest <workspace>] [--overwrite]
set -euo pipefail

usage() { sed -n '2,7p' "$0"; }
[ $# -ge 1 ] || { usage; exit 2; }
agent="$1"; shift
dest=""
overwrite=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) dest="${2:?}"; shift 2 ;;
    --overwrite) overwrite=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "install: unknown argument $1" >&2; exit 2 ;;
  esac
done

pkg="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dest="$(cd "${dest:-${pkg}}" && pwd)"

put() { # put <source> <target>
  local src="$1" dst="$2"
  mkdir -p "$(dirname "${dst}")"
  if [ -e "${dst}" ] && [ "${overwrite}" -eq 0 ]; then
    echo "kept     ${dst} (exists; --overwrite replaces it)"
  else
    cp "${src}" "${dst}"
    echo "wrote    ${dst}"
  fi
}

case "${agent}" in
  claude)
    put "${pkg}/adapters/claude/CLAUDE.md.example" "${dest}/.claude/CLAUDE.md"
    put "${pkg}/adapters/claude/settings.json.example" "${dest}/.claude/settings.json"
    put "${pkg}/adapters/claude/mcp.json.example" "${dest}/.mcp.json"
    for f in "${pkg}"/adapters/claude/.claude/agents/*.md "${pkg}"/adapters/claude/.claude/commands/*.md; do
      put "${f}" "${dest}/.claude/${f#"${pkg}/adapters/claude/.claude/"}"
    done
    ;;
  codex)
    put "${pkg}/adapters/codex/AGENTS.md" "${dest}/AGENTS.md"
    put "${pkg}/adapters/codex/config.toml.example" "${dest}/.codex/config.toml"
    put "${pkg}/adapters/codex/hooks.json" "${dest}/.codex/hooks.json"
    put "${pkg}/adapters/codex/rules/vra.rules" "${dest}/.codex/rules/vra.rules"
    mkdir -p "${dest}/.codex/skills"
    if [ ! -e "${dest}/.codex/skills/vams-release-automation" ]; then
      ln -s "${pkg}/skill" "${dest}/.codex/skills/vams-release-automation"
      echo "linked   ${dest}/.codex/skills/vams-release-automation -> ${pkg}/skill"
    fi
    ;;
  kiro)
    for f in "${pkg}"/adapters/kiro/.kiro/agents/*.json "${pkg}"/adapters/kiro/.kiro/steering/*.md; do
      put "${f}" "${dest}/.kiro/${f#"${pkg}/adapters/kiro/.kiro/"}"
    done
    ;;
  cursor)
    for f in "${pkg}"/adapters/cursor/.cursor/{mcp.json,hooks.json,cli.json} \
      "${pkg}"/adapters/cursor/.cursor/rules/*.mdc "${pkg}"/adapters/cursor/.cursor/skills/*/SKILL.md; do
      put "${f}" "${dest}/.cursor/${f#"${pkg}/adapters/cursor/.cursor/"}"
    done
    mkdir -p "${dest}/.cursor/skills"
    if [ ! -e "${dest}/.cursor/skills/vams-release-automation" ]; then
      ln -s "${pkg}/skill" "${dest}/.cursor/skills/vams-release-automation"
      echo "linked   ${dest}/.cursor/skills/vams-release-automation -> ${pkg}/skill"
    fi
    # Cursor runs hooks from the app's environment, which often has no VRA_PACKAGE: name the package by its
    # absolute path, so a workspace opened elsewhere (the VAMS root) still finds the guard
    if cmp -s "${pkg}/adapters/cursor/.cursor/hooks.json" "${dest}/.cursor/hooks.json"; then   # written now
      python3 - "${dest}/.cursor/hooks.json" "${pkg}" <<'PY'
import sys
p, pkg = sys.argv[1], sys.argv[2]
text = open(p, encoding="utf-8").read()
q = pkg.replace("\\", "\\\\").replace('"', '\\"')
open(p, "w", encoding="utf-8").write(text.replace("${VRA_PACKAGE:-.}", q))
PY
      echo "pinned   ${dest}/.cursor/hooks.json -> ${pkg}/adapters/shared/vra-guard.py"
    fi
    ;;
  *) usage; exit 2 ;;
esac

if [ "${dest}" != "${pkg}" ]; then
  echo "note     the adapter reads skill/ and adapters/shared/ relative to the workspace or \$VRA_PACKAGE;"
  echo "         export VRA_PACKAGE=${pkg}"
fi
echo "next     trust the workspace in ${agent} and run the post-install check in adapters/${agent}/README.md"
