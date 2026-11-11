#!/usr/bin/env bash
# Run a Python script with the interpreter of the installed vra tool (it has PyYAML), e.g.
#   bash vra-py.sh ci_graph.py requires-approval --ledger "${VRA_HOME}/ledger" --env prod
# Relative script names resolve next to this file.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
script="${1:?script required}"
shift
[ -f "${script}" ] || script="${here}/${script}"
export PATH="${HOME}/.local/bin:${PATH}"
py=""
if vra_bin="$(command -v vra 2>/dev/null)"; then
  candidate="$(dirname "$(readlink -f "${vra_bin}")")/python"
  [ -x "${candidate}" ] && py="${candidate}"
fi
exec "${py:-python3}" "${script}" "$@"
