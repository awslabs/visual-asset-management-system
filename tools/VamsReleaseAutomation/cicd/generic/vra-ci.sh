#!/usr/bin/env bash
# vra in any CI system (Jenkins, Azure Pipelines, Buildkite, TeamCity, ...): one entry script per step, the
# same commands, exit codes and artifacts as the CodeBuild, GitHub Actions and GitLab lanes.
#
#   vra-ci.sh home                                  clone / reuse the home folder, pull
#   vra-ci.sh validate                              ledger validate + preflight of VRA_PREFLIGHT_ENVS
#   vra-ci.sh build --lane release --ref <ref>      unit suites, synth, bundle; prints the build id last
#   vra-ci.sh deploy --env E --build B [--promote-from P] [--approver A --approved-at T --comment C]
#                                                   deploy (root) or promote; an approval record is written
#                                                   only when the ledger gates E (requiresApproval) and is
#                                                   then required: the approver comes from the CI's own
#                                                   approval step, never from the person who started the run
#   vra-ci.sh test --env E --suites S [--fork-dir D] live suites (run from a fork checkout at the build commit)
#   vra-ci.sh watch [watch|sync]                    the scheduled entry point
#   vra-ci.sh operator --action A --env E --approver X [--approved-at T] [--comment C] -- <vra args>
#                                                   a human-only action (unlock, resume --takeover / --confirm,
#                                                   --abort-workflow-executions, ...): writes the human:<A> record
#                                                   from the CI's approval step and runs vra --human-approval
#   vra-ci.sh cron                                  prints the crontab line for `vra watch` on this home
#                                                   (`vra watch --print-cron`; a host without a CI scheduler)
#   vra-ci.sh finish                                report --notify, then publish the state this job wrote
#
# Deploy steps wait up to VRA_LOCK_WAIT_MINUTES (default 30) for a busy env lock (vra --lock-wait). Never export
# a VAMS config variable (STACK_NAME, REGION, ADMIN_EMAIL_ADDRESS, ...) into the job: run-vra.sh refuses them.
#
# Contract (set by the CI before every step; the engine reads them, VRA_CI_PROVIDER=generic):
#   VRA_CI_PROVIDER=generic
#   VRA_INITIATOR      who started this run, as the CI authenticated them (Jenkins build cause user, Azure
#                      Build.RequestedForEmail). Never a value the person can type in.
#   VRA_RUN_REF        stable, unique id of this run, e.g. jenkins:<job>/<build> or azure-devops:<project>/<buildId>.
#                      Approval records are bound to it.
#   VRA_APPROVAL_REF   the CI's id of the approval (Jenkins input id, Azure approval id), recorded in the record
#   VRA_HOME, VRA_HOME_REPO, VRA_HOME_REF, VRA_HOME_TOKEN   the home folder (cicd/codebuild/scripts/vra-home.sh)
#   AWS credentials of the job's tier (one identity per env; see cicd/README.md "Which identity runs what")
# Outputs: vra-out/<step>.json (vra stdout), vra-out/<step>.result.json, ${VRA_HOME}/state/runs/** (logs,
# JUnit, report.md, IT requests). Exit codes: 0 ok, 10 blocked on IT, 20 needs a human, 30 tests failed,
# 40 rollback failed, 2 usage, 1 error (cicd/README.md).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
scripts="$(cd "${here}/../codebuild/scripts" && pwd)"
step="${1:?usage: vra-ci.sh home|validate|build|deploy|test|watch|operator|cron|finish ...}"
shift

die() { echo "[vra-ci] $*" >&2; exit 2; }

require_contract() {
  [ "${VRA_CI_PROVIDER:-}" = "generic" ] || die "VRA_CI_PROVIDER must be 'generic'"
  [ -n "${VRA_INITIATOR:-}" ] || die "VRA_INITIATOR is not set (the CI's authenticated run initiator)"
  [ -n "${VRA_RUN_REF:-}" ] || die "VRA_RUN_REF is not set (a unique id of this CI run)"
  [ -n "${VRA_HOME:-}" ] || die "VRA_HOME is not set"
}

if [ "${step}" = "cron" ]; then
  # a plain host with cron: only the home folder is needed (no CI run, no initiator)
  [ -n "${VRA_HOME:-}" ] || die "VRA_HOME is not set"
else
  require_contract
fi
run() { bash "${scripts}/run-vra.sh" "$@"; }

case "${step}" in
  home)
    bash "${scripts}/vra-home.sh" checkout
    bash "${scripts}/vra-home.sh" sync home-pull
    ;;
  validate)
    run ledger-validate ledger validate
    rc=0
    for env in ${VRA_PREFLIGHT_ENVS:-}; do
      for wf in ${VRA_PREFLIGHT_WORKFLOWS:-deploy backup migrate test-live}; do
        run "preflight-${env}-${wf}" preflight --env "${env}" --workflow "${wf}" || rc=$?
      done
    done
    exit "${rc}"
    ;;
  build)
    run build build "$@"
    bash "${scripts}/vra-home.sh" bundle-out vra-bundle
    python3 -c 'import json;print(json.load(open("vra-out/build.json"))["buildId"])'
    ;;
  deploy)
    env="" build="" from="" approver="" approved_at="" comment=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --env) env="$2"; shift 2 ;;
        --build) build="$2"; shift 2 ;;
        --promote-from) from="$2"; shift 2 ;;
        --approver) approver="$2"; shift 2 ;;
        --approved-at) approved_at="$2"; shift 2 ;;
        --comment) comment="$2"; shift 2 ;;
        *) die "unknown deploy argument $1" ;;
      esac
    done
    [ -n "${env}" ] && [ -n "${build}" ] || die "deploy needs --env and --build"
    [ -d vra-bundle ] && bash "${scripts}/vra-home.sh" bundle-in vra-bundle
    mkdir -p vra-out
    approval=()
    needs_approval="$(bash "${scripts}/vra-py.sh" ci_graph.py requires-approval --ledger "${VRA_HOME}/ledger" --env "${env}")"
    if [ "${needs_approval}" = "true" ]; then
      [ -n "${approver}" ] || { echo "[vra-ci] ${env} requires approval: pass --approver from the CI approval step" >&2; exit 20; }
      scope=promote
      [ -z "${from}" ] && scope=deploy
      python3 "${scripts}/approval_record.py" generic --approver "${approver}" --approved-at "${approved_at}" \
        --comment "${comment}" --env "${env}" --build "${build}" --scope "${scope}" --out "vra-out/approval-${env}.yaml"
      approval=(--approval "vra-out/approval-${env}.yaml")
    fi
    if [ -n "${from}" ]; then
      run "promote-${env}" promote --from "${from}" --to "${env}" --build "${build}" "${approval[@]}" \
        --lock-wait "${VRA_LOCK_WAIT_MINUTES:-30}"
    else
      run "deploy-${env}" deploy --env "${env}" --build "${build}" "${approval[@]}" \
        --lock-wait "${VRA_LOCK_WAIT_MINUTES:-30}"
    fi
    ;;
  test)
    env="" suites="live-cli,live-web,customer" fork_dir="fork"
    while [ $# -gt 0 ]; do
      case "$1" in
        --env) env="$2"; shift 2 ;;
        --suites) suites="$2"; shift 2 ;;
        --fork-dir) fork_dir="$2"; shift 2 ;;
        *) die "unknown test argument $1" ;;
      esac
    done
    [ -n "${env}" ] || die "test needs --env"
    out="$(pwd)/vra-out"
    if [[ ",${suites}," == *",live-web,"* ]]; then
      (cd "${fork_dir}/web" && npm ci --no-audit --no-fund && npx playwright install --with-deps chromium)
    fi
    (cd "${fork_dir}" && VRA_OUT_DIR="${out}" bash "${scripts}/run-vra.sh" "test-${env}" test --env "${env}" --suites "${suites}")
    ;;
  watch)
    cmd="${1:-watch}"
    case "${cmd}" in watch | sync) ;; *) die "watch takes watch or sync" ;; esac
    VRA_SOFT_EXIT_CODES="${VRA_SOFT_EXIT_CODES-10 20}" run watch "${cmd}"
    ;;
  operator)
    action="" env="" approver="" approved_at="" comment=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --action) action="$2"; shift 2 ;;
        --env) env="$2"; shift 2 ;;
        --approver) approver="$2"; shift 2 ;;
        --approved-at) approved_at="$2"; shift 2 ;;
        --comment) comment="$2"; shift 2 ;;
        --) shift; break ;;
        *) die "unknown operator argument $1 (vra arguments follow --)" ;;
      esac
    done
    [ -n "${action}" ] && [ -n "${env}" ] || die "operator needs --action and --env"
    [ $# -gt 0 ] || die "operator needs the vra command after --"
    [ -n "${approver}" ] || { echo "[vra-ci] ${action} is human-only: pass --approver from the CI approval step" >&2; exit 20; }
    mkdir -p vra-out
    python3 "${scripts}/approval_record.py" generic --human "${action}" --approver "${approver}" \
      --approved-at "${approved_at}" --comment "${comment}" --env "${env}" --out "vra-out/human-${action}.yaml"
    VRA_HUMAN_APPROVAL="vra-out/human-${action}.yaml" run "operator-${action}" "$@"
    ;;
  cron)
    VRA_OUT_DIR="${VRA_OUT_DIR:-$(pwd)/vra-out}" run cron watch --print-cron
    python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["result"]["cron"])' "${VRA_OUT_DIR:-$(pwd)/vra-out}/cron.json"
    ;;
  finish)
    run report report --notify || true
    bash "${scripts}/vra-home.sh" sync home-push
    ;;
  *)
    die "unknown step ${step}"
    ;;
esac
