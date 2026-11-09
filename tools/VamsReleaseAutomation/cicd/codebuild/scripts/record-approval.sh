#!/usr/bin/env bash
# Turn the CodePipeline manual approval of THIS pipeline execution into a vra approval record.
#
#   record-approval.sh <env> <build-id> <out-file> [promote|deploy]
#
# Lists the action executions of ${VRA_PIPELINE_NAME} filtered by ${VRA_PIPELINE_EXECUTION_ID} (passed by
# the Deploy action as #{codepipeline.PipelineExecutionId}) and takes the approval action
# ${VRA_APPROVAL_STAGE}/${VRA_APPROVAL_ACTION} of that execution only. In a QUEUED pipeline several
# executions can wait at once; an approval given for another execution (another build) never counts.
# The record is bound to the execution (source codepipeline:<pipeline>/<executionId>, binding with the
# CodeBuild build id), scoped to promote (or deploy for a gated env without promotesFrom), and expires
# after 24 h. vra checks the approver against
# envs/<env>/env.yaml approvers (ARN entries), against the execution's initiator (SoD), and that no other
# run consumed it. Name the output *.yaml (JSON is valid YAML).
# Approval comment convention: "ticket: CHG-1234; scope: emergency; notes: free text" (scope optional).
# Needs codepipeline:ListActionExecutions on the pipeline.
set -euo pipefail

env_name="${1:?env required}"
build_id="${2:?build id required}"
out_file="${3:?output file required}"
scope="${4:-promote}"

: "${VRA_PIPELINE_NAME:?VRA_PIPELINE_NAME not set}"
: "${VRA_PIPELINE_EXECUTION_ID:?VRA_PIPELINE_EXECUTION_ID not set (pass #{codepipeline.PipelineExecutionId})}"
: "${VRA_APPROVAL_STAGE:?VRA_APPROVAL_STAGE not set}"
: "${VRA_APPROVAL_ACTION:?VRA_APPROVAL_ACTION not set}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec_file="$(mktemp)"
trap 'rm -f "${exec_file}"' EXIT
aws codepipeline list-action-executions --pipeline-name "${VRA_PIPELINE_NAME}" \
  --filter "pipelineExecutionId=${VRA_PIPELINE_EXECUTION_ID}" --output json >"${exec_file}"

python3 "${here}/approval_record.py" codepipeline --executions "${exec_file}" \
  --stage "${VRA_APPROVAL_STAGE}" --action "${VRA_APPROVAL_ACTION}" \
  --env "${env_name}" --build "${build_id}" --out "${out_file}" --scope "${scope}"
