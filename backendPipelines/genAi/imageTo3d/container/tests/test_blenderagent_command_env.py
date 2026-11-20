# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The environment a command the Blender agent runs actually receives, through the real Codex binary.

Codex is started exactly as the adapter starts it: the workspace from ``_fresh_root``, the config from
``codex_config``, the environment from ``codex_process_env`` and ``_codex_run_env``, the same flags. A
stand-in for the Responses API answers Codex's first request with one shell call that prints the command's
variable NAMES (never a value) and its second with a final message.

The job's own environment is the parent, with a task-credentials path and a Hugging Face token set when the
host has none, so the check is the same on a developer machine and inside the job.

Skipped where Codex is not installed. In the Blender agent image it runs as a script too (the image has no
pytest): ``python tests/test_blenderagent_command_env.py`` prints the names and exits 1 on a leak.
"""

import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)

from models import blenderagent  # noqa: E402

BRIDGE_KEY_ENV = "BLENDERAGENT_BRIDGE_KEY"
MARKER = "ENVNAMES:"
COMMAND = f"echo {MARKER} $(env | cut -d= -f1 | sort | tr '\\n' ' ')"


def _stand_in(outputs: list):
    """A Responses API stand-in: one shell call running COMMAND, then a final message."""
    state = {"n": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps({"object": "list", "data": [], "models": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            tools = [t.get("name") or (t.get("function") or {}).get("name") for t in request.get("tools", [])]
            for item in request.get("input", []):
                if isinstance(item, dict) and item.get("type") == "function_call_output":
                    outputs.append(item.get("output"))
            n = state["n"]
            state["n"] += 1
            if n == 0:
                if "exec_command" in tools:
                    name, args = "exec_command", {"cmd": COMMAND}
                elif "shell_command" in tools:
                    name, args = "shell_command", {"command": COMMAND}
                else:
                    name, args = "shell", {"command": ["sh", "-c", COMMAND]}
                item = {"type": "function_call", "id": "fc_0", "call_id": "call_0", "name": name,
                        "arguments": json.dumps(args), "status": "completed"}
            else:
                item = {"type": "message", "id": f"msg_{n}", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "done", "annotations": []}]}
            usage = {"input_tokens": 1, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 1,
                     "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 2}
            rid = f"resp_{n}"
            events = [
                ("response.created", {"type": "response.created", "response": {"id": rid}}),
                ("response.output_item.done",
                 {"type": "response.output_item.done", "output_index": 0, "item": item}),
                ("response.completed", {"type": "response.completed", "response": {
                    "id": rid, "status": "completed", "output": [item], "usage": usage}}),
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event, data in events:
                self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def job_environment() -> dict:
    """The job's own environment, with the variables the check is about present even off the job."""
    parent = dict(os.environ)
    parent.setdefault("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "/v2/credentials/test-only")
    parent.setdefault("HF_TOKEN", "test-only")  # nosec B105 - a placeholder, never a real token
    parent.setdefault("AWS_REGION", "us-east-1")
    return parent


def command_environment(root: Path) -> dict:
    """Run one agent command through Codex the way a run does; return what it and Codex were given."""
    saved_root = blenderagent.AGENT_ROOT
    blenderagent.AGENT_ROOT = root
    try:
        return _command_environment()
    finally:
        blenderagent.AGENT_ROOT = saved_root


def _command_environment() -> dict:
    adapter = blenderagent.BlenderAgentAdapter()
    dirs = adapter._fresh_root()
    for sub in (".config", ".cache", ".tmp"):
        (dirs["workspace"] / sub).mkdir(exist_ok=True)
    outputs: list = []
    server = _stand_in(outputs)
    try:
        port = server.server_address[1]
        (dirs["codex_home"] / "config.toml").write_text(
            blenderagent.codex_config(f"http://127.0.0.1:{port}/v1", BRIDGE_KEY_ENV, "low"), encoding="utf-8")
        codex_env = blenderagent.codex_process_env(
            job_environment(),
            adapter._codex_run_env(dirs, BRIDGE_KEY_ENV, "sk-test-only", "EEVEE", 1))
        command = ["codex", "exec", "--cd", str(dirs["workspace"]), "--sandbox", blenderagent.SANDBOX_MODE,
                   "--skip-git-repo-check", "--ephemeral", "--json", "-"]
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            command, cwd=str(dirs["workspace"]), env=codex_env, input=b"go", capture_output=True, timeout=240)
    finally:
        server.shutdown()
    names = None
    for output in outputs:
        text = output if isinstance(output, str) else json.dumps(output)
        for line in text.replace("\\n", "\n").splitlines():
            if MARKER in line:
                names = set(line.split(MARKER, 1)[1].replace('"', " ").split())
    return {"codex_env": set(codex_env), "command_env": names, "codex_rc": result.returncode,
            "stderr_tail": result.stderr.decode("utf-8", "replace")[-600:]}


def leaks(seen: dict) -> list:
    """The names a command received that it must not, or a reason the check did not run."""
    if seen["command_env"] is None:
        return [f"the command printed no environment (codex rc={seen['codex_rc']}): {seen['stderr_tail']}"]
    bad = sorted(n for n in seen["command_env"] if n.startswith(("AWS_", "HF_")) or n == BRIDGE_KEY_ENV)
    # Controls: the command really ran with this run's values, and Codex itself did hold the bridge key,
    # so its absence above is the environment policy at work and not a key that was never there.
    if "AGENT_WORKSPACE" not in seen["command_env"]:
        bad.append("control: AGENT_WORKSPACE missing, so this is not the agent's command environment")
    if BRIDGE_KEY_ENV not in seen["codex_env"]:
        bad.append("control: Codex was started without the bridge key")
    return bad


try:
    import pytest
except ImportError:  # the image has no pytest; the script path below needs none
    pytest = None

if pytest is not None:
    @pytest.mark.skipif(shutil.which("codex") is None, reason="needs the Codex CLI (the Blender agent image)")
    def test_an_agent_command_receives_no_task_credentials_token_or_bridge_key(tmp_path):
        assert leaks(command_environment(tmp_path / "agent")) == []


if __name__ == "__main__":
    root = Path(os.environ.get("BLENDERAGENT_ROOT", "/var/agent"))
    seen = command_environment(root)
    print("CODEX-ENV-NAMES:", " ".join(sorted(seen["codex_env"])))
    print("COMMAND-ENV-NAMES:", " ".join(sorted(seen["command_env"] or [])))
    found = leaks(seen)
    print("RESULT:", "PASS" if not found else "FAIL " + "; ".join(found))
    sys.exit(1 if found else 0)
