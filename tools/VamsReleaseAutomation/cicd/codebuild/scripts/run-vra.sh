#!/usr/bin/env bash
# Run one `vra` command non-interactively and map its exit code to a CI result. Used by every CI lane
# (CodeBuild, GitHub Actions, GitLab CI, cicd/generic).
#
#   run-vra.sh <result-name> <vra args...>
#
# - Always adds --json --non-interactive (machine output on stdout, logs on stderr).
# - Writes stdout to ${VRA_OUT_DIR}/<result-name>.json and a summary to
#   ${VRA_OUT_DIR}/<result-name>.result.json {name, exitCode, outcome, runId}. When vra exits without
#   printing its JSON envelope (a crash), the script writes one ({exitCode, status: FAILED, message}) so a
#   caller always has JSON to read.
# - vra exit codes:
#     0  ok                    -> job SUCCEEDED
#     10 BLOCKED_ON_IT         -> job FAILED (IT request is in the run dir artifact)
#     20 NEEDS_HUMAN           -> job FAILED (conflicts / missing approval / lock held by another host)
#     30 TESTS_FAILED          -> job FAILED (JUnit report shows the failures)
#     40 ROLLBACK_FAILED       -> job FAILED (page a human)
#     2  usage error, 1 other  -> job FAILED
#   VRA_SOFT_EXIT_CODES="10 20" turns the listed codes into a SUCCEEDED job (used by the scheduled
#   `vra watch` jobs: a parked run is reported by vra itself and should not page).
#
# Home folder: VRA_HOME (see vra-home.sh). VRA_LEDGER defaults to ${VRA_HOME}/ledger.
# VRA_HUMAN_APPROVAL=<record> adds --human-approval <record>: the approval record (subject human:<action>,
#   scope [<action>]) that authorizes a human-only action in a job without a terminal (approval_record.py --human).
# A VAMS config variable in the job environment (STACK_NAME, REGION, ADMIN_EMAIL_ADDRESS, ...) stops the
#   command before vra runs (exit 2): vra would stop the build for it unless env.yaml synthEnv records it, and a
#   CI job never exports them (the env's values come from the ledger overlay and synthEnv).
set -uo pipefail

name="${1:?result name required}"
shift

out_dir="${VRA_OUT_DIR:-${CODEBUILD_SRC_DIR:-.}/vra-out}"
mkdir -p "${out_dir}"
stdout_file="${out_dir}/${name}.json"

export PATH="${HOME}/.local/bin:${PATH}"
if [ -n "${VRA_HOME:-}" ]; then
  export VRA_LEDGER="${VRA_LEDGER:-${VRA_HOME}/ledger}"
fi
# A non-editable install only sees the engine's packaged default manifests; point it at the
# reviewed iam/actions/ of the pinned package checkout (this script lives in <pkg>/cicd/codebuild/scripts).
if [ -z "${VRA_IAM_ACTIONS_DIR:-}" ]; then
  pkg_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
  [ -d "${pkg_root}/iam/actions" ] && export VRA_IAM_ACTIONS_DIR="${pkg_root}/iam/actions"
fi

# keep in sync with engine/vra/ledger.py VAMS_CONFIG_ENV_VARS
vams_config_vars=(STACK_NAME REGION ADMIN_EMAIL_ADDRESS ADMIN_USER_ID CRED_TOKEN_TIMEOUT_SECONDS
  PRESIGNED_URL_TIMEOUT_SECONDS AWS_USE_FIPS_ENDPOINT AWS_USE_WAF DEFAULT_USER_ROLE_NAME)
exported=()
for v in "${vams_config_vars[@]}"; do
  if [ -n "${!v+x}" ]; then exported+=("${v}"); fi
done

extra=()
if [ -n "${VRA_HUMAN_APPROVAL:-}" ]; then
  extra+=(--human-approval "${VRA_HUMAN_APPROVAL}")
fi

# Inside a CodePipeline execution, build / deploy / promote / test / rollback runs get a run id bound to the
# execution and the action (pipeline_ci.py run-plan). "Retry failed actions" then continues a parked run
# (BLOCKED_ON_IT after IT granted the access, NEEDS_HUMAN) with `vra resume` instead of starting over.
# VRA_PIPELINE_RESUME=false turns this off.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cmd_args=("$@")
if [ -n "${VRA_PIPELINE_EXECUTION_ID:-}" ] && [ "${VRA_PIPELINE_RESUME:-true}" = "true" ]; then
  plan="$(bash "${here}/vra-py.sh" pipeline_ci.py run-plan "${name}" "${1:-}" 2>/dev/null || echo plain)"
  case "${plan}" in
    "start "*) extra+=(--run-id "${plan#start }") ;;
    "resume "*)
      cmd_args=(resume "${plan#resume }")
      echo "[run-vra] retry of ${name} in this pipeline execution: resuming run ${plan#resume }" >&2
      ;;
  esac
fi

# report-run begin: `report` without a run id reports (and notifies) the run this job ran, not the newest run in
# the home by start time (a resumed run is older than any later watch run, so a Deploy-prod job
# notified the morning's watch-sync run instead of the promote it had just finished)
if [ "${cmd_args[0]:-}" = "report" ] && [ -s "${out_dir}/last-run-id" ]; then
  has_id=false
  for a in "${cmd_args[@]:1}"; do
    case "${a}" in -*) ;; *) has_id=true ;; esac
  done
  if [ "${has_id}" = "false" ]; then
    cmd_args=(report "$(head -n1 "${out_dir}/last-run-id")" "${cmd_args[@]:1}")
  fi
fi
# report-run end

if [ "${#exported[@]}" -gt 0 ]; then
  msg="the job environment sets VAMS config variable(s) ${exported[*]}: unset them in the CI image / job (record a value in envs/<env>/env.yaml synthEnv if the build needs one)"
  echo "[run-vra] ${msg}" >&2
  python3 - "${stdout_file}" "${msg}" <<'PY'
import json, sys
json.dump({"exitCode": 2, "status": "FAILED", "message": sys.argv[2]}, open(sys.argv[1], "w"), indent=2)
PY
  code=2
else
  echo "[run-vra] vra ${cmd_args[*]} ${extra[*]} --json --non-interactive" >&2
  vra "${cmd_args[@]}" "${extra[@]}" --json --non-interactive >"${stdout_file}"
  code=$?
fi

case "${code}" in
  0) outcome="OK" ;;
  10) outcome="BLOCKED_ON_IT" ;;
  20) outcome="NEEDS_HUMAN" ;;
  30) outcome="TESTS_FAILED" ;;
  40) outcome="ROLLBACK_FAILED" ;;
  2) outcome="USAGE_ERROR" ;;
  *) outcome="ERROR" ;;
esac

run_id="$(python3 - "${stdout_file}" "${code}" "${outcome}" <<'PY' 2>/dev/null || true
import json, sys
path, code, outcome = sys.argv[1], int(sys.argv[2]), sys.argv[3]
try:
    data = json.load(open(path))
except Exception:
    # no envelope on stdout: write one, so callers that parse it get the exit code and a message
    data = {"exitCode": code, "status": "FAILED" if code else "OK",
            "message": f"vra exited {code} ({outcome}) without JSON output; see the job log"}
    json.dump(data, open(path, "w"), indent=2)
print(data.get("runId", "") if isinstance(data, dict) else "")
PY
)"

python3 - "${out_dir}/${name}.result.json" "${name}" "${code}" "${outcome}" "${run_id}" <<'PY'
import json, sys
path, name, code, outcome, run_id = sys.argv[1:]
json.dump({"name": name, "exitCode": int(code), "outcome": outcome, "runId": run_id},
          open(path, "w"), indent=2)
PY

# report-run begin: remember this job's run for a later `report` step (a report never replaces it)
if [ -n "${run_id}" ] && [ "${cmd_args[0]:-}" != "report" ]; then
  printf '%s\n' "${run_id}" >"${out_dir}/last-run-id"
fi
# report-run end

echo "[run-vra] ${name}: exit ${code} (${outcome}) run ${run_id:-n/a}" >&2

case "${code}" in
  10)
    echo "[run-vra] Permission gap. Read it-requests/IT-REQUEST-*.md in the run artifact, then 'vra resume ${run_id}'." >&2
    if [ -n "${VRA_PIPELINE_EXECUTION_ID:-}" ]; then
      # the action fails with the IT request / IT package as its artifact; a retry resumes the run
      bash "${here}/vra-py.sh" pipeline_ci.py blocked-summary "${out_dir}" "${VRA_HOME:-.}" "${name}" >&2 || true
    fi
    ;;
  20) echo "[run-vra] Human decision needed (conflicts.json, approval, or an env lock held elsewhere). See report.md in the run artifact." >&2 ;;
  30) echo "[run-vra] Tests failed. See the JUnit report." >&2 ;;
  40) echo "[run-vra] ROLLBACK FAILED. The environment may be inconsistent: page the on-call owner." >&2 ;;
esac

for soft in ${VRA_SOFT_EXIT_CODES:-}; do
  if [ "${code}" = "${soft}" ]; then
    echo "[run-vra] exit ${code} is configured as soft; reporting success" >&2
    exit 0
  fi
done

exit "${code}"
