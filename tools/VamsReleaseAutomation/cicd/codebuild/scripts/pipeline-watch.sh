#!/usr/bin/env bash
# Scheduled watch when a CodePipeline owns the deploys (pipeline config watch.command "sync-merge", the default
# for automation.mode hybrid / pipeline / pipeline+actions). Runs inside buildspec-watch.yml:
#   1. vra sync: fetch upstream, fast-forward the vendor branches, detect a new release tag and new development
#      commits (the upstream refs come from vra.yaml lanes.*.source, e.g. a test/* branch of a fork).
#   2. vra merge per lane with something new: the release lane opens a sync PR into the trunk (a person merges it;
#      the merge commit starts the release pipeline through its source), the preview lane merges into its branch
#      (starts the preview pipeline).
#   3. S3 sources the stack created (VRA_SOURCE_BUCKET + VRA_SOURCE_RELEASE_KEY / VRA_SOURCE_PREVIEW_KEY):
#      vra pipeline push-source uploads the branch when its commit changed and starts the pipeline.
# Never deploys: deploys happen only in the pipeline. Exit codes follow run-vra.sh; VRA_SOFT_EXIT_CODES applies.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out_dir="${VRA_OUT_DIR:-${CODEBUILD_SRC_DIR:-.}/vra-out}"
mkdir -p "${out_dir}"
worst=0
note() { [ "$1" -gt "${worst}" ] && worst="$1"; return 0; }

bash "${here}/run-vra.sh" watch-sync sync
code=$?
note "${code}"
if [ "${code}" -eq 0 ] || [ -s "${out_dir}/watch-sync.json" ]; then
  while read -r lane ref; do
    [ -n "${lane}" ] || continue
    bash "${here}/run-vra.sh" "merge-${lane}" merge --lane "${lane}" --ref "${ref}"
    note $?
  done < <(bash "${here}/vra-py.sh" pipeline_ci.py watch-merges "${out_dir}/watch-sync.json")
fi

if [ -n "${VRA_SOURCE_BUCKET:-}" ]; then
  if [ -n "${VRA_SOURCE_RELEASE_KEY:-}" ]; then
    bash "${here}/run-vra.sh" push-source-release pipeline push-source --lane release \
      --bucket "${VRA_SOURCE_BUCKET}" --key "${VRA_SOURCE_RELEASE_KEY}" --pipeline "${VRA_RELEASE_PIPELINE:-}"
    note $?
  fi
  if [ -n "${VRA_SOURCE_PREVIEW_KEY:-}" ]; then
    bash "${here}/run-vra.sh" push-source-preview pipeline push-source --lane preview \
      --bucket "${VRA_SOURCE_BUCKET}" --key "${VRA_SOURCE_PREVIEW_KEY}" --pipeline "${VRA_PREVIEW_PIPELINE:-}"
    note $?
  fi
fi

echo "[pipeline-watch] done: exit ${worst}" >&2
exit "${worst}"
