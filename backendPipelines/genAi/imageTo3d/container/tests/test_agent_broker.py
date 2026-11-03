# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The image-edit broker: the one way the agent reaches an image model, through files, without a network.

The agent's commands have no network and no credentials. The broker runs in the pipeline process, reads
request files from the workspace, calls the named Stability AI model on Bedrock with the task role, and
writes the result image and a result file back. These tests drive it with a fake Bedrock client: a request
is served with the image returned at the input's size; a path that leaves the workspace and an operation
that is not on the list both come back as an error the agent can read rather than as an exception; and the
agent-side tool, run as the agent runs it, waits for and reports the result.
"""

import base64
import io
import json
import os
import subprocess
import sys
import threading

import pytest
from PIL import Image

import agent_broker

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS_DIR = os.path.join(_CONTAINER_DIR, "blenderagent_tools")


class FakeBedrock:
    """Returns a flat green image of the size it was sent, and records the request bodies."""

    def __init__(self):
        self.calls = []

    def invoke_model(self, modelId, body, contentType, accept):
        payload = json.loads(body)
        self.calls.append((modelId, payload))
        sent = Image.open(io.BytesIO(base64.b64decode(payload.get("image") or payload["init_image"])))
        out = Image.new("RGB", sent.size, (40, 200, 60))
        buffer = io.BytesIO()
        out.save(buffer, format="PNG")
        return {"body": io.BytesIO(json.dumps({"images": [base64.b64encode(buffer.getvalue()).decode()],
                                               "seeds": [1], "finish_reasons": [None]}).encode())}


@pytest.fixture
def workspace(tmp_path):
    for sub in ("textures", "requests", "refs"):
        (tmp_path / sub).mkdir()
    Image.new("RGB", (2048, 1536), (120, 80, 40)).save(tmp_path / "textures" / "atlas.png")
    Image.new("L", (2048, 1536), 255).save(tmp_path / "textures" / "mask.png")
    Image.new("RGB", (640, 480), (200, 100, 50)).save(tmp_path / "refs" / "photo_cutout.png")
    return tmp_path


@pytest.mark.unit
class TestTheBrokerServesRequestsInsideTheWorkspace:
    def test_an_inpaint_request_is_served_at_the_inputs_size(self, workspace):
        fake = FakeBedrock()
        broker = agent_broker.ImageEditBroker(workspace, "us-west-2", client=fake)
        (workspace / "requests" / "r1.json").write_text(json.dumps({
            "operation": "inpaint", "image": "textures/atlas.png", "mask": "textures/mask.png",
            "prompt": "black cotton", "output": "textures/filled.png"}), encoding="utf-8")
        assert broker.serve_pending() == 1
        result = json.loads((workspace / "requests" / "r1.result.json").read_text(encoding="utf-8"))
        assert result["status"] == "ok" and result["output"] == "textures/filled.png"
        assert result["model"] == "us.stability.stable-image-inpaint-v1:0"
        filled = Image.open(workspace / "textures" / "filled.png")
        assert filled.size == (2048, 1536), "the result comes back at the input's size"
        model_id, body = fake.calls[0]
        sent = Image.open(io.BytesIO(base64.b64decode(body["image"])))
        assert max(sent.size) <= 1024 and sent.size[0] % 64 == 0, sent.size
        assert body["prompt"] == "black cotton" and "mask" in body
        # Served once: a second pass finds nothing to do.
        assert broker.serve_pending() == 0

    def test_a_masked_edit_keeps_every_unmasked_texel_exactly(self, workspace):
        """The atlas goes to the model at up to 1024 px and the result comes back resized to the input's
        size, so shipping the model's whole output would put every texel the photograph had placed through a
        lossy round trip -- the first live inpaint did, and the agent rejected it for distorting protected
        detail. A masked operation is composited under its mask: the model's pixels where the mask is white,
        the input's own where it is black, whatever size the mask was drawn at."""
        fake = FakeBedrock()
        broker = agent_broker.ImageEditBroker(workspace, "us-west-2", client=fake)
        half = Image.new("L", (2048, 1536), 0)
        half.paste(255, (0, 0, 1024, 1536))
        half.save(workspace / "textures" / "half.png")
        Image.new("L", (256, 192), 255).save(workspace / "textures" / "all_small.png")
        for name, mask, operation in (("h", "textures/half.png", "inpaint"), ("e", "textures/all_small.png", "erase")):
            (workspace / "requests" / f"{name}.json").write_text(json.dumps({
                "operation": operation, "image": "textures/atlas.png", "mask": mask, "prompt": "denim",
                "output": f"textures/{name}.png"}), encoding="utf-8")
        assert broker.serve_pending() == 2
        for name in ("h", "e"):
            result = json.loads((workspace / "requests" / f"{name}.result.json").read_text(encoding="utf-8"))
            assert result["status"] == "ok" and result["unmaskedPreserved"] is True, result
        filled = Image.open(workspace / "textures" / "h.png").convert("RGB")
        assert filled.size == (2048, 1536)
        assert filled.getpixel((100, 700)) == (40, 200, 60), "inside the mask: the model's pixels"
        assert filled.getpixel((1900, 700)) == (120, 80, 40), "outside the mask: the input's own texel, untouched"
        erased = Image.open(workspace / "textures" / "e.png").convert("RGB")
        assert erased.getpixel((1900, 700)) == (40, 200, 60), "a mask drawn smaller than the image is fitted to it"
        # An unmasked operation has nothing to composite under and says so.
        (workspace / "requests" / "r.json").write_text(json.dumps({
            "operation": "search_recolor", "image": "textures/atlas.png", "prompt": "blue", "select_prompt": "jeans",
            "output": "textures/r.png"}), encoding="utf-8")
        broker.serve_pending()
        result = json.loads((workspace / "requests" / "r.result.json").read_text(encoding="utf-8"))
        assert result["status"] == "ok" and result["unmaskedPreserved"] is False

    def test_a_structure_request_follows_the_input_and_keeps_only_what_a_mask_protects(self, workspace):
        """The hidden side of a figure is regenerated along the render's outline and pose; the mask is not
        sent to the model but, when given, still decides which of the input's pixels come back untouched."""
        fake = FakeBedrock()
        broker = agent_broker.ImageEditBroker(workspace, "us-west-2", client=fake)
        half = Image.new("L", (2048, 1536), 0)
        half.paste(255, (0, 0, 1024, 1536))
        half.save(workspace / "textures" / "half.png")
        (workspace / "requests" / "s1.json").write_text(json.dumps({
            "operation": "structure", "image": "textures/atlas.png", "prompt": "the back of a woman",
            "control_strength": 0.9, "output": "textures/s1.png"}), encoding="utf-8")
        (workspace / "requests" / "s2.json").write_text(json.dumps({
            "operation": "structure", "image": "textures/atlas.png", "prompt": "the back of a woman",
            "mask": "textures/half.png", "output": "textures/s2.png"}), encoding="utf-8")
        assert broker.serve_pending() == 2
        for model, body in fake.calls:
            assert model == "us.stability.stable-image-control-structure-v1:0"
            assert body["prompt"] == "the back of a woman" and "mask" not in body, "the structure model takes no mask"
        assert {body.get("control_strength") for _, body in fake.calls} == {None, 0.9}
        r1 = json.loads((workspace / "requests" / "s1.result.json").read_text(encoding="utf-8"))
        r2 = json.loads((workspace / "requests" / "s2.result.json").read_text(encoding="utf-8"))
        assert r1["status"] == "ok" and r1["unmaskedPreserved"] is False
        assert r2["status"] == "ok" and r2["unmaskedPreserved"] is True
        kept = Image.open(workspace / "textures" / "s2.png").convert("RGB")
        assert kept.getpixel((100, 700)) == (40, 200, 60) and kept.getpixel((1900, 700)) == (120, 80, 40)

    def test_a_search_replace_request_names_what_to_replace(self, workspace):
        fake = FakeBedrock()
        broker = agent_broker.ImageEditBroker(workspace, "us-west-2", client=fake)
        (workspace / "requests" / "sr.json").write_text(json.dumps({
            "operation": "search_replace", "image": "textures/atlas.png", "prompt": "white low-top sneakers",
            "search_prompt": "the shoes", "output": "textures/sr.png"}), encoding="utf-8")
        broker.serve_pending()
        model, body = fake.calls[0]
        assert model == "us.stability.stable-image-search-replace-v1:0"
        assert body["search_prompt"] == "the shoes" and body["prompt"] == "white low-top sneakers"

    def test_a_style_transfer_request_carries_the_style_image(self, workspace):
        fake = FakeBedrock()
        broker = agent_broker.ImageEditBroker(workspace, "us-west-2", client=fake)
        (workspace / "requests" / "s.json").write_text(json.dumps({
            "operation": "style_transfer", "image": "textures/atlas.png", "style": "refs/photo_cutout.png",
            "prompt": "same person", "style_strength": 0.7, "output": "textures/styled.png"}), encoding="utf-8")
        broker.serve_pending()
        result = json.loads((workspace / "requests" / "s.result.json").read_text(encoding="utf-8"))
        assert result["status"] == "ok"
        _, body = fake.calls[0]
        assert "init_image" in body and "style_image" in body and body["style_strength"] == 0.7

    def test_a_path_that_leaves_the_workspace_is_refused_as_a_result_not_a_crash(self, workspace):
        broker = agent_broker.ImageEditBroker(workspace, "us-west-2", client=FakeBedrock())
        (workspace / "requests" / "bad.json").write_text(json.dumps({
            "operation": "inpaint", "image": "../../etc/passwd", "mask": "textures/mask.png",
            "output": "textures/x.png"}), encoding="utf-8")
        broker.serve_pending()
        result = json.loads((workspace / "requests" / "bad.result.json").read_text(encoding="utf-8"))
        assert result["status"] == "error" and "leaves the workspace" in result["message"]

    def test_an_output_outside_the_workspace_is_refused(self, workspace):
        broker = agent_broker.ImageEditBroker(workspace, "us-west-2", client=FakeBedrock())
        (workspace / "requests" / "out.json").write_text(json.dumps({
            "operation": "erase", "image": "textures/atlas.png", "mask": "textures/mask.png",
            "output": "../escaped.png"}), encoding="utf-8")
        broker.serve_pending()
        result = json.loads((workspace / "requests" / "out.result.json").read_text(encoding="utf-8"))
        assert result["status"] == "error" and "leaves the workspace" in result["message"]
        assert not (workspace.parent / "escaped.png").exists()

    def test_an_operation_not_on_the_list_is_refused(self, workspace):
        broker = agent_broker.ImageEditBroker(workspace, "us-west-2", client=FakeBedrock())
        (workspace / "requests" / "op.json").write_text(json.dumps({
            "operation": "text_to_image", "image": "textures/atlas.png", "output": "textures/x.png"}), encoding="utf-8")
        broker.serve_pending()
        result = json.loads((workspace / "requests" / "op.result.json").read_text(encoding="utf-8"))
        assert result["status"] == "error" and "unknown operation" in result["message"]

    def test_the_broker_shadows_no_private_thread_attribute(self, workspace):
        """The first live run with the broker died in the adapter's ``finally``: the stop flag was stored as
        ``self._stop``, which ``threading.Thread`` uses as an internal method that ``join()`` calls on Python
        3.12 -- the container's Python. Storing an Event there replaced the method, and ``join()`` then tried
        to call the Event. The tests run under 3.13, where CPython dropped ``_stop`` from ``Thread`` entirely,
        so a ``dir(Thread)``-based check has no teeth on this interpreter -- the very fault is invisible here.
        Pin the concrete Thread internal names ``join()`` relies on across supported CPython versions."""
        # Names threading.Thread has defined as its own METHODS across CPython 3.11-3.13; join() drives
        # several. Thread's instance DATA (_started, _tstate_lock, _is_stopped, _target, ...) is deliberately
        # excluded -- storing an Event under _started is what Thread itself does; shadowing a method is the fault.
        reserved = {"_stop", "_wait_for_tstate_lock", "_bootstrap", "_bootstrap_inner", "_set_tstate_lock",
                    "_reset_internal_locks", "_delete", "start", "run", "join", "is_alive"}
        broker = agent_broker.ImageEditBroker(workspace, "us-west-2", client=FakeBedrock())
        shadowed = set(vars(broker)) & reserved
        assert not shadowed, f"instance attributes shadow threading.Thread internals: {shadowed}"
        broker.start()
        broker.stop()
        broker.join(timeout=10)
        assert not broker.is_alive()

    def test_every_operation_maps_to_a_stability_inference_profile(self):
        for operation, model in agent_broker.OPERATIONS.items():
            assert model.startswith("us.stability."), (operation, model)


@pytest.mark.unit
def test_the_agent_side_tool_writes_a_request_and_waits_for_the_result(workspace):
    """The tool the agent runs, run the way the agent runs it: no network, a request file, a wait."""
    fake = FakeBedrock()
    broker = agent_broker.ImageEditBroker(workspace, "us-west-2", client=fake)
    broker.start()
    try:
        env = dict(os.environ, AGENT_WORKSPACE=str(workspace), PYTHONUTF8="1")
        done = subprocess.run([sys.executable, os.path.join(_TOOLS_DIR, "agent_image_edit.py"), "inpaint",
                               "--image", "textures/atlas.png", "--mask", "textures/mask.png",
                               "--prompt", "the back of a black t-shirt", "--out", "textures/back.png", "--timeout", "60"],
                              cwd=str(workspace), env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
    finally:
        broker.stop()
        broker.join(timeout=10)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "done in" in done.stdout and "LOOK AT IT" in done.stdout
    assert (workspace / "textures" / "back.png").is_file()
    assert fake.calls and fake.calls[0][1]["prompt"] == "the back of a black t-shirt"


@pytest.mark.unit
def test_the_agent_side_tool_refuses_paths_outside_the_workspace(workspace):
    env = dict(os.environ, AGENT_WORKSPACE=str(workspace), PYTHONUTF8="1")
    done = subprocess.run([sys.executable, os.path.join(_TOOLS_DIR, "agent_image_edit.py"), "erase",
                           "--image", "../outside.png", "--mask", "textures/mask.png", "--out", "textures/x.png"],
                          cwd=str(workspace), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert done.returncode != 0 and "outside the workspace" in (done.stdout + done.stderr)
