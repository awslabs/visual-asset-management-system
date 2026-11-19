# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Blender agent stage: what it hands the agent, what confines the agent, and what counts as done.

None of this can be exercised end to end without Blender, Codex and a Bedrock model, so the tests pin the
parts that decide whether a live run is safe and legible before it costs an hour of GPU and model time:

- the sandbox configuration Codex is given, because a wrong key there widens what the agent may write to;
- the input contract -- which models take several views, a starting mesh, or no image -- because every
  violation of it produces a plausible mesh from the wrong input rather than an error;
- the workspace layout and brief the agent reads, because the instructions name them;
- completion as a file, because the final message is not evidence.
"""

import asyncio
import json
import os
import re
from types import SimpleNamespace

import pytest

from models import blenderagent
from models.blenderagent import BlenderAgentAdapter, codex_config, litellm_config, task_prompt
from models.hunyuan3d import Hunyuan3dAdapter
from models.sam3d import Sam3dAdapter
from models.stable3dgen import Stable3DGenAdapter
from models.step1x3d import Step1X3dAdapter
from models.trellis2 import Trellis2Adapter
from models.triposr import TripoSRAdapter

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS_DIR = os.path.join(_CONTAINER_DIR, "blenderagent_tools")
_RECONSTRUCTION_ADAPTERS = (Sam3dAdapter, Stable3DGenAdapter, Step1X3dAdapter, Trellis2Adapter,
                            TripoSRAdapter, Hunyuan3dAdapter)


@pytest.mark.unit
class TestTheSandboxIsClosedByConfiguration:
    """The agent's confinement is a handful of TOML keys. Each one here is load-bearing."""

    def test_writes_are_confined_to_the_workspace_and_nowhere_else(self):
        config = codex_config("http://127.0.0.1:4000/v1", "KEY", "high")
        assert 'sandbox_mode = "workspace-write"' in config
        # The two default carve-outs are what would let the agent reach the pipeline's own /tmp dirs.
        assert "exclude_slash_tmp = true" in config
        assert "exclude_tmpdir_env_var = true" in config
        assert "network_access = false" in config

    def test_no_approval_prompt_can_ever_block_a_headless_run(self):
        assert 'approval_policy = "never"' in codex_config("http://x/v1", "KEY", "high")

    def test_credentials_are_kept_out_of_the_commands_the_agent_runs(self):
        config = codex_config("http://x/v1", "BRIDGE", "high")
        # Up to the next section header, not the next bracket: the exclude list itself opens with one.
        policy = config.split("[shell_environment_policy]", 1)[1].split("\n[", 1)[0]
        for pattern in ('"AWS_*"', '"*TOKEN*"', '"*SECRET*"', '"*KEY*"'):
            assert pattern in policy, f"{pattern} is not excluded from the agent's command environment"

    def test_the_model_provider_is_the_bridge_over_the_responses_api(self):
        config = codex_config("http://127.0.0.1:4321/v1", "BRIDGE_KEY", "high")
        assert 'base_url = "http://127.0.0.1:4321/v1"' in config
        assert 'env_key = "BRIDGE_KEY"' in config
        # Codex speaks only the Responses API; a chat wire would be rejected by this Codex release.
        assert 'wire_api = "responses"' in config
        assert 'model_provider = "bedrock_bridge"' in config

    def test_nothing_persists_and_nothing_searches_the_web(self):
        config = codex_config("http://x/v1", "KEY", "high")
        assert 'persistence = "none"' in config
        assert 'web_search = "disabled"' in config
        assert "view_image = true" in config, "the agent must be able to look at its own renders"

    def test_the_agent_root_is_not_under_tmp(self):
        """The whole point of excluding /tmp from the sandbox is defeated if the workspace lives there."""
        assert not str(blenderagent.AGENT_ROOT).replace("\\", "/").startswith("/tmp")


@pytest.mark.unit
class TestTheBridgeRoutesToBedrockOnTheTaskRole:
    def test_the_model_is_forced_onto_the_converse_route(self):
        config = litellm_config("us.openai.gpt-5.6-terra", "us-west-2")
        params = config["model_list"][0]["litellm_params"]
        assert params["model"] == "bedrock/converse/us.openai.gpt-5.6-terra"
        assert params["aws_region_name"] == "us-west-2"

    def test_no_credential_is_written_into_the_bridge_configuration(self):
        config = json.dumps(litellm_config("m", "r"))
        for forbidden in ("aws_access_key_id", "aws_secret_access_key", "api_key", "AWS_BEARER"):
            assert forbidden not in config

    def test_unsupported_request_fields_are_dropped_rather_than_fatal(self):
        assert litellm_config("m", "r")["litellm_settings"]["drop_params"] is True

    def test_a_stalled_call_is_given_up_within_minutes_on_both_sides(self):
        """A stream Bedrock never finishes is ended by the bridge and by Codex at the same moment, so a stall
        costs the run minutes rather than its budget."""
        timeout = litellm_config("m", "r")["litellm_settings"]["request_timeout"]
        assert 120 <= timeout <= 180
        toml = codex_config("http://127.0.0.1:1", "BRIDGE_KEY", "medium")
        assert f"stream_idle_timeout_ms = {timeout * 1000}" in toml

    def test_the_model_id_comes_from_the_deployment_and_never_a_guess(self, monkeypatch):
        monkeypatch.delenv("BLENDERAGENT_BEDROCK_MODEL_ID", raising=False)
        with pytest.raises(ValueError, match="No Bedrock model configured"):
            blenderagent.resolve_model_id()
        monkeypatch.setenv("BLENDERAGENT_BEDROCK_MODEL_ID", "global.openai.gpt-6-astra")
        assert blenderagent.resolve_model_id() == "global.openai.gpt-6-astra"

    def test_a_run_cannot_choose_the_model_or_widen_the_sandbox(self, monkeypatch):
        """The job role is granted the configured model and no other, and workspace-write is what confines
        the agent's commands; neither is a run setting, so a template naming either changes nothing."""
        monkeypatch.setenv("BLENDERAGENT_BEDROCK_MODEL_ID", "global.openai.gpt-6-astra")
        model_id, reasoning = blenderagent.validate_agent_settings(
            {"BLENDERAGENT_MODEL_ID": "us.other.model", "BLENDERAGENT_SANDBOX": "danger-full-access"})
        assert model_id == "global.openai.gpt-6-astra"
        assert reasoning == "high"
        assert blenderagent.SANDBOX_MODE == "workspace-write"
        assert 'sandbox_mode = "workspace-write"' in codex_config("http://x/v1", "KEY", "high")

    def test_image_editing_follows_the_deployment_switch(self, monkeypatch):
        monkeypatch.delenv("BLENDERAGENT_IMAGE_EDITING", raising=False)
        assert blenderagent.image_editing_enabled() is False
        monkeypatch.setenv("BLENDERAGENT_IMAGE_EDITING", "true")
        assert blenderagent.image_editing_enabled() is True
        monkeypatch.setenv("BLENDERAGENT_IMAGE_EDITING", "false")
        assert blenderagent.image_editing_enabled() is False


@pytest.mark.unit
class TestTheInputContract:
    def test_the_agent_declares_every_input_it_can_take(self):
        assert BlenderAgentAdapter.ACCEPTS_MULTIPLE_VIEWS is True
        assert BlenderAgentAdapter.ACCEPTS_STARTING_MESH is True
        assert BlenderAgentAdapter.REQUIRES_REFERENCE_IMAGE is False

    @pytest.mark.parametrize("adapter", _RECONSTRUCTION_ADAPTERS, ids=[a.KEY for a in _RECONSTRUCTION_ADAPTERS])
    def test_no_reconstruction_model_claims_it_can_refine_a_mesh(self, adapter):
        assert adapter.ACCEPTS_STARTING_MESH is False
        assert adapter.REQUIRES_REFERENCE_IMAGE is True

    @pytest.mark.parametrize("adapter", _RECONSTRUCTION_ADAPTERS + (BlenderAgentAdapter,),
                             ids=[a.KEY for a in _RECONSTRUCTION_ADAPTERS + (BlenderAgentAdapter,)])
    def test_every_adapter_takes_the_full_keyword_contract(self, adapter):
        import inspect

        parameters = inspect.signature(adapter.generate).parameters
        for name in ("extra_images", "source_images", "starting_mesh"):
            assert name in parameters, f"{adapter.KEY}.generate lacks {name}"

    def test_a_mesh_handed_to_a_reconstruction_model_is_refused_in_dispatch(self):
        import generate as generate_module

        with pytest.raises(ValueError, match="cannot start from an existing mesh"):
            generate_module.generate("/tmp/a.png", "/tmp/work", {"MODEL": "sam3d"},
                                     starting_mesh="/tmp/prev.glb")

    def test_a_run_with_no_image_is_refused_for_a_model_that_needs_one(self):
        import generate as generate_module

        with pytest.raises(ValueError, match="needs a reference image"):
            generate_module.generate(None, "/tmp/work", {"MODEL": "stable3dgen"})

    def test_a_run_with_neither_image_nor_mesh_is_refused_even_for_the_agent(self):
        import generate as generate_module

        with pytest.raises(ValueError, match="needs a reference image"):
            generate_module.generate(None, "/tmp/work", {"MODEL": "blenderagent"})


@pytest.mark.unit
class TestWhatTheAgentIsHanded:
    def test_the_instructions_and_every_tool_they_name_exist(self):
        instructions = open(os.path.join(_TOOLS_DIR, "AGENTS.md"), encoding="utf-8").read()
        for tool in ("bl_import.py", "bl_render.py", "bl_inspect.py", "bl_bake.py", "bl_solidify.py",
                     "bl_export.py", "bl_silhouette_base.py", "bl_reshape.py", "bl_project_photo.py",
                     "bl_uv_mask.py", "agent_image_edit.py", "agent_gate.py", "bl_refit.py"):
            assert tool in instructions, f"AGENTS.md never mentions {tool}"
            assert os.path.isfile(os.path.join(_TOOLS_DIR, tool)), f"{tool} is missing from the image"
        assert os.path.isfile(os.path.join(_TOOLS_DIR, "bl_common.py"))

    def test_the_instructions_demand_one_body_not_several_closed_parts(self):
        """The first agent run built a figure from 34 closed parts because the instructions allowed
        'several closed objects'; the print gate read them as 33 detached pieces and failed a model that
        looked right. The instructions now make solidifying into one body the mandatory last step."""
        instructions = open(os.path.join(_TOOLS_DIR, "AGENTS.md"), encoding="utf-8").read()
        assert "several closed objects are acceptable" not in instructions
        assert "bl_solidify.py" in instructions
        assert "exactly one mesh" in instructions.lower() or "exactly ONE mesh" in instructions

    def test_the_instructions_make_completion_a_set_of_files(self):
        instructions = open(os.path.join(_TOOLS_DIR, "AGENTS.md"), encoding="utf-8").read()
        assert "output/model.glb" in instructions
        assert "output/DONE.json" in instructions
        # The rule the whole design rests on, stated to the agent in so many words.
        assert "A message claiming\ncompletion without the files is not" in instructions or \
            "claiming completion without the files is not" in instructions.replace("\n", " ")

    def test_the_instructions_fit_the_document_budget_codex_is_given(self):
        size = os.path.getsize(os.path.join(_TOOLS_DIR, "AGENTS.md"))
        budget = int(re.search(r"project_doc_max_bytes = (\d+)",
                               codex_config("http://x/v1", "K", "high")).group(1))
        assert size < budget, f"AGENTS.md is {size} bytes, over the {budget}-byte budget Codex reads"

    def test_the_tools_refuse_to_write_outside_the_workspace(self):
        """The sandbox is the enforcement; the tools carry the same rule so a misconfigured sandbox is
        not the only thing standing between the agent and the pipeline's directories."""
        for tool in ("bl_render.py", "bl_export.py", "bl_bake.py", "bl_solidify.py", "bl_import.py",
                     "bl_silhouette_base.py", "bl_reshape.py", "bl_project_photo.py", "bl_uv_mask.py", "bl_refit.py",
                     "bl_view_fill.py"):
            source = open(os.path.join(_TOOLS_DIR, tool), encoding="utf-8").read()
            assert "require_inside_workspace" in source, f"{tool} never checks its output path"

    def test_a_suffixed_output_is_checked_as_the_path_written_not_as_its_prefix(self):
        """A tool given an output prefix writes ``<prefix>.png``, ``<prefix>_report.json`` and so on. Checking
        the prefix alone passes a prefix that names the workspace itself, whose suffixed files land beside
        the workspace, outside it; so each written path is checked, after its suffix is added."""
        expected = {
            "bl_render.py": ('require_inside_workspace(f"{args.out}_{name}.png"',
                             'require_inside_workspace(f"{args.out}_report.json"'),
            "bl_view_fill.py": ('f"{args.out}.png", f"{args.out}_mask.png", f"{args.out}.json"',),
        }
        for tool, fragments in expected.items():
            source = open(os.path.join(_TOOLS_DIR, tool), encoding="utf-8").read()
            for fragment in fragments:
                assert fragment in source, f"{tool} does not check {fragment!r}"

    def test_the_prompt_names_the_mode_the_references_and_the_budget(self):
        brief = {"mode": "refine", "references": ["refs/cat_front_cutout.png", "refs/cat_front_original.jpg"],
                 "startingMesh": "start/prev.glb", "guidance": "It is a ceramic mug", "targetSizeMm": 90,
                 "maxIterations": 7, "maxMinutes": 30}
        prompt = task_prompt(brief)
        assert "REFINE" in prompt and "prev.glb" in prompt
        assert "cat_front_cutout.png" in prompt
        assert "ceramic mug" in prompt and "90 mm" in prompt
        assert "7 build-render-compare" in prompt and "30 minutes" in prompt
        assert "AGENTS.md" in prompt and "output/DONE.json" in prompt

    def test_a_build_prompt_does_not_mention_a_starting_mesh(self):
        prompt = task_prompt({"mode": "build", "references": ["refs/a_cutout.png"], "startingMesh": None,
                              "guidance": None, "targetSizeMm": None, "maxIterations": 12, "maxMinutes": 45})
        assert "BUILD" in prompt and "Starting mesh" not in prompt


@pytest.mark.unit
class TestImagesAreLiftedOutOfToolResults:
    """Measured against GPT-5.6 on Bedrock: an image INSIDE a tool result is rejected, an image beside one
    in the same user turn is accepted. The hook turns the first shape into the second. Without it the
    agent failed the first time it looked at its own render, which is the whole point of the loop."""

    @pytest.fixture(autouse=True)
    def _hook(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "bridge_image_hook", os.path.join(_TOOLS_DIR, "bridge_image_hook.py"))
        self.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.hook)

    def test_a_responses_tool_output_holding_an_image_is_split(self):
        image = {"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "auto"}
        items = [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "look"}]},
            {"type": "function_call", "call_id": "c1", "name": "view_image", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": [image]},
        ]
        rewritten, moved = self.hook.lift_images_from_responses_input(items)
        assert moved == 1
        tool_output = rewritten[2]
        assert tool_output["type"] == "function_call_output"
        assert not any(p.get("type") == "input_image" for p in tool_output["output"])
        follow_up = rewritten[3]
        assert follow_up["role"] == "user"
        assert follow_up["content"][-1]["type"] == "input_image"
        assert follow_up["content"][-1]["image_url"] == "data:image/png;base64,AAAA"
        assert "c1" in follow_up["content"][0]["text"]

    def test_a_chat_tool_message_holding_an_image_is_split(self):
        messages = [
            {"role": "user", "content": "look"},
            {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function",
                                                  "function": {"name": "view_image", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": [
                {"type": "text", "text": "ok"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBBB"}}]},
        ]
        rewritten, moved = self.hook.lift_images_from_chat_messages(messages)
        assert moved == 1
        assert rewritten[2]["role"] == "tool" and rewritten[2]["content"] == [{"type": "text", "text": "ok"}]
        assert rewritten[3]["role"] == "user"
        assert rewritten[3]["content"][-1]["image_url"] == {"url": "data:image/png;base64,BBBB"}

    def test_requests_without_images_in_tool_results_pass_through_unchanged(self):
        items = [{"type": "function_call_output", "call_id": "c1", "output": "plain text"},
                 {"type": "message", "role": "user", "content": [{"type": "input_image", "image_url": "x"}]}]
        rewritten, moved = self.hook.lift_images_from_responses_input(list(items))
        assert moved == 0 and rewritten == items

    def test_the_request_rewrite_handles_both_shapes(self):
        data = {"input": [{"type": "function_call_output", "call_id": "c", "output": [
            {"type": "input_image", "image_url": "d"}]}]}
        data, moved = self.hook.rewrite_request(data)
        assert moved == 1 and data["input"][1]["role"] == "user"

    def test_a_model_id_litellm_does_not_know_is_registered_from_a_known_sibling(self):
        """GPT-6 Astra, not in the installed LiteLLM's map, was sent no tools and answered in 13 seconds
        that it had no terminal. Registering the id with its sibling's capabilities restores them."""
        litellm = pytest.importorskip("litellm")
        from litellm.llms.bedrock.chat.converse_transformation import AmazonConverseConfig

        model = "global.openai.gpt-99-test-only"
        assert model not in litellm.model_cost
        outcome = self.hook.ensure_model_known(model)
        assert outcome.startswith("registered from ")
        assert model in litellm.model_cost
        assert litellm.supports_function_calling(model=model, custom_llm_provider="bedrock_converse")
        assert "tools" in AmazonConverseConfig().get_supported_openai_params(model)
        assert self.hook.ensure_model_known("global.openai.gpt-5.6-terra") == "known"
        assert self.hook.ensure_model_known("") == "no model id"

    def test_a_gpt_6_id_takes_the_openai_reasoning_path_not_anthropic_thinking(self):
        """LiteLLM keys OpenAI-on-Bedrock handling on the substring 'openai.gpt-5'. Astra's id fails that
        test, was sent Anthropic's `thinking`, and Bedrock rejected it before the agent's first turn."""
        pytest.importorskip("litellm")
        from litellm.llms.bedrock.chat.converse_transformation import AmazonConverseConfig

        assert self.hook.is_openai_on_bedrock("global.openai.gpt-6-astra")
        assert self.hook.is_openai_on_bedrock("us.openai.gpt-5.6-terra")
        assert not self.hook.is_openai_on_bedrock("openai.gpt-oss-120b-1:0")
        assert not self.hook.is_openai_on_bedrock("global.anthropic.claude-sonnet-4-5-20250929-v1:0")
        config = AmazonConverseConfig()
        params = {}
        config._handle_reasoning_effort_parameter("global.openai.gpt-6-astra", "high", params)
        assert params == {"reasoning": {"effort": "high"}}, params
        supported = config.get_supported_openai_params("global.openai.gpt-6-astra")
        assert "reasoning_effort" in supported and "tools" in supported
        # Anthropic models keep LiteLLM's own handling.
        params = {}
        config._handle_reasoning_effort_parameter("anthropic.claude-sonnet-4-5-20250929-v1:0", "medium", params)
        assert "thinking" in params or "output_config" in params

    def test_the_bridge_configuration_names_the_hook(self):
        config = litellm_config("m", "r")
        assert "bridge_image_hook.proxy_handler_instance" in config["litellm_settings"]["callbacks"]


@pytest.mark.unit
class TestACompletionThatBilledNoTokensIsRefused:
    """Bedrock answered six of one session's 662 ConverseStream calls with HTTP 200 and zero input, zero
    output tokens; the bridge forwarded each as a completed response and the agent ended its turn, and with
    it the run, having done nothing. The bridge fills a zero prompt count from its own token counter, so the
    rule reads the output side: a completion with no output and no output tokens ends the stream in an
    error, which the agent retries with its history."""

    @pytest.fixture(autouse=True)
    def _hook(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "bridge_image_hook", os.path.join(_TOOLS_DIR, "bridge_image_hook.py"))
        self.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.hook)

    @staticmethod
    def _completed(usage, output=None):
        return SimpleNamespace(type="response.completed",
                               response=SimpleNamespace(usage=usage, output=output))

    def _relay(self, *events):
        async def upstream():
            for event in events:
                yield event

        async def collect():
            out = []
            async for chunk in self.hook.proxy_handler_instance.async_post_call_streaming_iterator_hook(
                    user_api_key_dict=None, response=upstream(), request_data={"model": "bridge"}):
                out.append(chunk)
            return out

        return asyncio.run(collect())

    def test_a_billed_completion_and_every_other_event_pass_through_unchanged(self):
        events = [SimpleNamespace(type="response.created", response=SimpleNamespace(usage=None)),
                  SimpleNamespace(type="response.output_text.delta", delta="a"),
                  self._completed(SimpleNamespace(input_tokens=438, output_tokens=12))]
        assert self._relay(*events) == events

    def test_a_completion_that_billed_nothing_ends_the_stream_in_an_error(self, capsys):
        with pytest.raises(self.hook.EmptyCompletionRefused, match="no output"):
            self._relay(SimpleNamespace(type="response.created", response=SimpleNamespace(usage=None)),
                        self._completed(SimpleNamespace(input_tokens=0, output_tokens=0)))
        assert "refused an empty completion" in capsys.readouterr().out

    def test_a_completion_carrying_no_usage_at_all_is_refused_too(self):
        with pytest.raises(self.hook.EmptyCompletionRefused):
            self._relay(self._completed(None))

    def test_a_prompt_count_the_bridge_filled_in_does_not_save_an_empty_completion(self):
        """The bridge replaces a zero prompt count with its own token count of the request, so on the fault
        the input side reads as billed; the output side is what stays empty."""
        with pytest.raises(self.hook.EmptyCompletionRefused):
            self._relay(self._completed(SimpleNamespace(input_tokens=34, output_tokens=0)))
        with pytest.raises(self.hook.EmptyCompletionRefused):
            self._relay(self._completed(SimpleNamespace(input_tokens=34, output_tokens=0), output=[
                {"type": "reasoning", "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": ""}]}]))

    def test_a_completion_carrying_output_is_never_refused(self):
        """A function call or text is something the model produced, whatever the usage says."""
        tool_call = self._completed(SimpleNamespace(input_tokens=0, output_tokens=0),
                                    output=[{"type": "function_call", "name": "shell", "arguments": "{}"}])
        text = self._completed(None, output=[SimpleNamespace(
            type="message", content=[SimpleNamespace(type="output_text", text="done")])])
        assert self._relay(tool_call, text) == [tool_call, text]

    def test_only_a_completion_is_judged(self):
        """A created event legitimately carries a response with no usage yet; only the completion is read."""
        events = [SimpleNamespace(type="response.created", response=SimpleNamespace(usage=None)),
                  SimpleNamespace(type="response.in_progress", response=SimpleNamespace(
                      usage=SimpleNamespace(input_tokens=0, output_tokens=0))),
                  self._completed(SimpleNamespace(input_tokens=438, output_tokens=12))]
        assert self._relay(*events) == events

    def test_the_rule_reads_litellm_s_own_completed_event(self):
        """LiteLLM's event type is a str enum and its usage a pydantic model; the check must read both."""
        pytest.importorskip("litellm")
        from litellm.types.llms.openai import (ResponseAPIUsage, ResponseCompletedEvent,
                                               ResponsesAPIStreamEvents)

        def completed(input_tokens, output_tokens):
            usage = ResponseAPIUsage(input_tokens=input_tokens, output_tokens=output_tokens,
                                     total_tokens=input_tokens + output_tokens)
            response = SimpleNamespace(usage=usage)
            return SimpleNamespace(type=ResponsesAPIStreamEvents.RESPONSE_COMPLETED, response=response)

        assert isinstance(ResponsesAPIStreamEvents.RESPONSE_COMPLETED, str)
        assert ResponseCompletedEvent.model_fields.keys() >= {"type", "response"}
        assert self._relay(completed(438, 12)) is not None
        with pytest.raises(self.hook.EmptyCompletionRefused):
            self._relay(completed(0, 0))
        with pytest.raises(self.hook.EmptyCompletionRefused):
            self._relay(completed(34, 0))


@pytest.mark.unit
class TestCompletionIsAFile:
    def test_the_event_summary_tolerates_an_unknown_stream_shape(self, tmp_path):
        path = tmp_path / "events.jsonl"
        path.write_text(
            '{"type":"thread.started"}\n'
            '{"type":"item.completed","item":{"type":"command_execution","command":"blender"}}\n'
            '{"type":"item.completed","item":{"type":"agent_message"}}\n'
            'not json at all\n'
            '{"type":"turn.completed","usage":{"input_tokens":1200,"output_tokens":300}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":800,"output_tokens":100}}\n',
            encoding="utf-8")
        summary = blenderagent._summarise_events(path)
        assert summary == {"commands": 1, "turns": 2, "inputTokens": 2000, "outputTokens": 400}

    def test_a_missing_event_file_summarises_to_zero(self, tmp_path):
        assert blenderagent._summarise_events(tmp_path / "absent.jsonl")["commands"] == 0

    def test_a_failure_digest_leads_with_the_errors_and_ends_with_the_commands(self, tmp_path):
        """The first live failure raised with a one-line stderr tail and nothing else; the stream that
        explained it was never read. The digest is what the exception now carries."""
        path = tmp_path / "events.jsonl"
        path.write_text(
            '{"type":"thread.started"}\n'
            '{"type":"item.completed","item":{"type":"error","message":"Model metadata for agent not found."}}\n'
            '{"type":"item.completed","item":{"type":"command_execution","command":"blender -b -P x.py",'
            '"exit_code":1,"status":"failed","aggregated_output":"...\\nError: sandbox denied"}}\n'
            '{"type":"turn.failed","error":{"message":"stream disconnected before completion"}}\n',
            encoding="utf-8")
        digest = blenderagent._failure_digest(path)
        assert "Model metadata" in digest
        assert "stream disconnected" in digest
        assert "blender -b -P x.py" in digest and "exit=1" in digest and "sandbox denied" in digest
        # Errors come first: the line that matters must not be buried under command output.
        assert digest.index("Model metadata") < digest.index("blender -b")

    def test_the_failure_carries_its_diagnostics_for_the_container_to_upload(self):
        error = blenderagent.AgentRunFailed("no mesh", diagnostics=["/x/notes.md", "/x/events.jsonl"])
        assert isinstance(error, RuntimeError)
        assert error.diagnostics == ["/x/notes.md", "/x/events.jsonl"]

    def test_references_are_named_so_the_agent_can_pair_cutout_with_original(self, tmp_path):
        prepared = tmp_path / "cat_front_nobg_framed.png"
        prepared.write_bytes(b"x")
        original = tmp_path / "cat_front.jpg"
        original.write_bytes(b"y")
        workspace = tmp_path / "ws"
        (workspace / "refs").mkdir(parents=True)
        staged = BlenderAgentAdapter._stage_references(workspace, [str(prepared)], [str(original)])
        names = sorted(os.path.basename(s) for s in staged)
        assert names == ["cat_front_cutout.png", "cat_front_original.jpg"]

    def test_the_dockerfile_carries_the_tools_and_the_three_executables(self):
        dockerfile = open(os.path.join(_CONTAINER_DIR, "Dockerfile.blenderagent"), encoding="utf-8").read()
        assert "COPY blenderagent_tools /opt/ml/code/blenderagent_tools" in dockerfile
        assert "blender --version" in dockerfile, "the Blender install is never verified"
        assert "codex --version" in dockerfile, "the Codex install is never verified"
        assert "litellm --version" in dockerfile, "the bridge install is never verified"
        assert "BLENDERAGENT_ROOT=/var/agent" in dockerfile

    def test_the_dockerfile_carries_the_refits_depth_model_at_pinned_revisions(self):
        """The MoGe clone's own pin is checked with every other clone's in containerBuildSources.test.ts; the
        utils3d install is a pip URL that check does not read, and the import check is what proves the
        dependencies MoGe was installed without are the ones it needs."""
        dockerfile = open(os.path.join(_CONTAINER_DIR, "Dockerfile.blenderagent"), encoding="utf-8").read()
        assert re.search(r"^ARG MOGE_COMMIT=[0-9a-f]{40}$", dockerfile, re.M)
        assert re.search(r"^ARG UTILS3D_COMMIT=[0-9a-f]{40}$", dockerfile, re.M)
        assert "utils3d.git@${UTILS3D_COMMIT}" in dockerfile
        assert "pip install --no-cache-dir --no-deps /opt/moge" in dockerfile
        assert "from moge.model.v2 import MoGeModel" in dockerfile, "the MoGe install is never verified"
