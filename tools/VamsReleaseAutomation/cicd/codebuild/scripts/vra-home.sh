#!/usr/bin/env bash
# The vra home folder in a CI job: one checkout of the customer's home repository (vra-home.yaml, ledger/,
# state/, artifacts/, checkpoints/), shared with every operator machine and every other CI job.
#
#   vra-home.sh checkout          clone VRA_HOME_REPO at VRA_HOME_REF into VRA_HOME (or reuse a checkout there)
#   vra-home.sh sync [label]      pull before the job reads state; push what the job wrote at the end
#                                 (result in vra-out/<label>.json, default label home-sync)
#   vra-home.sh bundle-out <dir>  copy VRA_HOME/artifacts into <dir>/artifacts (CI artifact hand-over)
#   vra-home.sh bundle-in <dir>   copy <dir>/artifacts back into VRA_HOME/artifacts
#   vra-home.sh adopt <dir>       copy <dir>/{state,checkpoints,artifacts} into VRA_HOME (the publish job of
#                                 github-actions/vra-home-publish.yml; no links, nothing else)
#
# Environment:
#   VRA_HOME          home folder (required)
#   VRA_HOME_REPO     git URL of the home repository (checkout only; https://..., codecommit::<region>://<repo>)
#   VRA_HOME_REF      branch, default main
#   VRA_HOME_TOKEN    HTTPS token for the home repository. With it the job can push the state it writes;
#                     without it the checkout is read-only, which is right when vra.yaml uses
#                     stateStore.type s3 (state, locks and history then live in S3 and nothing is pushed).
#                     Falls back to VRA_GIT_TOKEN for reading. Never written to disk.
#
# `sync` runs `vra home sync` (the engine pulls with --ff-only, re-checks locks, and pushes per vra-home.yaml
# sync settings; a rejected push is lock contention, exit 20). Before it pushes, this script refuses (exit 20)
# when the job changed anything outside state/, artifacts/ or checkpoints/: a CI job never edits the ledger
# or vra-home.yaml, and the build job runs fork code that must not be able to plant an approval.
set -euo pipefail

cmd="${1:?usage: vra-home.sh checkout|sync|bundle-out <dir>|bundle-in <dir>|adopt <dir>}"
: "${VRA_HOME:?VRA_HOME not set}"
ref="${VRA_HOME_REF:-main}"
log() { printf '[vra-home] %s\n' "$*" >&2; }

credentials() {
  local token="${VRA_HOME_TOKEN:-${VRA_GIT_TOKEN:-}}"
  if [ -n "${token}" ]; then
    # The helper reads the token from the environment at call time; nothing is stored in .git/config.
    git -C "${VRA_HOME}" config credential.helper \
      '!f() { echo "username=x-access-token"; echo "password=${VRA_HOME_TOKEN:-${VRA_GIT_TOKEN}}"; }; f'
  fi
  git -C "${VRA_HOME}" config user.name "${VRA_HOME_GIT_NAME:-vra-ci}"
  git -C "${VRA_HOME}" config user.email "${VRA_HOME_GIT_EMAIL:-vra-ci@users.noreply.invalid}"
}

guard_paths() {
  # Paths this job changed: uncommitted, plus commits not on the remote branch yet.
  local changed
  changed="$( { git -C "${VRA_HOME}" status --porcelain --untracked-files=all | cut -c4-;
                git -C "${VRA_HOME}" diff --name-only "origin/${ref}...HEAD" 2>/dev/null || true; } | sort -u)"
  local bad
  bad="$(printf '%s\n' "${changed}" | grep -vE '^(state|artifacts|checkpoints)/' | grep -v '^$' || true)"
  if [ -n "${bad}" ]; then
    log "refusing to publish: this job changed files outside state/, artifacts/, checkpoints/:"
    while IFS= read -r f; do printf '  %s\n' "${f}" >&2; done <<<"${bad}"
    exit 20
  fi
}

case "${cmd}" in
  checkout)
    if [ -d "${VRA_HOME}/.git" ]; then
      log "using the existing checkout in ${VRA_HOME}"
    else
      : "${VRA_HOME_REPO:?VRA_HOME_REPO not set (git URL of the home repository)}"
      if [[ "${VRA_HOME_REPO}" == codecommit::* ]] && ! command -v git-remote-codecommit >/dev/null; then
        python3 -m pip install --quiet --disable-pip-version-check git-remote-codecommit==1.17
      fi
      mkdir -p "$(dirname "${VRA_HOME}")"
      # A deploy / test job's primary input is the build stage's artifact, which carries vra-home/state/iam/:
      # git cannot clone into that non-empty folder. Move it aside, clone, then add back only the files the
      # home does not have (the home's committed copy wins). (E2E-050 B-050-16)
      carried=""
      if [ -d "${VRA_HOME}" ] && [ -n "$(ls -A "${VRA_HOME}")" ]; then
        carried="${VRA_HOME}.carried"
        mv "${VRA_HOME}" "${carried}"
      fi
      token="${VRA_HOME_TOKEN:-${VRA_GIT_TOKEN:-}}"
      if [ -n "${token}" ]; then
        git -c credential.helper='!f() { echo "username=x-access-token"; echo "password=${VRA_HOME_TOKEN:-${VRA_GIT_TOKEN}}"; }; f' \
          clone --quiet --branch "${ref}" "${VRA_HOME_REPO}" "${VRA_HOME}"
      else
        git clone --quiet --branch "${ref}" "${VRA_HOME_REPO}" "${VRA_HOME}"
      fi
      if [ -n "${carried}" ]; then
        if [ -d "${carried}/state" ]; then
          # engine paths only, no links, never overwriting the clone
          (cd "${carried}" && find state -type f -print0) | while IFS= read -r -d '' f; do
            [ -e "${VRA_HOME}/${f}" ] && continue
            mkdir -p "$(dirname "${VRA_HOME}/${f}")"
            cp -p -- "${carried}/${f}" "${VRA_HOME}/${f}"
          done
        fi
        log "kept $(cd "${carried}" && find state -type f 2>/dev/null | wc -l | tr -d ' ') carried file(s) from the input artifact in ${carried}"
      fi
    fi
    # A detached checkout (CI default) cannot fast-forward or push: put it on the branch tip.
    if ! git -C "${VRA_HOME}" symbolic-ref -q HEAD >/dev/null; then
      git -C "${VRA_HOME}" fetch --quiet origin "${ref}"
      git -C "${VRA_HOME}" checkout --quiet -B "${ref}" "origin/${ref}"
    fi
    credentials
    test -f "${VRA_HOME}/vra-home.yaml" || { log "${VRA_HOME} has no vra-home.yaml: run 'vra setup' first"; exit 2; }
    test -d "${VRA_HOME}/ledger" || { log "${VRA_HOME} has no ledger/"; exit 2; }
    log "home ${VRA_HOME} at $(git -C "${VRA_HOME}" rev-parse --short HEAD) (${ref})"
    ;;
  sync)
    label="${2:-home-sync}"
    guard_paths
    if vra home --help >/dev/null 2>&1; then
      bash "$(dirname "${BASH_SOURCE[0]}")/run-vra.sh" "${label}" home sync
    else
      # engine without `vra home sync`: fast-forward only, never push
      git -C "${VRA_HOME}" pull --quiet --ff-only origin "${ref}"
    fi
    ;;
  bundle-out)
    dest="${2:?destination dir required}"
    mkdir -p "${dest}/artifacts"
    if [ -d "${VRA_HOME}/artifacts" ]; then
      cp -R "${VRA_HOME}/artifacts/." "${dest}/artifacts/"
    fi
    ;;
  bundle-in)
    src="${2:?source dir required}"
    mkdir -p "${VRA_HOME}/artifacts"
    if [ -d "${src}/artifacts" ]; then
      cp -R "${src}/artifacts/." "${VRA_HOME}/artifacts/"
    fi
    ;;
  adopt)
    # The publish job (github-actions/vra-home-publish.yml): take over state/, checkpoints/ and artifacts/ that
    # another job wrote (an artifact in the vra-home layout). Nothing else is copied; links are refused.
    src="${2:?source dir required}"
    if [ -n "$(find "${src}" -type l -print -quit 2>/dev/null)" ]; then
      log "refusing to adopt ${src}: it contains symbolic links"
      exit 20
    fi
    for d in state checkpoints artifacts; do
      if [ -d "${src}/${d}" ]; then
        mkdir -p "${VRA_HOME}/${d}"
        cp -R "${src}/${d}/." "${VRA_HOME}/${d}/"
      fi
    done
    guard_paths
    ;;
  *)
    log "unknown command ${cmd}"
    exit 2
    ;;
esac
