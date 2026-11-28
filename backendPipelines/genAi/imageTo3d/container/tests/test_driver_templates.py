#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every adapter's driver script is Python held in a string, so nothing type-checks or imports it.

An adapter does not call its upstream model in-process: each one writes a small driver script into the
work directory and runs it with the interpreter inside the upstream repository, because the upstream
stacks pin conflicting dependencies and cannot share one process. The consequence is that a driver's
syntax is never checked anywhere -- not by an import, not by a linter reading the module, not by the
container's own tests -- and a typo in one surfaces only as a failed AWS Batch GPU job, minutes and a GPU
instance later.

These tests parse each driver and check the contract it has with its adapter: the arguments it is handed
on the command line, and the settings keys it reads out of the JSON payload against the keys the adapter
actually puts there. A driver reading a key the adapter never sends gets a KeyError on a GPU.
"""

import pathlib
import ast
import json
import os
import re

import pytest

from models.sam3d import FIELD_FILENAME, STATS_FILENAME, Sam3dAdapter

_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")

# Adapters that drive their model through a generated script. An adapter without one is not a failure --
# a model whose upstream imports cleanly into the container's own process needs no driver.
_DRIVER_PATTERN = re.compile(r"^([A-Z_]*DRIVER[A-Z_]*)\s*=\s*'''", re.M)


def _modules():
    return sorted(name[:-3] for name in os.listdir(_MODELS_DIR)
                  if name.endswith(".py") and name not in ("__init__.py", "base.py"))


def _source(module):
    with open(os.path.join(_MODELS_DIR, f"{module}.py"), encoding="utf-8") as handle:
        return handle.read()


def _drivers(module):
    """``{constantName: script}`` for every driver script one adapter module holds."""
    text = _source(module)
    found = {}
    for name in _DRIVER_PATTERN.findall(text):
        found[name] = text.split(f"{name} = '''", 1)[1].split("'''", 1)[0]
    return found


def _driver_setting_keys(script):
    """Every ``settings["KEY"]`` and ``settings.get("KEY"...)`` the driver reads."""
    return set(re.findall(r"""settings(?:\.get\(|\[)\s*["']([A-Za-z0-9_]+)["']""", script))


def _adapter_driver_settings(module):
    """The keys the adapter puts into its ``driver_settings`` dict.

    Read off the source rather than by calling ``generate``, which would need the model's weights and a
    GPU. The dict is a literal in every adapter, so its keys are readable statically.
    """
    text = _source(module)
    if "driver_settings = {" not in text:
        return None
    block = text.split("driver_settings = {", 1)[1]
    depth = 1
    for index, character in enumerate(block):
        depth += {"{": 1, "}": -1}.get(character, 0)
        if depth == 0:
            block = block[:index]
            break
    return set(re.findall(r"""^\s*["']([A-Za-z0-9_]+)["']\s*:""", block, re.M))


@pytest.mark.unit
@pytest.mark.parametrize("module", _modules())
def test_every_driver_script_is_valid_python(module):
    """A syntax error here costs a GPU job to discover, and nothing else looks at these strings."""
    for name, script in _drivers(module).items():
        try:
            ast.parse(script)
        except SyntaxError as e:
            pytest.fail(f"{module}.{name} line {e.lineno}: {e.msg}\n{e.text}")


@pytest.mark.unit
@pytest.mark.parametrize("module", _modules())
def test_every_driver_reads_its_three_command_line_arguments(module):
    """The adapters all invoke their driver as ``driver.py <image> <output_dir> <settings json>``."""
    for name, script in _drivers(module).items():
        assert "sys.argv[1]" in script, f"{module}.{name} never reads the image path"
        assert "sys.argv[2]" in script, f"{module}.{name} never reads the output directory"
        assert "sys.argv[3]" in script, f"{module}.{name} never reads the settings payload"
        assert "json.loads(sys.argv[3])" in script, f"{module}.{name} does not parse the settings"


@pytest.mark.unit
@pytest.mark.parametrize("module", _modules())
def test_every_settings_key_a_driver_reads_is_one_its_adapter_sends(module):
    """A key the driver reads and the adapter never sends is a KeyError on a GPU instance.

    The reverse is allowed: an adapter may send a value a driver only uses on one branch.
    """
    sent = _adapter_driver_settings(module)
    if sent is None:
        pytest.skip(f"{module} builds no driver_settings dict")
    for name, script in _drivers(module).items():
        read = _driver_setting_keys(script)
        missing = sorted(read - sent)
        assert not missing, f"{module}.{name} reads settings its adapter never sends: {missing}"


@pytest.mark.unit
def test_the_settings_payload_a_driver_receives_is_json_serialisable():
    """Every value in a driver_settings dict crosses the process boundary as JSON."""
    for module in _modules():
        for name, script in _drivers(module).items():
            assert "json.loads" in script, f"{module}.{name}"
    # A dict is the only shape the drivers unpack, and json.dumps of a non-serialisable value would
    # fail in the adapter rather than the driver, which is where it belongs.
    json.dumps({"probe": 1.0})


# Adapters whose texture stage is a multi-view diffusion pipeline carrying its own attention
# processors. Attention slicing replaces those processors, so the call must not appear in any of them.
_MULTIVIEW_TEXTURE_ADAPTERS = ("step1x3d", "hunyuan3d")


@pytest.mark.parametrize("module", _MULTIVIEW_TEXTURE_ADAPTERS)
def test_a_multiview_texture_stage_is_never_asked_to_slice_attention(module):
    """Attention slicing destroys a texture stage whose behaviour lives in its attention processors.

    Step1X-3D builds its pipeline around a custom processor and loads learned weights into it --
    `init_custom_adapter(self_attn_processor=DecoupledMVRowColSelfAttnProcessor2_0)` then
    `load_custom_adapter(..., "step1x-3d-ig2v.safetensors")` -- and that processor carries both the image
    conditioning and the coupling between the six generated views. Hunyuan3D-2's paint stage is the same
    shape of thing: a multi-view diffusion pass conditioned on the reference image.

    diffusers' `Attention.set_attention_slice` finishes with an unconditional `self.set_processor(...)` to a
    built-in `SlicedAttnProcessor`, with no check of what is installed and no preservation path. So enabling
    it replaces the adapter, and the stage samples with neither the reference image nor any cross-view
    attention. Every textured Step1X-3D run made while the call was present produced an atlas of content
    unrelated to the photograph -- several different pairs of jeans, a teal top, red hair -- and nothing
    else that was varied made any difference, because the slicing ran in every memory mode that produced a
    texture at all.

    The call does not raise when it does this, so only its absence can be asserted.
    """
    source = pathlib.Path(os.path.join(_MODELS_DIR, f"{module}.py")).read_text(encoding="utf-8")
    calls = [line.strip() for line in source.splitlines()
             if "enable_attention_slicing" in line and not line.lstrip().startswith("#")]

    assert not calls, f"attention slicing must not be applied to {module}'s pipeline: {calls}"


def test_step1x3d_keeps_vae_slicing():
    """VAE slicing is a different operation on a different module and is the safe memory control.

    It splits the decode, which is where a 2048x2048 output puts its largest allocation, and it is what
    lets that stage fit the general fleet's 22 GiB card without touching the attention processors.
    """
    source = pathlib.Path(os.path.join(_MODELS_DIR, "step1x3d.py")).read_text(encoding="utf-8")
    assert "enable_vae_slicing" in source, "VAE slicing is the safe memory control and should remain"


def _sam3d_driver():
    return _drivers("sam3d")["DRIVER_TEMPLATE"]


def _reads_setting(node, key):
    """Whether ``node`` is the expression ``settings["key"]``."""
    return (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
            and node.value.id == "settings" and isinstance(node.slice, ast.Constant)
            and node.slice.value == key)


@pytest.mark.unit
class TestTheSam3dDriverInstallsTheInteriorFillOnlyWhenAsked:
    """The fill rebinds a function inside the upstream extractor and imports torch to do it. A run with
    the switch off must reach neither, so the install call sits under the switch and nothing at the
    driver's top level imports what the fill needs."""

    def test_the_install_call_is_gated_on_the_switch(self):
        tree = ast.parse(_sam3d_driver())
        gated = [node for node in tree.body if isinstance(node, ast.If)
                 and _reads_setting(node.test, "solid_interior")
                 and any("install_solid_interior(" in ast.unparse(stmt) for stmt in node.body)]
        assert len(gated) == 1
        ungated = [node for node in tree.body if not isinstance(node, (ast.If, ast.FunctionDef))
                   and "install_solid_interior(" in ast.unparse(node)]
        assert not ungated, [ast.unparse(node) for node in ungated]

    def test_the_gate_hands_the_opening_over_as_the_adapter_typed_it(self):
        tree = ast.parse(_sam3d_driver())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "install_solid_interior"]
        assert len(calls) == 1
        assert [ast.unparse(arg) for arg in calls[0].args] == ["settings['solid_interior_opening']"]

    def test_nothing_at_the_top_level_imports_what_the_fill_needs(self):
        tree = ast.parse(_sam3d_driver())
        imported = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not imported & {"torch", "numpy", "scipy", "sam3d_objects"}, sorted(imported)

    def test_a_switched_on_run_whose_seeding_was_never_rewritten_stops(self):
        """Upstream can move the call the fill hooks; a mesh made without it carries the inner wall."""
        script = _sam3d_driver()
        assert "if settings[\"solid_interior\"] and not solid_interior_runs:" in script
        after = script.split("if settings[\"solid_interior\"] and not solid_interior_runs:", 1)[1]
        assert after.lstrip().startswith("raise RuntimeError(")


@pytest.mark.unit
class TestTheSam3dDriverDumpsTheFieldOnlyWhenAsked:
    """The dump rebinds the same extractor function the fill does and imports numpy to write. It sits
    under its own switch, goes in ahead of the fill so the file holds the field the model seeded, and a
    switched-on dump that never fired is a warning, not a failed run: the mesh does not depend on it."""

    def _gates(self, key, call):
        tree = ast.parse(_sam3d_driver())
        return [index for index, node in enumerate(tree.body) if isinstance(node, ast.If)
                and _reads_setting(node.test, key)
                and any(call in ast.unparse(stmt) for stmt in node.body)]

    def test_the_install_call_is_gated_on_the_switch(self):
        assert len(self._gates("dump_field", "install_field_dump(")) == 1
        tree = ast.parse(_sam3d_driver())
        ungated = [node for node in tree.body if not isinstance(node, (ast.If, ast.FunctionDef))
                   and "install_field_dump(" in ast.unparse(node)]
        assert not ungated, [ast.unparse(node) for node in ungated]

    def test_the_dump_is_installed_ahead_of_the_fill(self):
        [dump] = self._gates("dump_field", "install_field_dump(")
        [fill] = self._gates("solid_interior", "install_solid_interior(")
        assert dump < fill

    def test_the_gate_hands_over_the_file_the_adapter_reads(self):
        tree = ast.parse(_sam3d_driver())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "install_field_dump"]
        assert len(calls) == 1
        assert FIELD_FILENAME in ast.unparse(calls[0].args[0])

    def test_a_switched_on_dump_that_never_fired_only_warns(self):
        tree = ast.parse(_sam3d_driver())
        checks = [node for node in tree.body if isinstance(node, ast.If) and isinstance(node.test, ast.BoolOp)
                  and isinstance(node.test.op, ast.And) and _reads_setting(node.test.values[0], 'dump_field')
                  and ast.unparse(node.test.values[1]) == 'not field_dumps']
        assert len(checks) == 1
        assert not any(isinstance(stmt, ast.Raise) for stmt in ast.walk(checks[0]))
        assert any(isinstance(stmt, ast.Expr) for stmt in checks[0].body)


@pytest.mark.unit
class TestTheSam3dAdapterCarriesTheFieldDumpOntoTheRun:
    """Whatever field files the driver wrote beside the mesh ride onto the execution as diagnostics,
    in name order, and a run that asked for one and got none is still a run."""

    def _generate(self, tmp_path, monkeypatch, dumps, settings):
        snapshot = tmp_path / "snapshot"
        (snapshot / "checkpoints").mkdir(parents=True)
        (snapshot / "checkpoints" / "pipeline.yaml").write_text("stub", encoding="utf-8")
        monkeypatch.setenv("HF_HOME", str(tmp_path))

        def fake_run_upstream(cmd, cwd, hf_home, extra_env=None, timeout=None):
            out_dir = pathlib.Path(cmd[4])
            (out_dir / "sam3d.glb").write_bytes(b"glTF")
            for name in dumps:
                (out_dir / name).write_bytes(b"PK\x03\x04")

        monkeypatch.setattr("models.sam3d.run_upstream", fake_run_upstream)
        adapter = Sam3dAdapter()
        adapter._snapshot_dirs[Sam3dAdapter.CONFIG_REPO] = str(snapshot)
        result = adapter.generate(str(tmp_path / "in.png"), str(tmp_path / "work"), settings)
        return result, tmp_path / "work" / "generate" / "sam3d"

    def test_the_field_file_is_a_diagnostic_and_not_a_texture(self, tmp_path, monkeypatch):
        result, out_dir = self._generate(tmp_path, monkeypatch, [FIELD_FILENAME], {"SAM3D_DUMP_FIELD": "true"})
        assert str(out_dir / FIELD_FILENAME) in result.diagnostics
        assert result.texture_paths == []
        assert result.notes["sam3dDumpField"] == "true"

    def test_a_second_grid_rides_along_in_name_order(self, tmp_path, monkeypatch):
        second = FIELD_FILENAME[:-4] + "-2.npz"
        result, out_dir = self._generate(tmp_path, monkeypatch, [second, FIELD_FILENAME], {"SAM3D_DUMP_FIELD": "true"})
        fields = [path for path in result.diagnostics if path.endswith(".npz")]
        assert fields == [str(out_dir / second), str(out_dir / FIELD_FILENAME)]

    def test_a_dump_that_was_asked_for_but_never_written_leaves_the_run_standing(self, tmp_path, monkeypatch):
        result, _ = self._generate(tmp_path, monkeypatch, [], {"SAM3D_DUMP_FIELD": "true"})
        assert not any(path.endswith(".npz") for path in result.diagnostics)
        assert result.mesh_path.endswith("sam3d.glb")

    def test_the_default_run_asks_for_no_dump(self, tmp_path, monkeypatch):
        result, _ = self._generate(tmp_path, monkeypatch, [], {})
        assert result.notes["sam3dDumpField"] == "false"
        assert not any(path.endswith(".npz") for path in result.diagnostics)


@pytest.mark.unit
class TestTheSam3dAdapterCarriesTheDriverStatsOntoTheRun:
    """The driver writes its measurements beside the mesh under one name the adapter also knows. They
    ride onto the execution as a diagnostic and, compacted, as a note; a run that produced a mesh but
    no measurements is still a run."""

    def test_the_driver_writes_the_file_the_adapter_reads(self):
        assert STATS_FILENAME.endswith(".json")
        assert f'"{STATS_FILENAME}"' in _sam3d_driver()

    def _generate(self, tmp_path, monkeypatch, sidecar):
        snapshot = tmp_path / "snapshot"
        (snapshot / "checkpoints").mkdir(parents=True)
        (snapshot / "checkpoints" / "pipeline.yaml").write_text("stub", encoding="utf-8")
        monkeypatch.setenv("HF_HOME", str(tmp_path))

        def fake_run_upstream(cmd, cwd, hf_home, extra_env=None, timeout=None):
            out_dir = pathlib.Path(cmd[4])
            (out_dir / "sam3d.glb").write_bytes(b"glTF")
            if sidecar is not None:
                (out_dir / STATS_FILENAME).write_text(sidecar, encoding="utf-8")

        monkeypatch.setattr("models.sam3d.run_upstream", fake_run_upstream)
        adapter = Sam3dAdapter()
        adapter._snapshot_dirs[Sam3dAdapter.CONFIG_REPO] = str(snapshot)
        result = adapter.generate(str(tmp_path / "in.png"), str(tmp_path / "work"), {})
        return result, tmp_path / "work" / "generate" / "sam3d" / STATS_FILENAME

    def test_the_stats_become_a_note_and_a_diagnostic(self, tmp_path, monkeypatch):
        stats = {"solidInterior": "off", "rawMesh": {"vertices": 3, "faces": 1},
                 "glb": {"vertices": 3, "faces": 1}, "sampler": {"ss_inference_steps": 12},
                 "upstreamCommit": "abc123"}
        result, stats_path = self._generate(tmp_path, monkeypatch, json.dumps(stats, indent=2))
        assert json.loads(result.notes["sam3dStats"]) == stats
        assert result.diagnostics == [str(stats_path)]

    def test_every_note_stays_a_string(self, tmp_path, monkeypatch):
        # The notes dict is dumped whole into one report field; a nested object there would be the
        # only one of its kind.
        result, _ = self._generate(tmp_path, monkeypatch, json.dumps({"solidInterior": "off"}))
        assert all(isinstance(value, str) for value in result.notes.values()), result.notes

    def test_the_stats_file_is_not_taken_for_a_texture_sidecar(self, tmp_path, monkeypatch):
        result, _ = self._generate(tmp_path, monkeypatch, json.dumps({"solidInterior": "off"}))
        assert result.texture_paths == []

    def test_a_missing_file_is_noted_and_the_run_still_succeeds(self, tmp_path, monkeypatch):
        result, _ = self._generate(tmp_path, monkeypatch, None)
        assert result.notes["sam3dStats"] == "missing"
        assert result.diagnostics == []
        assert result.mesh_path.endswith("sam3d.glb")

    def test_an_unreadable_file_is_noted_and_still_uploaded_for_inspection(self, tmp_path, monkeypatch):
        result, stats_path = self._generate(tmp_path, monkeypatch, "{not json")
        assert result.notes["sam3dStats"] == "unreadable"
        assert result.diagnostics == [str(stats_path)]

    def _warnings(self, monkeypatch):
        """The messages the adapter logs at warning level, as one list, with the other levels swallowed."""
        messages = []

        class Recorder:
            def warning(self, message, *args, **kwargs):
                messages.append(str(message))

            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        monkeypatch.setattr("models.sam3d.logger", Recorder())
        return messages

    def test_the_radius_the_fill_ran_at_becomes_its_own_note(self, tmp_path, monkeypatch):
        fill = {"opening": 2, "openingRequested": "auto", "openingFlippedVertices": 225,
                "flippedVertices": 225, "flippedByRadius": [0, 0, 225, 225]}
        warnings = self._warnings(monkeypatch)
        result, _ = self._generate(tmp_path, monkeypatch, json.dumps({"solidInterior": fill}))
        assert result.notes["sam3dSolidInteriorRadius"] == "2"
        assert all(isinstance(value, str) for value in result.notes.values()), result.notes
        assert warnings == []

    def test_a_search_that_flipped_nothing_is_warned_about_with_what_it_measured(self, tmp_path, monkeypatch):
        fill = {"opening": 0, "openingRequested": "auto", "openingFlippedVertices": 0, "flippedVertices": 0,
                "flippedByRadius": [0, 16, 20]}
        warnings = self._warnings(monkeypatch)
        result, _ = self._generate(tmp_path, monkeypatch, json.dumps({"solidInterior": fill}))
        assert result.notes["sam3dSolidInteriorRadius"] == "0"
        assert len(warnings) == 1
        assert "flipped nothing" in warnings[0]
        assert "up to 2" in warnings[0]
        assert "[0, 16, 20]" in warnings[0]

    def test_a_fixed_opening_that_flipped_nothing_points_at_the_setting(self, tmp_path, monkeypatch):
        fill = {"opening": 1, "openingRequested": 1, "openingFlippedVertices": 0, "flippedVertices": 0,
                "flippedByRadius": [0]}
        warnings = self._warnings(monkeypatch)
        self._generate(tmp_path, monkeypatch, json.dumps({"solidInterior": fill}))
        assert len(warnings) == 1
        assert "flipped nothing" in warnings[0]
        assert "SAM3D_SOLID_INTERIOR_OPENING" in warnings[0]

    def test_a_fill_that_handed_back_everything_the_opening_sealed_says_so(self, tmp_path, monkeypatch):
        fill = {"opening": 2, "openingRequested": "auto", "openingFlippedVertices": 40, "flippedVertices": 0,
                "flippedByRadius": [0, 0, 40]}
        warnings = self._warnings(monkeypatch)
        self._generate(tmp_path, monkeypatch, json.dumps({"solidInterior": fill}))
        assert len(warnings) == 1
        assert "flipped nothing" in warnings[0]
        assert "all 40 vertices" in warnings[0]
        assert "SAM3D_SOLID_INTERIOR_OPENING" not in warnings[0]
