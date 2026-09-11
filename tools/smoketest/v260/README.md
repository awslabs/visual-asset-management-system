# VAMS v2.6.0+ smoke tooling

Workstation scripts and their offline tests for the smoke rounds of the built-in pipelines. Nothing
here is deployed; the scripts run against a named sandbox with the caller's own credentials.

## Video SOP/BOM fixtures and R0 probes

The `genai-video-sop-bom` pipeline needs narrated video, and the sandbox has none, so
[`make_teardown_videos.py`](make_teardown_videos.py) builds a synthetic set: Amazon Polly narrates a
scripted ten-step teardown, PIL captions one frame per second, and the `imageio-ffmpeg` binary muxes the
clips (two happy-path parts, five tiny clips, a 200 s low-bitrate clip, an under/over size pair, a silent
clip and a 30 s `anullsrc` FLAC). Output lands in `_video_sop_bom_fixtures/` (or `--out-dir`), which
carries its own `.gitignore` — fixtures are generated, never committed — and `FIXTURE_FILES` maps the
suite's roles (`happy`, `tiny`, `long`, `size_small`, `size_big`, `silent`, `probe_flac`) to the file
names. `--without-polly` exercises the whole mux path offline with silence in place of speech.

[`video_sop_bom_r0_probes.py`](video_sop_bom_r0_probes.py) runs the spec's R0 pre-deploy probes. Each
probe prints `[OBSERVED] key = value` lines, or `[SKIP] <probe> -- <reason>` when a prerequisite flag is
missing; there is no PASS line, because the point is to record the values the design guessed at before a
test is pinned to them. `--region` is required and never inferred from the profile. The JSON report
(`--report`, alias `--json-out`) carries the flat keys `suite_video_sop_bom.py --record R0=<report>`
reads at its top level and every observation under `probes`.

```bash
python tools/smoketest/v260/make_teardown_videos.py --region us-east-1 --aws-profile <profile>
python tools/smoketest/v260/video_sop_bom_r0_probes.py --list-probes
python tools/smoketest/v260/video_sop_bom_r0_probes.py --region us-east-1 --aws-profile <profile> \
    --aux-bucket <aux bucket> --kms-key-arn <cmk arn> --create-role \
    --batch-job-queue <queue> --batch-job-definition <job definition>
python -m pytest tools/smoketest/v260/test_video_sop_bom_fixtures_and_probes.py -q   # offline, 13 tests
```
