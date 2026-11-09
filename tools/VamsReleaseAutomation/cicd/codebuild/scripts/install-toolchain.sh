#!/usr/bin/env bash
# Install the pinned vra toolchain inside a CodeBuild container.
# Used by every cicd/codebuild/buildspec-*.yml. Idempotent.
#
# Pins (override through project environment variables):
#   NPM_VERSION      npm major 11, >= 11.10 (VAMS CI uses 11.19.1)
#   UV_VERSION       uv for Python tool installs
#   AWS_CDK_VERSION  CDK CLI; empty = the version pinned by the fork's infra/package-lock.json (npx)
#   VRA_PACKAGE      pip/uv spec for the vra engine. Default: the root (pyproject.toml) of the
#                    vams-release-automation source (secondary source "Vra"), so the pipeline runs
#                    exactly the reviewed commit. Use a pinned wheel or index version in air-gapped
#                    partitions, e.g. "vams-release-automation==<version>" with PIP_INDEX_URL pointing at a
#                    private mirror.
# Private mirrors (GovCloud / no internet egress): set NPM_CONFIG_REGISTRY, PIP_INDEX_URL,
# UV_INDEX_URL to a CodeArtifact or internal mirror.
set -euo pipefail

NPM_VERSION="${NPM_VERSION:-11.19.1}"
UV_VERSION="${UV_VERSION:-0.12.17}"
VRA_SRC_DIR="${CODEBUILD_SRC_DIR_Vra:-${VRA_SRC_DIR:-${VRA_PKG_DIR:-}}}"
VRA_PACKAGE="${VRA_PACKAGE:-}"

log() { printf '[install-toolchain] %s\n' "$*" >&2; }

node_major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
if [ "${node_major}" != "22" ]; then
  log "expected Node 22, found $(node --version); check runtime-versions in the buildspec"
  exit 1
fi

log "npm ${NPM_VERSION}"
npm install -g "npm@${NPM_VERSION}" --no-audit --no-fund >/dev/null

if [ -n "${AWS_CDK_VERSION:-}" ]; then
  log "aws-cdk ${AWS_CDK_VERSION}"
  npm install -g "aws-cdk@${AWS_CDK_VERSION}" --no-audit --no-fund >/dev/null
fi

log "uv ${UV_VERSION}"
python3 -m pip install --quiet --disable-pip-version-check "uv==${UV_VERSION}"

if [ -z "${VRA_PACKAGE}" ]; then
  if [ -n "${VRA_SRC_DIR}" ] && [ -f "${VRA_SRC_DIR}/pyproject.toml" ]; then
    VRA_PACKAGE="${VRA_SRC_DIR}"
  else
    log "set VRA_PACKAGE or provide the vams-release-automation repo as secondary source 'Vra'"
    exit 1
  fi
fi

log "vra from ${VRA_PACKAGE}"
uv tool install --force --python "$(command -v python3)" "${VRA_PACKAGE}"
export PATH="${HOME}/.local/bin:${PATH}"

if [ "${VRA_REQUIRE_DOCKER:-false}" = "true" ]; then
  docker info >/dev/null 2>&1 || { log "docker not available: enable privileged mode on the CodeBuild project"; exit 1; }
fi

vra --version >&2 || true
node --version >&2
npm --version >&2
python3 --version >&2
uv --version >&2
