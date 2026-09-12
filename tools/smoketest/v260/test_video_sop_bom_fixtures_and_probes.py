"""Offline tests for the Video SOP/BOM fixture generator and R0 probe script.

Both scripts are imported by the smoke suite (the generator for `STEPS`/`KEYWORDS`, the probes for
their registry), so their module-level contract is pinned here without AWS, ffmpeg or Polly. The
import-time guard matters most: a client built at import would make `python -c "import ..."` a
credentialed call, and the suite imports these on every run.

Run:  python -m pytest tools/smoketest/v260/test_video_sop_bom_fixtures_and_probes.py -q
"""

from __future__ import annotations

import importlib
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _fresh_import(name, monkeypatch):
    """Import `name` with every boto3 constructor replaced by one that fails the test."""
    import boto3  # noqa: PLC0415

    def _boom(*args, **kwargs):
        raise AssertionError(f"AWS client construction at import time: {args} {kwargs}")

    monkeypatch.setattr(boto3, "client", _boom)
    monkeypatch.setattr(boto3, "resource", _boom)
    monkeypatch.setattr(boto3, "Session", _boom)
    sys.modules.pop(name, None)
    return importlib.import_module(name)


class TestFixtureGenerator:
    def test_fresh_import_guard_catches_an_import_time_client(self, tmp_path, monkeypatch):
        """Control for the two `no AWS calls` tests: a module that does build a client at import trips the guard."""
        (tmp_path / "vsb_import_time_client.py").write_text(
            "import boto3\nCLIENT = boto3.client('sts', region_name='us-east-1')\n", encoding="utf-8")
        monkeypatch.syspath_prepend(str(tmp_path))
        with pytest.raises(AssertionError, match="AWS client construction at import time"):
            _fresh_import("vsb_import_time_client", monkeypatch)

    def test_importing_the_generator_makes_no_aws_calls(self, monkeypatch):
        gen = _fresh_import("make_teardown_videos", monkeypatch)
        assert gen.PRODUCT_NAME == "Cognex DataMan 80 barcode reader"

    def test_narration_exposes_keywords_and_steps(self):
        import make_teardown_videos as gen  # noqa: PLC0415
        assert len(gen.KEYWORDS) >= 12
        assert all(k == k.lower() == k.strip() and k.isascii() for k in gen.KEYWORDS)
        assert len(gen.STEPS) >= 8
        assert [s.number for s in gen.STEPS] == list(range(1, len(gen.STEPS) + 1))
        narration = " ".join(s.text for s in gen.STEPS).lower()
        assert all(k in narration for k in gen.KEYWORDS), [k for k in gen.KEYWORDS if k not in narration]
        assert gen.COMPONENT_NOUNS == tuple(s.component for s in gen.STEPS)
        assert all(s.component in s.text.lower() for s in gen.STEPS)
        assert len(set(gen.COMPONENT_NOUNS)) == len(gen.COMPONENT_NOUNS)

    def test_chunk_text_respects_the_polly_limit(self):
        import make_teardown_videos as gen  # noqa: PLC0415
        assert gen.chunk_text("A b. C d. E f.", limit=6) == ["A b.", "C d.", "E f."]
        assert gen.chunk_text("A b. C d. E f.", limit=9) == ["A b. C d.", "E f."]
        assert gen.chunk_text("  one   two.  ") == ["one two."]
        with pytest.raises(ValueError):
            gen.chunk_text("x" * 10 + ".", limit=6)
        assert gen.POLLY_MAX_CHARS == 3000
        assert all(len(gen.chunk_text(s.text)) == 1 for s in gen.STEPS)

    def test_fixture_file_names_match_d13(self):
        import make_teardown_videos as gen  # noqa: PLC0415
        assert gen.HAPPY_PAIR == ("teardown-part1.mp4", "teardown-part2.MP4")
        assert gen.TINY_CLIPS == tuple(f"tiny-{i}.mp4" for i in range(1, 6))
        assert gen.LONG_CLIP == "teardown-long-200s.mp4" and gen.LONG_CLIP_SECONDS == 200 > 180
        assert gen.SIZE_PAIR == ("size-small.mp4", "size-big.mp4")
        assert gen.SILENT_CLIP == "silent.mp4" and gen.SILENT_CLIP_SECONDS == 30
        assert gen.SILENT_FLAC == "silence-30s.flac" and gen.SILENT_FLAC_SECONDS == 30
        expected = set(gen.HAPPY_PAIR) | set(gen.TINY_CLIPS) | {gen.LONG_CLIP, gen.SILENT_CLIP, gen.SILENT_FLAC} | set(gen.SIZE_PAIR)
        assert set(gen.FIXTURE_DESCRIPTIONS) == expected and len(expected) == 12
        # The role map is what the smoke suite reads (`FIXTURE_FILES[role]`); every file has exactly one role.
        assert gen.FIXTURE_ROLES == ("happy", "tiny", "long", "size_small", "size_big", "silent", "probe_flac")
        assert tuple(gen.FIXTURE_FILES) == gen.FIXTURE_ROLES
        assert gen.FIXTURE_FILES["happy"] == list(gen.HAPPY_PAIR) and gen.FIXTURE_FILES["tiny"] == list(gen.TINY_CLIPS)
        assert gen.FIXTURE_FILES["long"] == gen.LONG_CLIP and gen.FIXTURE_FILES["probe_flac"] == gen.SILENT_FLAC
        assert (gen.FIXTURE_FILES["size_small"], gen.FIXTURE_FILES["size_big"]) == gen.SIZE_PAIR
        flattened = [n for v in gen.FIXTURE_FILES.values() for n in ([v] if isinstance(v, str) else v)]
        assert sorted(flattened) == sorted(expected) and len(flattened) == 12
        assert gen.DEFAULT_OUT == os.path.join(HERE, "_video_sop_bom_fixtures")
        # A no-speech input is rejected on the task token, so the silent fixture
        # exercises that rejection rather than an empty-deliverable success.
        assert "VideoSopBomInputRejected" in gen.FIXTURE_DESCRIPTIONS[gen.SILENT_CLIP]

    def test_frame_captions_maps_seconds_to_steps(self):
        import make_teardown_videos as gen  # noqa: PLC0415
        assert gen.frame_captions(["S1", "S2"], [2.0, 3.0]) == ["S1", "S1", "S2", "S2", "S2"]
        assert gen.frame_captions(["S1"], [1.5]) == ["S1", "S1"]
        assert gen.frame_captions(["S1", "S2"], [0.4, 0.4]) == ["S1"]

    def test_part_scripts_split_the_steps(self):
        import make_teardown_videos as gen  # noqa: PLC0415
        assert gen.PART_STEPS[1] + gen.PART_STEPS[2] == gen.STEPS
        assert len(gen.PART_STEPS[1]) >= 4 and len(gen.PART_STEPS[2]) >= 4
        assert gen.PART_STEPS[1][0].text.startswith("Step one.")


R0_PROBES = (
    "transcribe-conditioned-statement", "silent-flac-transcription", "language-codes", "bedrock-converse",
    "vpc-endpoint-services", "fargate-vcpu-quota", "batch-9kb-command", "batch-failing-container-cause",
)


class _FakeSts:
    def get_caller_identity(self):
        return {"Arn": "arn:aws-us-gov:sts::123456789012:assumed-role/Admin/probe", "Account": "123456789012",
                "UserId": "AROAEXAMPLE:probe"}


class _FakeSession:
    """Only STS answers; any other client means a probe touched AWS before its SKIP check."""

    def client(self, service, **kwargs):
        if service == "sts":
            return _FakeSts()
        raise AssertionError(f"probe built a {service!r} client without its prerequisites")


class TestR0Probes:
    def test_probe_registry_matches_the_r0_table(self, monkeypatch):
        probes = _fresh_import("video_sop_bom_r0_probes", monkeypatch)
        assert tuple(probes.PROBES) == R0_PROBES
        assert all(probes.PROBE_SETTLES[name] for name in R0_PROBES)
        # The probe's copy of the enum is pinned to the container's vocab (the same repository, three
        # directories up), and the batch-table confirmation is data the enum must match.
        container = os.path.join(HERE, "..", "..", "..", "backendPipelines", "genAi", "videoSopBom", "container")
        assert os.path.isdir(container), container
        sys.path.insert(0, os.path.abspath(container))
        from video_sop_bom_pipeline import vocab  # noqa: PLC0415

        assert probes.LANGUAGE_CODES == vocab.LANGUAGE_CODES
        assert probes.LANGUAGE_CODES[0] == "auto"
        assert set(probes.LANGUAGE_CODES[1:]) == set(probes.BATCH_CONFIRMED["codes"])
        assert probes.BATCH_CONFIRMED["date"] == "2026-09-09" and probes.BATCH_CONFIRMED["source"].startswith("https://")

    def test_probe_role_policy_is_exactly_the_d7_statement(self):
        import video_sop_bom_r0_probes as probes  # noqa: PLC0415
        key = "arn:aws-us-gov:kms:us-gov-west-1:123456789012:key/0f1e2d3c"
        policy = probes.build_probe_role_policy("vams-aux", key, "aws-us-gov", "us-gov-west-1", "123456789012")
        start, jobs, s3, kms = policy["Statement"]
        assert start["Action"] == ["transcribe:StartTranscriptionJob"] and start["Resource"] == "*"
        assert start["Condition"] == {"StringEquals": {
            "transcribe:OutputBucketName": "vams-aux", "transcribe:OutputEncryptionKMSKeyId": key}}
        assert jobs["Action"] == ["transcribe:GetTranscriptionJob", "transcribe:DeleteTranscriptionJob"]
        assert jobs["Resource"] == "arn:aws-us-gov:transcribe:us-gov-west-1:123456789012:transcription-job/vams-video-sop-bom-*"
        assert s3["Resource"] == ["arn:aws-us-gov:s3:::vams-aux", "arn:aws-us-gov:s3:::vams-aux/*"]
        assert "s3:DeleteObject" in s3["Action"]
        assert kms["Resource"] == key and "kms:GenerateDataKey*" in kms["Action"]
        without_key = probes.build_probe_role_policy("vams-aux", "", "aws", "us-east-1", "123456789012")
        assert len(without_key["Statement"]) == 3
        assert without_key["Statement"][0]["Condition"] == {"StringEquals": {"transcribe:OutputBucketName": "vams-aux"}}
        assert "transcribe:OutputEncryptionKMSKeyId" not in json.dumps(without_key)

    def test_probe_without_prerequisite_prints_skip_never_pass(self, tmp_path, capsys):
        import video_sop_bom_r0_probes as probes  # noqa: PLC0415
        report = tmp_path / "r0.json"
        rc = probes.main(["--region", "us-gov-west-1", "--only", "batch-9kb-command",
                          "--only", "transcribe-conditioned-statement", "--report", str(report)],
                         session_factory=lambda args: _FakeSession())
        out = capsys.readouterr().out
        assert rc == 0
        assert "identity: arn:aws-us-gov:sts::123456789012:assumed-role/Admin/probe" in out
        assert "[SKIP] batch-9kb-command -- needs --batch-job-queue and --batch-job-definition" in out
        assert "[SKIP] transcribe-conditioned-statement -- needs --aux-bucket" in out
        assert "PASS" not in out and "[OBSERVED]" not in out
        assert "R0 PROBES: observed 0  skipped 2  errors 0" in out
        saved = json.loads(report.read_text(encoding="utf-8"))
        assert set(saved["probes"]) == {"batch-9kb-command", "transcribe-conditioned-statement"}
        assert all("skip" in entry for entry in saved["probes"].values())
        assert saved["title"] == probes.REPORT_TITLE and saved["verdict"] == "RECORDED"
        assert saved["rows"] == [["batch-9kb-command", "SKIP: needs --batch-job-queue and --batch-job-definition"],
                                 ["transcribe-conditioned-statement", "SKIP: needs --aux-bucket"]]
        assert all(saved[key] is None for key in probes.FLAT_KEYS), "a skipped probe records no value"

    def test_language_codes_probe_prints_observed_lines_and_report_values(self, tmp_path, capsys):
        """Positive control for the [OBSERVED] path: this probe needs no credentials (the SDK model is
        local), so it completes under the fake session; the alias flag spellings are exercised here,
        while the live smoke suite passes the canonical ones."""
        import video_sop_bom_r0_probes as probes  # noqa: PLC0415
        report = tmp_path / "r0.json"
        rc = probes.main(["--region", "us-east-1", "--only", "language-codes", "--json-out", str(report),
                          "--model-id", "m", "--job-queue", "q", "--fixture-flac", "f.flac"],
                         session_factory=lambda args: _FakeSession())
        out = capsys.readouterr().out
        assert rc == 0
        for code in probes.LANGUAGE_CODES[1:]:
            assert f"  [OBSERVED] sdk_enum.{code} = True" in out, code
        assert "  [OBSERVED] auto = IdentifyLanguage=True (not a LanguageCode value)" in out
        assert "[SKIP]" not in out and "PASS" not in out
        assert "R0 PROBES: observed 14  skipped 0  errors 0" in out
        saved = json.loads(report.read_text(encoding="utf-8"))
        assert saved["probes"]["language-codes"]["sdk_enum.en-US"] is True
        assert "skip" not in saved["probes"]["language-codes"]
        assert saved["languageCodeEnumSupported"] == list(probes.LANGUAGE_CODES[1:])
        assert saved["rows"][0][0] == "language-codes" and saved["rows"][0][1].startswith("sdk_enum.en-US=True")
        assert saved["fargateOnDemandVcpuQuota"] is None, "a probe that did not run records None"

    def test_summarize_maps_probe_observations_onto_the_flat_keys(self):
        import video_sop_bom_r0_probes as probes  # noqa: PLC0415
        observations = {
            "silent-flac-transcription": {"en_us.status": "COMPLETED", "en_us.subtitle_file_uris": None,
                                          "auto.status": "FAILED", "seconds": 61.0},
            "fargate-vcpu-quota": {"applied_value": 6.0, "seconds": 0.4},
            "bedrock-converse": {"model_id": "m", "error": "AccessDeniedException: no access", "seconds": 0.3},
            "vpc-endpoint-services": {"com.amazonaws.us-east-1.transcribe": ["Interface"],
                                      "com.amazonaws.us-east-1.bedrock-runtime": "absent", "seconds": 0.2},
            "transcribe-conditioned-statement": {"start_with_key": "accepted", "start_without_key": "AccessDeniedException: x",
                                                 "start_mismatched_bucket": "AccessDeniedException: y", "seconds": 90.0},
            "batch-9kb-command": {"submit_job": "ClientException: too long", "seconds": 0.5},
            "batch-failing-container-cause": {"top_level_keys": ["container", "status", "statusReason"], "seconds": 40.0},
            "language-codes": {"sdk_enum.en-US": True, "sdk_enum.zh-CN": False, "seconds": 0.1},
        }
        flat = probes.summarize(observations, "us-east-1")
        assert set(probes.FLAT_KEYS) < set(flat) and flat["title"] == probes.REPORT_TITLE
        assert flat["identifyLanguageOnSilence"] == "FAILED" and flat["languageCodeOnSilence"] == "COMPLETED"
        assert flat["subtitleFileUrisOnSilence"] is False
        assert flat["fargateOnDemandVcpuQuota"] == 6.0
        assert flat["bedrockConverseProbe"] == "AccessDeniedException"
        assert flat["transcribeEndpointServiceName"] == "com.amazonaws.us-east-1.transcribe"
        assert flat["bedrockRuntimeEndpointServiceName"] is None
        assert flat["conditionedStartTranscriptionJob"] == {
            "withKey": "accepted", "withoutKey": "AccessDeniedException: x", "mismatchedBucket": "AccessDeniedException: y"}
        assert flat["overridesCapSurface"] == "validation"
        assert flat["noCallbackCauseKeys"] == ["container", "status", "statusReason"]
        assert flat["languageCodeEnumSupported"] == ["en-US"]
        assert [row[0] for row in flat["rows"]] == list(observations)
        accepted = {"bedrock-converse": {"stopReason": "max_tokens"},
                    "batch-9kb-command": {"submit_job": "accepted", "status": "FAILED"}}
        assert probes.summarize(accepted, "us-east-1")["bedrockConverseProbe"] == "ok"
        assert probes.summarize(accepted, "us-east-1")["overridesCapSurface"] == "job-failed"
        skipped = {"bedrock-converse": {"skip": "x", "seconds": 0.0}}
        assert all(probes.summarize(skipped, "us-east-1")[key] is None for key in probes.FLAT_KEYS)
        assert all(probes.summarize({}, "us-east-1")[key] is None for key in probes.FLAT_KEYS)

    def test_list_probes_and_language_enum_run_offline(self, capsys):
        import video_sop_bom_r0_probes as probes  # noqa: PLC0415
        assert probes.main(["--list-probes"]) == 0
        out = capsys.readouterr().out
        assert all(name in out for name in R0_PROBES)
        present = probes.language_codes_in_service_model(probes.LANGUAGE_CODES, "us-east-1")
        assert "auto" not in present and len(present) == 12
        assert all(present.values()), [c for c, ok in present.items() if not ok]


# --- end of fixture-and-probe tests ---
