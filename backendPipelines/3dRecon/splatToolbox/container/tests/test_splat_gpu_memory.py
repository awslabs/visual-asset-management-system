#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The shipped templates bound the GPU memory a training run needs, and a run that still runs out of
GPU memory says so (#1357).

With the upstream default model (`splatfacto`) the number of Gaussians grows without a bound until
densification stops at the last step. A 165-photo capture (4864x3648, trained at the 4K cap of
3840x2880) reached 8.3 million Gaussians at step 14,000 of 15,000 and ran out of memory on a 22 GiB
NVIDIA L4 after about 88 GPU minutes. Both templates now train `splatfacto-mcmc`, whose Gaussian count
is capped by `NUM_GAUSSIANS`, a template tag. A run whose training still runs out of GPU memory
reports that as its failure cause instead of a bare exit status.
"""

import io
import json
import os
import re
import subprocess
import sys

import pytest

_TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "vamsSchema", "templates")
TEMPLATES = ("splat-objects.json", "splat-environments-360.json")


def _template(name):
    with open(os.path.join(_TEMPLATES_DIR, name)) as f:
        return json.load(f)


def _rendered_defaults(template):
    """The template's configBody with every tag at its default, as the execute form renders it."""
    body = template["configBody"]
    for tag in template["tagSchema"]:
        default = tag["default"]
        text = default if isinstance(default, str) else json.dumps(default)
        body = body.replace("{{" + tag["tagKey"] + "}}", text)
    assert "{{" not in body, body
    return json.loads(body)


@pytest.mark.unit
class TestTemplatesCapTheGaussianCount:
    @pytest.mark.parametrize("name", TEMPLATES)
    def test_default_run_trains_the_capped_model(self, name):
        config = _rendered_defaults(_template(name))

        assert config["MODEL"] == "splatfacto-mcmc"
        assert config["NUM_GAUSSIANS"] == 3000000

    @pytest.mark.parametrize("name", TEMPLATES)
    def test_the_cap_is_a_tag_the_execute_form_can_change(self, name):
        template = _template(name)
        (tag,) = [t for t in template["tagSchema"] if t["tagKey"] == "NUM_GAUSSIANS"]

        assert tag["type"] == "integer"
        assert tag["default"] == 3000000
        assert tag["required"] is False
        assert '"NUM_GAUSSIANS":{{NUM_GAUSSIANS}}' in template["configBody"]
        assert len(tag["description"]) <= 1024


@pytest.mark.unit
class TestGaussianCapValidation:
    @pytest.mark.parametrize("value", [None, "3000000", "1", " 500000 "])
    def test_a_positive_whole_number_or_no_value_is_accepted(self, container_main, value):
        assert container_main.gaussian_cap_cause(value) is None

    @pytest.mark.parametrize("value", ["0", "-5", "3e6", "3000000.0", "many", ""])
    def test_anything_else_is_refused_before_the_run_starts(self, container_main, value):
        cause = container_main.gaussian_cap_cause(value)

        assert cause.startswith("NUM_GAUSSIANS must be a positive whole number")
        assert len(cause) <= container_main.FAILURE_CAUSE_MAX_LENGTH


@pytest.mark.unit
class TestGpuOutOfMemoryIsReported:
    def _run(self, container_main, script):
        out = io.BytesIO()
        status, out_of_memory = container_main.run_streaming(
            [sys.executable, "-c", script], cwd=os.getcwd(), env=dict(os.environ), out=out)
        return status, out_of_memory, out.getvalue()

    def test_output_is_passed_through_and_an_out_of_memory_error_is_noticed(self, container_main):
        script = ("import sys\n"
                  "print('Step 13700: 109893 GSs pruned. Now having 8000128 GSs.', flush=True)\n"
                  "sys.stderr.write('torch.OutOfMemoryError: CUDA out of memory. Tried to allocate "
                  "1.43 GiB.\\n')\n"
                  "sys.exit(27)\n")

        status, out_of_memory, output = self._run(container_main, script)

        assert status == 27
        assert out_of_memory is True
        assert b"Now having 8000128 GSs." in output
        assert b"CUDA out of memory" in output

    def test_a_run_without_the_error_is_not_reported_as_one(self, container_main):
        status, out_of_memory, output = self._run(
            container_main, "import sys\nprint('Training finished')\nsys.exit(3)\n")

        assert (status, out_of_memory) == (3, False)
        assert output == b"Training finished\n"

    def test_the_failure_cause_names_gpu_memory_and_the_cap(self, container_main, monkeypatch):
        monkeypatch.setenv("MODEL", "splatfacto-mcmc")
        monkeypatch.setenv("NUM_GAUSSIANS", "3000000")
        error = subprocess.CalledProcessError(27, "main.py")

        cause = container_main.failure_cause(error, gpu_out_of_memory=True)

        assert cause.startswith("The GPU ran out of memory during training (exit status 27")
        assert "NUM_GAUSSIANS 3000000" in cause
        assert len(cause) <= container_main.FAILURE_CAUSE_MAX_LENGTH

    def test_any_other_failure_keeps_the_exit_status_cause(self, container_main):
        error = subprocess.CalledProcessError(2, "main.py")

        assert container_main.failure_cause(error) == "Pipeline execution failed with exit status 2"

    def test_the_launcher_runs_main_py_through_the_streaming_runner(self, container_dir):
        with open(os.path.join(container_dir, "__main__.py")) as f:
            source = f.read()

        assert re.search(r"run_streaming\(\s*\[sys\.executable, '-c', launcher\]", source)
        assert "failure_cause(e, gpu_out_of_memory)" in source
