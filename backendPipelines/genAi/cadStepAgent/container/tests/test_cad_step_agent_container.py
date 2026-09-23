# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Container behaviour for the GenAI CAD STEP agent, with Strands, CadQuery, S3 and Step Functions
replaced at their boundaries."""

import json
import os
import resource
import subprocess  # nosec B404 - the test spawns the interpreter by absolute path with a fixed argv
import sys
import time
import types
from unittest.mock import MagicMock, patch

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)


@pytest.fixture(autouse=True)
def _fake_strands(monkeypatch):
    """A `strands` module whose @tool decorator is the identity, so tools.build_tools imports."""
    fake = types.ModuleType("strands")
    fake.tool = lambda fn: fn
    fake.Agent = MagicMock(name="Agent")
    monkeypatch.setitem(sys.modules, "strands", fake)
    yield fake


from cad_step_agent import agent as agent_module  # noqa: E402
from cad_step_agent import report, run, sandbox, tools  # noqa: E402
from cad_step_agent import cad_io  # noqa: E402


# ---------------------------------------------------------------------------------------------------
# sandbox
# ---------------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestSandbox:
    def test_environment_is_an_allow_list_with_no_credential_shaped_variables(self):
        source = {
            "PATH": "/usr/bin", "LANG": "C.UTF-8",
            "AWS_ACCESS_KEY_ID": "AKIA", "AWS_SECRET_ACCESS_KEY": "s", "AWS_SESSION_TOKEN": "t",
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/creds",
            "TASK_TOKEN": "tok", "OPENAI_API_KEY": "sk", "BEDROCK_MODEL_ID": "m", "HOME": "/root",
        }
        env = sandbox.scrubbed_environment(source, extra={"CAD_OUTPUT_STEP": "/w/out.step"})
        assert env["PATH"] == "/usr/bin" and env["LANG"] == "C.UTF-8"
        assert env["CAD_OUTPUT_STEP"] == "/w/out.step"
        assert env["PYTHONSAFEPATH"] == "1"
        assert not any(k.startswith("AWS_") for k in env)
        for forbidden in ("TASK_TOKEN", "OPENAI_API_KEY", "BEDROCK_MODEL_ID", "HOME"):
            assert forbidden not in env

    def test_a_credential_shaped_extra_is_refused(self):
        with pytest.raises(ValueError):
            sandbox.scrubbed_environment({}, extra={"MY_SECRET_KEY": "x"})

    def test_bounded_tail(self):
        lines = [f"line {i} " + "x" * 5000 for i in range(200)]
        tail = sandbox.bounded_tail(lines, max_lines=3, max_chars=20).splitlines()
        assert len(tail) == 3 and all(len(l) == 20 for l in tail)
        assert tail[-1].startswith("line 199")

    def test_run_script_writes_output_and_reports_success(self, tmp_path):
        code = "import os\nopen(os.environ['CAD_OUTPUT_STEP'], 'w').write('ISO-10303-21;')\nprint('done')\n"
        result = sandbox.run_script(code, str(tmp_path / "a1"), timeout_seconds=30)
        assert result.succeeded and result.returncode == 0 and not result.timed_out
        assert result.output_exists and "done" in result.output_tail

    def test_run_script_without_output_is_not_a_success(self, tmp_path):
        result = sandbox.run_script("print('no file')\n", str(tmp_path / "a2"), timeout_seconds=30)
        assert result.returncode == 0 and not result.output_exists and not result.succeeded

    def test_run_script_cannot_see_the_parent_credentials(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak-me")
        monkeypatch.setenv("TASK_TOKEN", "leak-me-too")
        code = ("import os,json\nprint(json.dumps({k: v for k, v in os.environ.items() if 'leak' in v}))\n"
                "open(os.environ['CAD_OUTPUT_STEP'],'w').write('x')\n")
        result = sandbox.run_script(code, str(tmp_path / "a3"), timeout_seconds=30)
        assert json.loads(result.output_tail.splitlines()[0]) == {}

    def test_run_script_times_out(self, tmp_path):
        result = sandbox.run_script("import time\ntime.sleep(30)\n", str(tmp_path / "a4"), timeout_seconds=1)
        assert result.timed_out and not result.succeeded
        assert "terminated" in result.output_tail

    def test_a_timed_out_script_leaves_no_surviving_grandchild(self, tmp_path):
        # The script forks a sleeper and then sleeps itself; the timeout must take the whole group.
        code = ("import subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "print(child.pid, flush=True)\n"
                "time.sleep(60)\n")
        result = sandbox.run_script(code, str(tmp_path / "a5"), timeout_seconds=1)
        assert result.timed_out
        grandchild = int(result.output_tail.splitlines()[0])
        for _ in range(50):
            if not _alive(grandchild):
                break
            time.sleep(0.05)
        assert not _alive(grandchild), f"grandchild {grandchild} survived the timeout"

    def test_the_script_runs_under_the_resource_limits(self, tmp_path):
        code = ("import resource, json, os\n"
                "print(json.dumps({'as': resource.getrlimit(resource.RLIMIT_AS)[0],"
                " 'fsize': resource.getrlimit(resource.RLIMIT_FSIZE)[0],"
                " 'nproc': resource.getrlimit(resource.RLIMIT_NPROC)[0]}))\n"
                "open(os.environ['CAD_OUTPUT_STEP'],'w').write('x')\n")
        result = sandbox.run_script(code, str(tmp_path / "a6"), timeout_seconds=30)
        assert result.succeeded, result.output_tail
        limits = json.loads(result.output_tail.splitlines()[0])
        assert limits["as"] <= sandbox.SCRIPT_MAX_ADDRESS_SPACE_BYTES
        assert limits["fsize"] <= sandbox.SCRIPT_MAX_FILE_BYTES
        assert limits["nproc"] != resource.RLIM_INFINITY

    def test_an_oversized_output_file_is_cut_off_by_the_file_size_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sandbox, "SCRIPT_MAX_FILE_BYTES", 4096)
        code = ("import os\n"
                "with open(os.environ['CAD_OUTPUT_STEP'], 'wb') as fh:\n"
                "    fh.write(b'x' * 1_000_000)\n")
        result = sandbox.run_script(code, str(tmp_path / "a7"), timeout_seconds=30)
        assert not result.succeeded
        assert os.path.getsize(result.output_path) <= 4096

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc and prctl are Linux")
    def test_a_script_cannot_read_the_hardened_agent_environment(self, tmp_path):
        """The regression test for the /proc/<ppid>/environ leak.

        Run in a subprocess so hardening the "agent" does not change this test process. The agent
        stand-in carries the credential marker, records that a script COULD read it while dumpable (the
        positive control that the marker is there and the channel exists), hardens itself, and records
        that the same script then cannot.
        """
        probe = (
            "import json, os, sys\n"
            f"sys.path.insert(0, {json.dumps(_CONTAINER_DIR)})\n"
            "import types\n"
            "fake = types.ModuleType('strands'); fake.tool = lambda fn: fn\n"
            "sys.modules['strands'] = fake\n"
            "from cad_step_agent import sandbox\n"
            "assert os.environ['AWS_CONTAINER_CREDENTIALS_RELATIVE_URI'] == '/v2/credentials/TEST-MARKER'\n"
            "script = ('import os\\n'\n"
            "          'try:\\n'\n"
            "          '    data = open(f\"/proc/{os.getppid()}/environ\", \"rb\").read()\\n'\n"
            "          '    print(\"READ\", b\"TEST-MARKER\" in data)\\n'\n"
            "          'except PermissionError:\\n'\n"
            "          '    print(\"DENIED\")\\n'\n"
            "          'open(os.environ[\"CAD_OUTPUT_STEP\"], \"w\").write(\"x\")\\n')\n"
            f"before = sandbox.run_script(script, {json.dumps(str(tmp_path / 'before'))}, timeout_seconds=30)\n"
            "hardened = sandbox.harden_agent_process()\n"
            f"after = sandbox.run_script(script, {json.dumps(str(tmp_path / 'after'))}, timeout_seconds=30)\n"
            "print(json.dumps({'hardened': hardened, 'before': before.output_tail.splitlines()[0],"
            " 'after': after.output_tail.splitlines()[0]}))\n"
        )
        env = dict(os.environ)
        env["AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"] = "/v2/credentials/TEST-MARKER"
        completed = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True,
                                   timeout=120, check=False)
        assert completed.returncode == 0, completed.stderr
        report = json.loads(completed.stdout.strip().splitlines()[-1])
        assert report["hardened"] is True
        # Positive control: while the agent stand-in was dumpable, the script read the marker.
        assert report["before"] == "READ True", report
        # The fix: once non-dumpable, the same read is refused.
        assert report["after"] == "DENIED", report


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("State:"):
                    return not line.split()[1].startswith("Z")
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------------------------------
def _outcome(**overrides):
    base = dict(status=report.STATUS_PARTIAL, prompt="Add holes", mode="modify", output_file_name="part.stp",
                model="bedrock:model-x", run_id="abc123", attempts=[report.AttemptRecord(1, True, "ok", "tail")],
                summary="s" * 5000, unresolved=["could not find a datasheet"], sources=["https://example.test/a"],
                geometry={"solid_count": 1}, wall_seconds=12.0, research_allowed=True)
    base.update(overrides)
    return report.RunOutcome(**base)


@pytest.mark.unit
class TestReport:
    def test_metadata_document_is_bounded_and_carries_every_key(self):
        doc = report.metadata_document(_outcome())
        assert doc["type"] == "metadata" and doc["updateType"] == "update"
        keys = {e["metadataKey"]: e["metadataValue"] for e in doc["metadata"]}
        assert keys["cadAgentStatus"] == "partial"
        assert len(keys["cadAgentSummary"]) <= report.SUMMARY_MAX_CHARS
        assert json.loads(keys["cadAgentUnresolved"]) == ["could not find a datasheet"]
        assert json.loads(keys["cadAgentSources"]) == ["https://example.test/a"]
        assert keys["cadAgentAttempts"] == "1"
        assert json.loads(keys["cadAgentGeometry"]) == {"solid_count": 1}

    def test_unresolved_list_is_bounded(self):
        doc = report.metadata_document(_outcome(unresolved=["u" * 300] * 40))
        value = next(e["metadataValue"] for e in doc["metadata"] if e["metadataKey"] == "cadAgentUnresolved")
        assert len(value) <= report.LIST_MAX_CHARS and len(json.loads(value)) < 40

    def test_success_payload_is_the_outcome_not_the_output(self):
        payload = report.success_payload(_outcome(attempts=[report.AttemptRecord(1, True, "ok", "STDOUT" * 500)]))
        assert set(payload) == {"status", "outputFile", "attempts", "unresolvedCount", "summary"}
        assert "STDOUT" not in json.dumps(payload)
        assert len(payload["summary"]) <= report.TOKEN_SUMMARY_MAX_CHARS

    def test_markdown_report_lists_unresolved_and_sources(self):
        text = report.markdown_report(_outcome())
        assert "## Not completed" in text and "could not find a datasheet" in text
        assert "https://example.test/a" in text and "Attempt 1 - ok" in text

    def test_failure_cause_is_bounded(self):
        assert len(report.failure_cause("x" * 1000)) == report.CAUSE_MAX_CHARS


# ---------------------------------------------------------------------------------------------------
# agent (model resolution)
# ---------------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestModelResolution:
    def test_bedrock_is_the_default_and_uses_the_deployment_model(self):
        provider, model_id, key = agent_module.resolve_model("", env={"BEDROCK_MODEL_ID": "global.model"})
        assert (provider, model_id, key) == ("bedrock", "global.model", None)

    def test_per_run_model_id_overrides_the_deployment_default(self):
        assert agent_module.resolve_model("bedrock", "other.model", env={"BEDROCK_MODEL_ID": "global.model"})[1] == "other.model"

    def test_openai_requires_a_configured_secret(self):
        with pytest.raises(agent_module.ModelConfigurationError):
            agent_module.resolve_model("openai", env={"OPENAI_MODEL_ID": "gpt-x"})

    def test_openai_reads_a_json_shaped_secret(self):
        secrets = MagicMock()
        secrets.get_secret_value.return_value = {"SecretString": json.dumps({"apiKey": "sk-test"})}
        provider, model_id, key = agent_module.resolve_model(
            "openai", env={"OPENAI_MODEL_ID": "gpt-x", "OPENAI_API_KEY_SECRET_ARN": "arn:aws:secretsmanager:r:1:secret:s"},
            secrets_client=secrets)
        assert (provider, model_id, key) == ("openai", "gpt-x", "sk-test")
        secrets.get_secret_value.assert_called_once_with(SecretId="arn:aws:secretsmanager:r:1:secret:s")

    def test_openai_reads_a_plain_secret(self):
        secrets = MagicMock()
        secrets.get_secret_value.return_value = {"SecretString": "sk-plain\n"}
        assert agent_module.read_openai_api_key("arn", secrets) == "sk-plain"

    def test_unknown_provider_is_refused(self):
        with pytest.raises(agent_module.ModelConfigurationError):
            agent_module.resolve_model("anthropic-direct", env={})

    def test_the_system_prompt_names_the_sandbox_contract(self):
        for token in ("CAD_INPUT_STEP", "CAD_OUTPUT_STEP", "finish", "partial", "no network"):
            assert token in agent_module.SYSTEM_PROMPT


# ---------------------------------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------------------------------
def _state(tmp_path, research=True, attempts=3, deadline_offset=600):
    import time
    return tools.RunState(work_root=str(tmp_path), input_step=None, output_name="out.step", max_attempts=attempts,
                          script_timeout_seconds=30, deadline_epoch=time.time() + deadline_offset,
                          research_allowed=research)


def _tool_map(bound):
    return {fn.__name__: fn for fn in bound}


@pytest.mark.unit
class TestTools:
    def test_research_tools_are_registered_only_when_allowed(self, tmp_path):
        with_research = _tool_map(tools.build_tools(_state(tmp_path, research=True), search_fn=lambda q, n: [], fetch_fn=lambda u: ""))
        without = _tool_map(tools.build_tools(_state(tmp_path, research=False)))
        assert {"web_search", "fetch_url"} <= set(with_research)
        assert not {"web_search", "fetch_url"} & set(without)
        assert {"inspect_input_step", "run_cad_script", "finish"} <= set(without)

    def test_run_cad_script_records_attempts_and_the_best_output(self, tmp_path):
        state = _state(tmp_path, research=False)
        fns = _tool_map(tools.build_tools(state))
        valid = cad_io.StepSummary(valid=True, solid_count=1, face_count=6, edge_count=12,
                                   bounding_box_mm=[0, 0, 0, 10, 10, 10], volume_mm3=1000.0)
        with patch.object(cad_io, "inspect_step", return_value=valid):
            out = json.loads(fns["run_cad_script"](
                "import os\nopen(os.environ['CAD_OUTPUT_STEP'],'w').write('ISO')\n", "make a cube"))
        assert out["ok"] is True and out["attempt"] == 1 and out["attemptsLeft"] == 2
        assert state.best_output and os.path.isfile(state.best_output)
        assert state.best_geometry["solid_count"] == 1

    def test_run_cad_script_without_geometry_is_not_ok(self, tmp_path):
        state = _state(tmp_path, research=False)
        fns = _tool_map(tools.build_tools(state))
        out = json.loads(fns["run_cad_script"]("print('nothing')\n", "noop"))
        assert out["ok"] is False and "no output file" in out["geometry"]["error"]
        assert state.best_output is None and len(state.attempts) == 1

    def test_attempt_budget_is_enforced(self, tmp_path):
        state = _state(tmp_path, research=False, attempts=1)
        fns = _tool_map(tools.build_tools(state))
        fns["run_cad_script"]("print(1)\n", "first")
        out = json.loads(fns["run_cad_script"]("print(2)\n", "second"))
        assert out["ok"] is False and "attempt budget" in out["error"]
        assert len(state.attempts) == 1

    def test_time_budget_is_enforced(self, tmp_path):
        state = _state(tmp_path, research=False, deadline_offset=-1)
        fns = _tool_map(tools.build_tools(state))
        out = json.loads(fns["run_cad_script"]("print(1)\n", "late"))
        assert out["ok"] is False and "time budget" in out["error"]

    def test_finish_records_the_outcome(self, tmp_path):
        state = _state(tmp_path, research=False)
        fns = _tool_map(tools.build_tools(state))
        assert fns["finish"]("built it", ["no datasheet found", " "], "Partial") == "recorded"
        assert state.finished and state.final_unresolved == ["no datasheet found"]
        assert state.final_status_hint == "partial"

    def test_fetch_url_records_sources_and_strips_html(self, tmp_path):
        state = _state(tmp_path)
        fns = _tool_map(tools.build_tools(
            state, search_fn=lambda q, n: [{"title": "T", "href": "https://x.test", "body": "B"}],
            fetch_fn=lambda u: tools.strip_html("<html><script>x()</script><p>Board is 69.6 &times; 45 mm</p></html>")))
        search = json.loads(fns["web_search"]("jetson nano dimensions"))
        assert search["ok"] and search["results"][0]["url"] == "https://x.test"
        page = json.loads(fns["fetch_url"]("https://x.test/spec"))
        assert page["ok"] and "69.6" in page["text"] and "x()" not in page["text"]
        assert state.sources == ["https://x.test/spec"]
        assert json.loads(fns["fetch_url"]("ftp://nope"))["ok"] is False

    def test_search_failures_are_reported_not_raised(self, tmp_path):
        def boom(q, n):
            raise RuntimeError("offline")
        fns = _tool_map(tools.build_tools(_state(tmp_path), search_fn=boom, fetch_fn=lambda u: ""))
        assert json.loads(fns["web_search"]("q"))["ok"] is False


# ---------------------------------------------------------------------------------------------------
# run_job
# ---------------------------------------------------------------------------------------------------
def _definition(mode="modify", **agent_overrides):
    agent_cfg = {"prompt": "Add four M3 holes", "allowInternetResearch": False, "modelProvider": "bedrock",
                 "modelId": "", "maxAttempts": 3, "maxRunSeconds": 600, "scriptTimeoutSeconds": 30}
    agent_cfg.update(agent_overrides)
    return {
        "jobName": "CadStepAgent_x",
        "mode": mode,
        "inputFile": {"bucketName": "abkt", "objectKey": "xasset1/sub/part.stp", "fileExtension": ".stp"} if mode == "modify" else None,
        "outputFiles": {"bucketName": "abkt", "objectDir": "xasset1/", "relativeSubdir": "sub/" if mode == "modify" else "",
                        "fileName": "part.stp" if mode == "modify" else "board-20260922-185500.step"},
        "outputMetadata": {"bucketName": "abkt", "objectDir": "xasset1/metadata/"},
        "auxiliary": {"bucketName": "aux", "objectDir": "xasset1/pipeline/"},
        "assetId": "xasset1", "databaseId": "db1",
        "agent": agent_cfg,
    }


class _FakeS3:
    def __init__(self):
        self.objects = {}

    def download_file(self, bucket, key, local):
        with open(local, "w") as fh:
            fh.write("ISO-10303-21;")

    def upload_file(self, local, bucket, key):
        with open(local, "rb") as fh:
            self.objects[(bucket, key)] = fh.read()

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[(Bucket, Key)] = Body


def _agent_that(script_writes_output=True, finish_args=None, raise_after=False):
    """An agent_factory whose agent drives the bound tools deterministically."""
    def factory(model, bound_tools):
        fns = {fn.__name__: fn for fn in bound_tools}

        def agent(instruction):
            fns["inspect_input_step"]()
            code = "import os\nopen(os.environ['CAD_OUTPUT_STEP'],'w').write('ISO')\n" if script_writes_output else "print('x')\n"
            fns["run_cad_script"](code, "attempt")
            if finish_args is not None:
                fns["finish"](*finish_args)
            if raise_after:
                raise RuntimeError("model went away")
            return "done"
        return agent
    return factory


VALID = cad_io.StepSummary(valid=True, solid_count=1, face_count=6, edge_count=12,
                           bounding_box_mm=[0, 0, 0, 10, 10, 10], volume_mm3=1000.0)


@pytest.mark.unit
class TestRunJob:
    def test_successful_modify_run_uploads_step_report_metadata_and_reports_success(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_MODEL_ID", "global.model")
        s3, sfn = _FakeS3(), MagicMock()
        with patch.object(cad_io, "inspect_step", return_value=VALID):
            payload = run.run_job(json.dumps(_definition()), "inner-token", s3=s3, sfn=sfn,
                                  agent_factory=_agent_that(finish_args=("Added holes", [], "succeeded")))
        assert payload["status"] == "succeeded" and payload["outputKey"] == "xasset1/sub/part.stp"
        assert ("abkt", "xasset1/sub/part.stp") in s3.objects
        assert ("abkt", "xasset1/sub/part.stp.cad-agent-report.md") in s3.objects
        meta = json.loads(s3.objects[("abkt", "xasset1/metadata/sub/part.stp.metadata.json")])
        assert {e["metadataKey"] for e in meta["metadata"]} >= {"cadAgentStatus", "cadAgentSummary", "cadAgentGeometry"}
        sfn.send_task_success.assert_called_once()
        assert json.loads(sfn.send_task_success.call_args.kwargs["output"])["status"] == "succeeded"
        sfn.send_task_failure.assert_not_called()

    def test_unresolved_items_make_the_run_partial_but_still_a_success_token(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_MODEL_ID", "global.model")
        s3, sfn = _FakeS3(), MagicMock()
        with patch.object(cad_io, "inspect_step", return_value=VALID):
            payload = run.run_job(_definition("generate"), "inner-token", s3=s3, sfn=sfn,
                                  agent_factory=_agent_that(finish_args=("Built board", ["could not find the connector datasheet"], "partial")))
        assert payload["status"] == "partial" and payload["unresolvedCount"] == 1
        assert ("abkt", "xasset1/board-20260922-185500.step") in s3.objects
        sfn.send_task_success.assert_called_once()

    def test_no_valid_step_fails_the_token_with_a_bounded_cause(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_MODEL_ID", "global.model")
        s3, sfn = _FakeS3(), MagicMock()
        with pytest.raises(run.RunFailed):
            run.run_job(_definition(), "inner-token", s3=s3, sfn=sfn, agent_factory=_agent_that(script_writes_output=False))
        assert not s3.objects
        kwargs = sfn.send_task_failure.call_args.kwargs
        assert kwargs["taskToken"] == "inner-token" and kwargs["error"] == run.FAILURE_ERROR_CODE
        assert len(kwargs["cause"]) <= 256

    def test_agent_crash_after_a_valid_output_is_a_partial_success(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_MODEL_ID", "global.model")
        s3, sfn = _FakeS3(), MagicMock()
        with patch.object(cad_io, "inspect_step", return_value=VALID):
            payload = run.run_job(_definition(), "inner-token", s3=s3, sfn=sfn, agent_factory=_agent_that(raise_after=True))
        assert payload["status"] == "partial"
        sfn.send_task_success.assert_called_once()

    def test_missing_bedrock_model_fails_before_the_agent_runs(self, monkeypatch):
        monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
        sfn = MagicMock()
        with pytest.raises(agent_module.ModelConfigurationError):
            run.run_job(_definition(), "inner-token", s3=_FakeS3(), sfn=sfn, agent_factory=_agent_that())
        sfn.send_task_failure.assert_called_once()

    def test_a_definition_with_the_wrong_extension_is_refused(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_MODEL_ID", "global.model")
        definition = _definition()
        definition["outputFiles"]["fileName"] = "part.glb"
        sfn = MagicMock()
        with pytest.raises(run.RunFailed):
            run.run_job(definition, "inner-token", s3=_FakeS3(), sfn=sfn, agent_factory=_agent_that())
        sfn.send_task_failure.assert_called_once()

    def test_a_hand_edited_output_name_is_refused_even_with_the_right_extension(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_MODEL_ID", "global.model")
        definition = _definition()
        definition["outputFiles"]["fileName"] = "../part.stp"
        sfn = MagicMock()
        with pytest.raises(run.RunFailed):
            run.run_job(definition, "inner-token", s3=_FakeS3(), sfn=sfn, agent_factory=_agent_that())
        sfn.send_task_failure.assert_called_once()

    def test_the_token_is_reported_exactly_once_when_the_watchdog_fires_first(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_MODEL_ID", "global.model")
        s3, sfn = _FakeS3(), MagicMock()

        def slow_factory(model, bound_tools):
            fns = {fn.__name__: fn for fn in bound_tools}

            def agent(instruction):
                import time
                time.sleep(1.5)
                fns["run_cad_script"]("import os\nopen(os.environ['CAD_OUTPUT_STEP'],'w').write('ISO')\n", "late")
                return "done"
            return agent

        with patch.object(cad_io, "inspect_step", return_value=VALID), pytest.raises(Exception):
            run.run_job(_definition(maxRunSeconds=1), "inner-token", s3=s3, sfn=sfn, agent_factory=slow_factory)
        assert sfn.send_task_failure.call_count == 1
        sfn.send_task_success.assert_not_called()

    def test_parse_definition_accepts_every_transport_shape(self):
        d = _definition()
        assert run.parse_definition(d) == d
        assert run.parse_definition(json.dumps(d)) == d
        assert run.parse_definition([json.dumps(d)]) == d
        with pytest.raises(run.RunFailed):
            run.parse_definition({"mode": "modify"})


# ---------------------------------------------------------------------------------------------------
# batch_main
# ---------------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestBatchMain:
    def test_the_definition_and_token_come_from_the_environment_after_hardening(self):
        from cad_step_agent import batch_main
        calls = []
        with patch.object(batch_main.sandbox, "harden_agent_process", lambda: calls.append("harden") or True), \
                patch.object(batch_main.run, "run_job", lambda d, t: calls.append(("run", d, t))):
            rc = batch_main.main({"CAD_AGENT_DEFINITION": '{"mode": "modify"}', "TASK_TOKEN": "inner"})
        assert rc == 0
        assert calls == ["harden", ("run", '{"mode": "modify"}', "inner")]

    def test_a_missing_definition_is_a_usage_error_without_a_run(self):
        from cad_step_agent import batch_main
        with patch.object(batch_main.sandbox, "harden_agent_process", lambda: True), \
                patch.object(batch_main.run, "run_job", MagicMock()) as run_job:
            assert batch_main.main({}) == 2
        run_job.assert_not_called()

    def test_a_failed_run_is_exit_code_one(self):
        from cad_step_agent import batch_main
        with patch.object(batch_main.sandbox, "harden_agent_process", lambda: True), \
                patch.object(batch_main.run, "run_job", MagicMock(side_effect=run.RunFailed("no step"))):
            assert batch_main.main({"CAD_AGENT_DEFINITION": "{}"}) == 1
