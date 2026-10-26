#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the imageTo3d container's settings allow-list and per-model resolution.

``config.json`` is what an operator's template field and an asset's standing metadata are filtered
against, so a parameter absent from it is unreachable however it is supplied: the adapter reads the
key, finds nothing, and silently uses its own hardcoded default. The allow-list is two-level -- the
common settings every run accepts, plus one block per model -- and MODEL therefore has to resolve
before the settings it gates, since a key belonging to another model must not reach an adapter that
reads the same name.
"""

import importlib.util
import json
import os
import re

import pytest

from models.sam3d import Sam3dAdapter

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODELS_DIR = os.path.join(_CONTAINER_DIR, "models")

# The model keys the pipeline ships, and the default the state machine falls back to.
_MODEL_KEYS = ("sam3d", "stable3dgen", "trellis2", "triposr", "step1x3d", "hunyuan3d", "blenderagent")
_DEFAULT_MODEL = "stable3dgen"

# Tags whose value genuinely cannot be listed, so the operator types it in. Everything else accepts
# a fixed set and belongs in an enum, where the execute form shows the choices rather than leaving
# the operator to guess a spelling the container will reject. A text prompt is the expected member
# here once segmentation lands; add a tag only when its values really are open-ended.
# Tags whose value really is open-ended, so an enum could not enumerate it. PRINT_FEATURE_COLOR takes
# any #RRGGBB value; the container validates the format and rejects a malformed one.
# SAM_PROMPT is genuinely open-ended: "centre", or a point or box given as fractions of the image, so
# there is no finite value set an enum could list.
# BLENDERAGENT_GUIDANCE is prose handed to the agent.
_FREE_TEXT_TAGS = frozenset({"PRINT_FEATURE_COLOR", "SAM_PROMPT", "BLENDERAGENT_GUIDANCE"})

# Keys read by more than one adapter, resolved from the common block rather than a per-model one.
_SHARED_KEYS = ("MODEL", "BACKGROUND_REMOVAL")

# The SAM 3D Objects templates: the base one plus the variants that run the same model under other
# downstream settings, named as TestTemplatesExposeTheirModelsParameters._template() resolves them.
_SAM3D_TEMPLATE_VARIANTS = ("sam3d", "sam3d-print-ready", "sam3d-then-refine")

# Tag types whose placeholder renders a JSON value of that type and therefore takes no quotes.
_TYPED_TAG_TYPES = ("integer", "number", "boolean", "string-list")

_PLACEHOLDER_PATTERN = re.compile(r'"([A-Za-z0-9_]+)"\s*:\s*"?\{\{\s*([A-Za-z0-9_]+)\s*\}\}"?')


def _config_body_placeholders(config_body):
    """``{configKey: tagName}`` for every ``{{tag}}`` a config body substitutes, quoted or bare.

    A body carrying a bare typed placeholder is not parseable JSON until the tags are resolved, so
    the mapping is read off the text rather than off a parse.
    """
    return {key: tag for key, tag in _PLACEHOLDER_PATTERN.findall(config_body)}


def _resolved_config_body(template):
    """A template's config body with every declared tag replaced by its default, parsed as JSON.

    Mirrors what the resolver hands the container: a string or enum value is substituted inside the
    template's own quotes, and a typed value is substituted as a bare JSON literal.
    """
    body = template["configBody"]
    for field in template["tagSchema"]:
        tag = "{{" + field["tagKey"] + "}}"
        if field["type"] in _TYPED_TAG_TYPES:
            body = body.replace(tag, json.dumps(field["default"]))
        else:
            body = body.replace(tag, str(field["default"]))
    return json.loads(body)


@pytest.fixture(scope="module")
def main_module():
    """The container entry module, loaded by file (its name is ``__main__.py``)."""
    spec = importlib.util.spec_from_file_location(
        "imageto3d_container_main", os.path.join(_CONTAINER_DIR, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _adapter_source(model):
    with open(os.path.join(_MODELS_DIR, f"{model}.py"), encoding="utf-8") as handle:
        return handle.read()


def _adapter_setting_keys(model):
    """Every ``setting(params, "KEY", ...)`` key one adapter module reads."""
    return set(re.findall(r"""setting\(\s*params\s*,\s*["']([A-Za-z0-9_]+)["']""",
                          _adapter_source(model)))


def _adapter_setting_defaults(model):
    """The literal fallback each ``setting(params, "KEY", default)`` call in one adapter declares.

    Only single-literal defaults are captured; a default that is an attribute reference (a weight
    subfolder constant, say) is skipped rather than compared as text.
    """
    found = re.findall(
        r"""setting\(\s*params\s*,\s*["']([A-Za-z0-9_]+)["']\s*,\s*([^,()]+?)\s*\)""",
        _adapter_source(model))
    return {key: raw.strip().strip('"').strip("'") for key, raw in found
            if not raw.strip().startswith("self.")}


@pytest.mark.unit
class TestAllowListStructure:
    def test_config_json_is_read_by_absolute_path(self, main_module):
        """The entrypoint's working directory is not guaranteed to be the code directory."""
        assert main_module.CONFIG_PATH.is_absolute()
        assert main_module.CONFIG_PATH.is_file()

    def test_the_allow_list_has_a_common_block_and_one_block_per_model(self, main_module):
        allowed = main_module.load_allowed_settings()
        assert set(allowed) == {"common", "models"}
        assert set(allowed["models"]) == set(_MODEL_KEYS)

    def test_the_common_block_carries_the_pipeline_wide_settings(self, main_module):
        allowed = main_module.load_allowed_settings()
        assert set(allowed["common"]) == {
            "MODEL", "IMAGE_CLEANUP", "BACKGROUND_REMOVAL", "SAM_PROMPT",
            "FRAMING_RATIO", "FRAMING_BACKGROUND",
            "PROJECT_PHOTO_COLOUR", "PROJECT_PHOTO_POSE",
            "MESH_CLEANUP", "PRINT_GATE_POLICY", "TARGET_FACE_COUNT", "TARGET_SIZE_MM",
            "PRINT_FEATURES", "PRINT_FEATURE_JOIN", "PRINT_FEATURE_COLOR",
            "LOOP_DIAMETER_MM", "LOOP_THICKNESS_MM", "BASE_DIAMETER_MM", "BASE_HEIGHT_MM",
            "EXPORT_FORMATS", "REPAIR_BACKENDS", "MIN_VOLUME_RETENTION", "CLOSE_HOLE_MAX_EDGES",
            "NEGLIGIBLE_FRAGMENT_FRACTION",
            "BENCHMARK_REPORT", "INVALIDATE_MODEL_CACHE", "PASS_THROUGH_INPUTS",
        }

    def test_no_model_block_redeclares_a_common_key(self, main_module):
        """A per-model copy of a common key would shadow the common default for that model only."""
        allowed = main_module.load_allowed_settings()
        for model, block in allowed["models"].items():
            assert not set(block) & set(allowed["common"]), model

    def test_every_model_block_key_is_namespaced_to_its_model(self, main_module):
        allowed = main_module.load_allowed_settings()
        for model, block in allowed["models"].items():
            prefix = f"{model.upper()}_"
            assert all(key.startswith(prefix) for key in block), (model, sorted(block))


@pytest.mark.unit
class TestEveryAdapterParameterIsReachable:
    """The defect this closes: a parameter the adapter reads but the allow-list omits is dead code
    -- ``resolve_settings`` never emits the key, so ``setting()`` falls back to its hardcoded
    default and neither a template field nor asset metadata can move it."""

    @pytest.mark.parametrize("model", _MODEL_KEYS)
    def test_every_key_the_adapter_reads_is_in_the_allow_list(self, main_module, model):
        allowed = main_module.load_allowed_settings()
        accepted = main_module.allowed_for_model(allowed, model)
        unreachable = _adapter_setting_keys(model) - set(accepted)
        assert unreachable == set(), f"{model} reads unreachable settings: {sorted(unreachable)}"

    @pytest.mark.parametrize("model", _MODEL_KEYS)
    def test_the_allow_list_declares_no_key_the_adapter_never_reads(self, main_module, model):
        """An exposed knob no adapter reads is a field an operator can set to no effect."""
        allowed = main_module.load_allowed_settings()
        block = allowed["models"][model]
        assert set(block) <= _adapter_setting_keys(model), sorted(
            set(block) - _adapter_setting_keys(model))

    @pytest.mark.parametrize("model", _MODEL_KEYS)
    def test_a_shared_key_is_resolved_from_the_common_block(self, main_module, model):
        accepted = main_module.allowed_for_model(main_module.load_allowed_settings(), model)
        for key in _SHARED_KEYS:
            if key in _adapter_setting_keys(model):
                assert key in accepted


@pytest.mark.unit
class TestAllowedForModel:
    def test_a_run_accepts_the_common_keys_plus_its_own(self, main_module):
        allowed = main_module.load_allowed_settings()
        accepted = main_module.allowed_for_model(allowed, "triposr")
        assert "TARGET_FACE_COUNT" in accepted
        assert "TRIPOSR_MC_RESOLUTION" in accepted

    def test_another_models_keys_are_not_accepted(self, main_module):
        allowed = main_module.load_allowed_settings()
        accepted = main_module.allowed_for_model(allowed, "triposr")
        assert not any(key.startswith(("TRELLIS2_", "SAM3D_", "STEP1X3D_")) for key in accepted)

    def test_an_unknown_model_accepts_the_common_keys_only(self, main_module):
        allowed = main_module.load_allowed_settings()
        assert main_module.allowed_for_model(allowed, "not-a-model") == allowed["common"]


@pytest.mark.unit
class TestResolveSettings:
    def test_every_accepted_key_resolves_to_its_default_when_nothing_is_supplied(self, main_module):
        allowed = main_module.load_allowed_settings()
        settings = main_module.resolve_settings(allowed, {}, "trellis2")
        assert set(settings) == set(main_module.allowed_for_model(allowed, "trellis2"))
        assert settings["PRINT_GATE_POLICY"] == "failClosed"
        assert settings["EXPORT_FORMATS"] == "glb,obj,stl"
        assert settings["TRELLIS2_TEXTURE_SIZE"] == "2048"

    @pytest.mark.parametrize("model", _MODEL_KEYS)
    def test_a_defaulted_value_matches_the_adapters_own_hardcoded_default(self, main_module, model):
        """The allow-list defaults are taken from the adapter code, so a run that sets nothing
        behaves exactly as it did when the adapter's own fallback was the only value."""
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(), {}, model)
        for key, adapter_default in _adapter_setting_defaults(model).items():
            if _is_number(adapter_default):
                assert float(settings[key]) == float(adapter_default), key
            else:
                assert settings[key] == adapter_default, key

    def test_a_per_model_parameter_supplied_in_the_configuration_reaches_the_settings(
            self, main_module):
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(), {"TRELLIS2_SEED": "42"}, "trellis2")
        assert settings["TRELLIS2_SEED"] == "42"

    def test_another_models_parameter_is_dropped_rather_than_leaking_in(self, main_module):
        """A TRELLIS2_* value standing on the asset must not reach a TripoSR run."""
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(),
            {"TRELLIS2_SEED": "42"}, "triposr")
        assert "TRELLIS2_SEED" not in settings
        assert "TRELLIS2_TEXTURE_SIZE" not in settings

    def test_an_unlisted_key_is_dropped(self, main_module):
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(),
            {"MESH_CLEANUP": "false", "NOT_A_SETTING": "x"}, "trellis2")
        assert "NOT_A_SETTING" not in settings
        assert settings["MESH_CLEANUP"] == "false"

    def test_a_blank_value_everywhere_resolves_to_the_default(self, main_module):
        """A headless fileUpload trigger fills no form, and an optional field submits ""."""
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(), {"TRELLIS2_SEED": "   "}, "trellis2")
        assert settings["TRELLIS2_SEED"] == "1"

    def test_the_resolved_model_is_written_back_into_the_settings(self, main_module):
        """Generation reads MODEL out of the settings, so it must carry the validated key rather
        than whatever blank or aliased value was supplied."""
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(), {"MODEL": ""}, "sam3d")
        assert settings["MODEL"] == "sam3d"


@pytest.mark.unit
class TestATypedConfigurationValueResolves:
    """A typed tag's placeholder is bare in the config body, so the value reaches the container as a
    JSON number or boolean rather than as text. Resolution and every adapter read it through
    ``str()``-based helpers, so a typed value must resolve to the same effective setting the string
    form did -- and a blank or absent one must still fall back to the default."""

    @pytest.mark.parametrize("key, supplied, expected", [
        ("TARGET_FACE_COUNT", 200000, "200000"),
        ("TARGET_FACE_COUNT", 0, "0"),
        ("TARGET_SIZE_MM", 42.5, "42.5"),
        ("TARGET_SIZE_MM", 0.0, "0.0"),
        ("TRELLIS2_SPARSE_CFG_STRENGTH", 7.5, "7.5"),
        ("TRELLIS2_SEED", 12345, "12345"),
    ])
    def test_a_json_number_resolves_to_its_text_form(
            self, main_module, key, supplied, expected):
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(), {key: supplied}, "trellis2")
        assert settings[key] == expected

    def test_a_numeric_value_resolves_to_the_same_setting_as_its_string_form(self, main_module):
        allowed = main_module.load_allowed_settings()
        numeric = main_module.resolve_settings(allowed, {"TARGET_FACE_COUNT": 150000}, "trellis2")
        text = main_module.resolve_settings(allowed, {"TARGET_FACE_COUNT": "150000"}, "trellis2")
        assert numeric["TARGET_FACE_COUNT"] == text["TARGET_FACE_COUNT"] == "150000"

    def test_a_numeric_zero_is_supplied_rather_than_read_as_absent(self, main_module):
        """0 is the documented 'leave it alone' value for both budgets, and a falsy-value check
        would drop it in favour of the non-zero default."""
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(), {"TARGET_FACE_COUNT": 0, "TARGET_SIZE_MM": 0}, "trellis2")
        assert settings["TARGET_FACE_COUNT"] == "0"
        assert settings["TARGET_SIZE_MM"] == "0"

    def test_an_absent_typed_value_falls_back_to_the_default(self, main_module):
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(), {}, "trellis2")
        assert settings["TARGET_FACE_COUNT"] == "200000"
        assert settings["TRELLIS2_SPARSE_CFG_STRENGTH"] == "7.5"

    def test_a_blank_typed_value_falls_back_to_the_default(self, main_module):
        """A tag left blank on the execute form submits "", which must not become the setting."""
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(),
            {"TARGET_FACE_COUNT": "", "TRELLIS2_SEED": "   "}, "trellis2")
        assert settings["TARGET_FACE_COUNT"] == "200000"
        assert settings["TRELLIS2_SEED"] == "1"

    @pytest.mark.parametrize("supplied, expected", [
        (200000, "200000"),
        (0, "0"),
        (0.85, "0.85"),
        (0.0, "0.0"),
        (True, "True"),
        ("150000", "150000"),
    ])
    def test_the_adapter_helper_takes_a_typed_value_as_its_text_form(self, supplied, expected):
        """A direct container invocation hands the adapters the config body's own parsed values, so
        ``setting`` sees the JSON type rather than the text resolution would have produced."""
        from models.base import setting
        assert setting({"K": supplied}, "K", "fallback") == expected

    @pytest.mark.parametrize("supplied", [None, "", "   "])
    def test_the_adapter_helper_falls_back_for_a_blank_value(self, supplied):
        from models.base import setting
        assert setting({"K": supplied}, "K", "fallback") == "fallback"

    def test_the_adapter_helper_falls_back_for_an_absent_value(self):
        from models.base import setting
        assert setting({}, "K", "fallback") == "fallback"
        assert setting(None, "K", "fallback") == "fallback"

    def test_a_typed_value_survives_the_adapters_own_numeric_cast(self):
        """Each adapter wraps its ``setting`` call in ``int()`` / ``float()``, which is where a
        mis-handled JSON number would surface."""
        from models.base import setting
        assert int(setting({"TRELLIS2_SEED": 12345}, "TRELLIS2_SEED", 1)) == 12345
        assert float(setting({"TARGET_SIZE_MM": 42.5}, "TARGET_SIZE_MM", 0.0)) == 42.5
        assert int(setting({}, "TRELLIS2_SEED", 1)) == 1

    @pytest.mark.parametrize("model", _MODEL_KEYS)
    def test_a_body_resolved_from_typed_defaults_reproduces_every_default_setting(
            self, main_module, model):
        """End to end: the template's own defaults, substituted as the JSON types their tags
        declare, resolve to exactly the settings a run that supplied nothing gets."""
        template_path = os.path.join(
            os.path.dirname(_CONTAINER_DIR), "vamsSchema", "templates",
            f"image-to-3d-{model}.json")
        with open(template_path, encoding="utf-8") as handle:
            template = json.load(handle)
        allowed = main_module.load_allowed_settings()
        params = _resolved_config_body(template)
        resolved = main_module.resolve_settings(allowed, params, model)
        defaulted = main_module.resolve_settings(allowed, {}, model)
        for field in template["tagSchema"]:
            key = field["tagKey"]
            if _is_number(defaulted[key]):
                assert float(resolved[key]) == float(defaulted[key]), key
            else:
                assert resolved[key] == defaulted[key], key


@pytest.mark.unit
class TestModelResolvedFirst:
    """MODEL decides which per-model keys a run accepts, so it cannot be resolved in the same pass
    as the settings it gates."""

    def test_the_model_comes_from_the_input_configuration(self, main_module):
        allowed = main_module.load_allowed_settings()
        assert main_module.resolve_model_key(allowed, {"MODEL": "sam3d"}, {}) == "sam3d"

    def test_a_blank_model_falls_back_to_the_definition_then_the_default(self, main_module):
        allowed = main_module.load_allowed_settings()
        assert main_module.resolve_model_key(allowed, {}, {"model": "sam3d"}) == "sam3d"
        assert main_module.resolve_model_key(allowed, {}, {}) == _DEFAULT_MODEL

    def test_an_unsupported_model_raises(self, main_module):
        allowed = main_module.load_allowed_settings()
        with pytest.raises(ValueError, match="Unsupported image-to-3D model"):
            main_module.resolve_model_key(allowed, {"MODEL": "not-a-model"}, {})

    def test_the_model_chosen_selects_which_per_model_keys_resolve(self, main_module):
        """One supplied map, two models: each run sees only its own parameters."""
        allowed = main_module.load_allowed_settings()
        params = {"TRELLIS2_SEED": "42", "TRIPOSR_MC_RESOLUTION": "320"}

        def resolved(model):
            key = main_module.resolve_model_key(allowed, {"MODEL": model}, {})
            return main_module.resolve_settings(allowed, params, key)

        trellis = resolved("trellis2")
        triposr = resolved("triposr")
        assert trellis["TRELLIS2_SEED"] == "42" and "TRIPOSR_MC_RESOLUTION" not in trellis
        assert triposr["TRIPOSR_MC_RESOLUTION"] == "320" and "TRELLIS2_SEED" not in triposr

    def test_resolve_model_still_validates_a_direct_container_invocation(self, main_module):
        assert main_module.resolve_model({"MODEL": "step1x3d"}, {}) == "step1x3d"
        with pytest.raises(ValueError, match="Unsupported image-to-3D model"):
            main_module.resolve_model({"MODEL": "not-a-model"}, {})


@pytest.mark.unit
class TestTemplatesExposeTheirModelsParameters:
    """A parameter in the allow-list but not in a tagSchema is settable only through asset
    metadata; the model's own tuning knobs belong on the execute form."""

    _TEMPLATE_DIR = os.path.join(
        os.path.dirname(_CONTAINER_DIR), "vamsSchema", "templates")

    # Deployment-level backend choices: settable via asset metadata, deliberately not offered as a
    # form field an operator would have to reason about.
    _NOT_IN_FORM = {"TRELLIS2_ATTN_BACKEND", "TRELLIS2_SPCONV_ALGO",
                    "STABLE3DGEN_ATTN_BACKEND", "STABLE3DGEN_SPARSE_BACKEND",
                    "STABLE3DGEN_SPCONV_ALGO", "SAM3D_CONFIG_RELPATH",
                    "TRELLIS2_MAX_NUM_TOKENS", "TRELLIS2_RASTERIZER_FACE_LIMIT",
                    "STEP1X3D_GEOMETRY_SUBFOLDER", "STEP1X3D_TEXTURE_SUBFOLDER",
                    # The geometry label conditions the Label checkpoint, which is selected by the
                    # off-form subfolder setting above; offering half of that pair on the form would
                    # give an operator a field that does nothing on the default checkpoint.
                    "STEP1X3D_LABEL_SYMMETRY", "STEP1X3D_LABEL_EDGE_TYPE",
                    # The GPU-memory threshold the texture stage's "auto" mode compares against. The
                    # MODE is on the form; the number it switches at is a property of the hardware, not
                    # a choice an operator can reason about per run.
                    "STEP1X3D_TEXTURE_OFFLOAD_BELOW_GIB",
                    # Which checkpoint inside the weights repo the shape stage opens, and its precision.
                    # Both name an artefact rather than describe a choice, and the wrong value is a load
                    # failure rather than a different result.
                    "HUNYUAN3D_SHAPE_SUBFOLDER", "HUNYUAN3D_PAINT_SUBFOLDER",
                    "HUNYUAN3D_VARIANT",
                    # Query points per batch in the surface extractor: it trades GPU memory against time
                    # and changes nothing about the mesh, so there is nothing for an operator to weigh.
                    "HUNYUAN3D_NUM_CHUNKS",
                    # The loopback port the model bridge listens on inside the container. Nothing an
                    # operator can reason about per run.
                    "BLENDERAGENT_BRIDGE_PORT",
                    # How wide a puncture in the surface the interior fill seals. The switch is on the
                    # form; this tunes what it does and is set from a run's recorded stats, not chosen
                    # per run.
                    "SAM3D_SOLID_INTERIOR_OPENING",
                    # Whether the driver writes the extractor's vertex grid to disk: a diagnostic for
                    # working on the fill away from the GPU, not a choice about the mesh.
                    "SAM3D_DUMP_FIELD"}

    def _template(self, model):
        path = os.path.join(self._TEMPLATE_DIR, f"image-to-3d-{model}.json")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    # The refit's settings are on the two refit templates, which TestTheRefitTemplates checks. The agent's
    # own template runs the agent alone and offers none of them.
    _REFIT_ONLY = {"BLENDERAGENT_MODE", "BLENDERAGENT_REFIT_REBUILD", "BLENDERAGENT_REFIT_RESOLUTION",
                   "BLENDERAGENT_REFIT_OUTLINE_FIT", "BLENDERAGENT_REFIT_RELIEF",
                   "BLENDERAGENT_REFIT_RELIEF_STRENGTH", "BLENDERAGENT_REFIT_RETOPO"}

    @pytest.mark.parametrize("model", _MODEL_KEYS)
    def test_the_tag_schema_exposes_every_per_model_parameter_meant_for_the_form(
            self, main_module, model):
        allowed = main_module.load_allowed_settings()
        expected = set(allowed["models"][model]) - self._NOT_IN_FORM - self._REFIT_ONLY
        declared = {field["tagKey"] for field in self._template(model)["tagSchema"]}
        assert expected <= declared, sorted(expected - declared)

    @pytest.mark.parametrize("model", _MODEL_KEYS)
    def test_the_config_body_passes_every_declared_tag_through(self, main_module, model):
        template = self._template(model)
        placeholders = _config_body_placeholders(template["configBody"])
        for field in template["tagSchema"]:
            assert placeholders.get(field["tagKey"]) == field["tagKey"], field["tagKey"]

    @pytest.mark.parametrize("model", _MODEL_KEYS)
    def test_a_typed_placeholder_is_bare_and_a_text_placeholder_is_quoted(self, main_module, model):
        """Quoting is type-driven: an integer/number/boolean/string-list tag renders a JSON value of
        that type, so its placeholder is the whole value; a string/enum tag renders text and sits
        inside the string it fills. The reverse is refused on save."""
        template = self._template(model)
        for field in template["tagSchema"]:
            key, tag = field["tagKey"], "{{" + field["tagKey"] + "}}"
            if field["type"] in _TYPED_TAG_TYPES:
                assert f'": {tag}' in template["configBody"], key
                assert f'"{tag}"' not in template["configBody"], key
            else:
                assert f'"{tag}"' in template["configBody"], key

    @pytest.mark.parametrize("model", _MODEL_KEYS)
    def test_the_config_body_names_the_templates_own_model(self, main_module, model):
        assert _resolved_config_body(self._template(model))["MODEL"] == model

    @pytest.mark.parametrize("model", _MODEL_KEYS)
    def test_the_config_body_carries_no_other_models_parameter(self, main_module, model):
        allowed = main_module.load_allowed_settings()
        accepted = main_module.allowed_for_model(allowed, model)
        body = _resolved_config_body(self._template(model))
        assert set(body) <= set(accepted), sorted(set(body) - set(accepted))

    @pytest.mark.parametrize("model", _MODEL_KEYS)
    def test_the_body_resolves_to_valid_json_carrying_each_default_as_its_declared_type(
            self, main_module, model):
        """A body whose typed placeholders are bare is only valid JSON once resolved, and each value
        arrives as the JSON type its tag declares rather than as text."""
        template = self._template(model)
        body = _resolved_config_body(template)
        for field in template["tagSchema"]:
            value = body[field["tagKey"]]
            if field["type"] == "integer":
                assert isinstance(value, int) and not isinstance(value, bool), field["tagKey"]
            elif field["type"] == "number":
                assert isinstance(value, (int, float)) and not isinstance(value, bool), \
                    field["tagKey"]
            else:
                assert isinstance(value, str), field["tagKey"]
            assert value == field["default"], field["tagKey"]

    @pytest.mark.parametrize("model", _MODEL_KEYS)
    def test_every_declared_tag_is_optional_and_carries_a_default(self, main_module, model):
        """A headless trigger fills no form, and an integer/number/boolean tag with no default is
        absent from the filled map, which makes its placeholder fail resolution."""
        for field in self._template(model)["tagSchema"]:
            assert field.get("required") is False, field["tagKey"]
            assert field.get("default") is not None, field["tagKey"]

    @pytest.mark.parametrize("model", _MODEL_KEYS)
    def test_every_enum_declares_a_non_empty_value_set_containing_its_default(
            self, main_module, model):
        """An optional enum submits "" when left alone; a blank counts as absent, so the declared
        default is what fills the tag and it has to be one of the offered values."""
        for field in self._template(model)["tagSchema"]:
            if field.get("type") != "enum":
                continue
            assert field.get("enumValues"), field["tagKey"]
            assert str(field["default"]) in field["enumValues"], field["tagKey"]

    @pytest.mark.parametrize("model", _MODEL_KEYS)
    def test_no_field_with_a_fixed_value_set_is_left_as_free_text(self, main_module, model):
        """A choice between known values has to be an enum, not a string the operator types.

        A free-text field offers no hint of what it accepts, so a plausible-looking value the
        container does not recognise is only caught after a GPU run -- or, for a value that parses to
        an empty selection, not caught at all. A genuinely open-ended field goes in _FREE_TEXT_TAGS.
        """
        offenders = [
            field["tagKey"] for field in self._template(model)["tagSchema"]
            if field.get("type") == "string" and field["tagKey"] not in _FREE_TEXT_TAGS
        ]
        assert not offenders, (
            f"these tags accept a fixed set of values and must be declared as enum so the execute "
            f"form lists them, or added to _FREE_TEXT_TAGS if they really are open-ended: "
            f"{offenders}")

    @pytest.mark.parametrize("model", _MODEL_KEYS)
    def test_every_enum_value_is_distinct(self, main_module, model):
        """A duplicated option renders twice and makes the list read as though it has more choices."""
        for field in self._template(model)["tagSchema"]:
            values = field.get("enumValues") or []
            assert len(values) == len(set(values)), field["tagKey"]

    @pytest.mark.parametrize("variant", _SAM3D_TEMPLATE_VARIANTS)
    def test_every_sam3d_variant_offers_the_interior_fill_on_by_default(self, variant):
        """The print-ready and then-refine templates are the same model under other downstream
        settings; the fill is a property of the reconstruction, so each of them offers it."""
        template = self._template(variant)
        fields = {field["tagKey"]: field for field in template["tagSchema"]}
        switch = fields["SAM3D_SOLID_INTERIOR"]
        assert switch["type"] == "enum"
        assert switch["default"] == "true"
        assert sorted(switch["enumValues"]) == ["false", "true"]
        assert switch["required"] is False
        placeholders = _config_body_placeholders(template["configBody"])
        assert placeholders.get("SAM3D_SOLID_INTERIOR") == "SAM3D_SOLID_INTERIOR"

    @pytest.mark.parametrize("variant", _SAM3D_TEMPLATE_VARIANTS)
    @pytest.mark.parametrize("key", ["SAM3D_SOLID_INTERIOR_OPENING", "SAM3D_DUMP_FIELD"])
    def test_the_fill_opening_and_the_field_dump_stay_off_every_sam3d_form(self, variant, key):
        template = self._template(variant)
        declared = {field["tagKey"] for field in template["tagSchema"]}
        placeholders = _config_body_placeholders(template["configBody"])
        assert key not in declared
        assert key not in placeholders

    def test_exactly_one_template_is_the_default(self):
        defaults = [model for model in _MODEL_KEYS if self._template(model).get("isDefault")]
        assert defaults == [_DEFAULT_MODEL]


def _is_number(text):
    try:
        float(text)
        return True
    except ValueError:
        return False


def _sam3d_run(tmp_path, monkeypatch, settings, launches=None):
    """The driver payload the sam3d adapter sends and the notes it returns, for one resolved settings dict.

    The upstream run is replaced by a stand-in that records the payload and writes the mesh the
    adapter then looks for, so the chain is exercised up to the process boundary without a GPU. Each
    launch of the stand-in is appended to ``launches`` when a list is given.
    """
    snapshot = tmp_path / "snapshot"
    (snapshot / "checkpoints").mkdir(parents=True)
    (snapshot / "checkpoints" / "pipeline.yaml").write_text("stub", encoding="utf-8")
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    payload = {}

    def fake_run_upstream(cmd, cwd, hf_home, extra_env=None, timeout=None):
        if launches is not None:
            launches.append(list(cmd))
        payload.update(json.loads(cmd[-1]))
        with open(os.path.join(cmd[4], "sam3d.glb"), "wb") as handle:
            handle.write(b"glTF")

    monkeypatch.setattr("models.sam3d.run_upstream", fake_run_upstream)
    adapter = Sam3dAdapter()
    adapter._snapshot_dirs[Sam3dAdapter.CONFIG_REPO] = str(snapshot)
    result = adapter.generate(str(tmp_path / "in.png"), str(tmp_path / "work"), settings)
    return payload, result.notes


@pytest.mark.unit
class TestTheInteriorFillSettingsReachTheSam3dDriver:
    """SAM3D_SOLID_INTERIOR and its opening travel config.json -> resolve_settings -> adapter -> driver
    payload, and arrive typed: the driver branches on a bool and takes the opening as the word ``auto``
    or an int."""

    def test_the_allow_list_defaults_turn_the_fill_on(self, main_module, tmp_path, monkeypatch):
        settings = main_module.resolve_settings(main_module.load_allowed_settings(), {}, "sam3d")
        payload, _ = _sam3d_run(tmp_path, monkeypatch, settings)
        assert payload["solid_interior"] is True
        assert payload["solid_interior_opening"] == "auto"

    def test_supplied_values_arrive_typed(self, main_module, tmp_path, monkeypatch):
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(),
            {"SAM3D_SOLID_INTERIOR": "true", "SAM3D_SOLID_INTERIOR_OPENING": "2"},
            "sam3d")
        payload, _ = _sam3d_run(tmp_path, monkeypatch, settings)
        assert payload["solid_interior"] is True
        assert payload["solid_interior_opening"] == 2

    @pytest.mark.parametrize("supplied, expected", [
        ("True", True), ("TRUE", True), (" true ", True), ("false", False), ("", True),
    ])
    def test_the_switch_reads_like_the_other_boolean_settings(
            self, main_module, tmp_path, monkeypatch, supplied, expected):
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(), {"SAM3D_SOLID_INTERIOR": supplied}, "sam3d")
        payload, _ = _sam3d_run(tmp_path, monkeypatch, settings)
        assert payload["solid_interior"] is expected

    def test_the_values_the_driver_ran_with_are_noted_on_the_result(
            self, main_module, tmp_path, monkeypatch):
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(),
            {"SAM3D_SOLID_INTERIOR": "true", "SAM3D_SOLID_INTERIOR_OPENING": "2"}, "sam3d")
        _, notes = _sam3d_run(tmp_path, monkeypatch, settings)
        assert notes["sam3dSolidInterior"] == "true"
        assert notes["sam3dSolidInteriorOpening"] == "2"

    def test_the_default_opening_is_noted_as_the_word_the_driver_received(
            self, main_module, tmp_path, monkeypatch):
        settings = main_module.resolve_settings(main_module.load_allowed_settings(), {}, "sam3d")
        _, notes = _sam3d_run(tmp_path, monkeypatch, settings)
        assert notes["sam3dSolidInteriorOpening"] == "auto"

    @pytest.mark.parametrize("value", ["auto", " Auto ", "AUTO"])
    def test_the_word_auto_arrives_lower_cased(self, main_module, tmp_path, monkeypatch, value):
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(), {"SAM3D_SOLID_INTERIOR_OPENING": value}, "sam3d")
        payload, _ = _sam3d_run(tmp_path, monkeypatch, settings)
        assert payload["solid_interior_opening"] == "auto"

    def test_a_zero_opening_is_a_radius_of_zero(self, main_module, tmp_path, monkeypatch):
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(), {"SAM3D_SOLID_INTERIOR_OPENING": "0"}, "sam3d")
        payload, _ = _sam3d_run(tmp_path, monkeypatch, settings)
        assert payload["solid_interior_opening"] == 0

    @pytest.mark.parametrize("value", ["-1", "wide", "1.5"])
    def test_anything_else_is_refused_before_the_driver_runs(self, main_module, tmp_path, monkeypatch, value):
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(), {"SAM3D_SOLID_INTERIOR_OPENING": value}, "sam3d")
        launches = []
        with pytest.raises(ValueError, match="SAM3D_SOLID_INTERIOR_OPENING"):
            _sam3d_run(tmp_path, monkeypatch, settings, launches)
        assert launches == []


@pytest.mark.unit
class TestTheFieldDumpSettingReachesTheSam3dDriver:
    """SAM3D_DUMP_FIELD travels the same road as the fill switch, arrives as a bool, and is noted."""

    def test_the_allow_list_default_leaves_the_dump_off(self, main_module, tmp_path, monkeypatch):
        settings = main_module.resolve_settings(main_module.load_allowed_settings(), {}, "sam3d")
        payload, notes = _sam3d_run(tmp_path, monkeypatch, settings)
        assert payload["dump_field"] is False
        assert notes["sam3dDumpField"] == "false"

    @pytest.mark.parametrize("supplied, expected", [
        ("True", True), ("TRUE", True), (" true ", True), ("false", False), ("", False),
    ])
    def test_the_switch_reads_like_the_other_boolean_settings(
            self, main_module, tmp_path, monkeypatch, supplied, expected):
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(), {"SAM3D_DUMP_FIELD": supplied}, "sam3d")
        payload, notes = _sam3d_run(tmp_path, monkeypatch, settings)
        assert payload["dump_field"] is expected
        assert notes["sam3dDumpField"] == str(expected).lower()

    def test_the_dump_does_not_switch_the_fill_on(self, main_module, tmp_path, monkeypatch):
        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(),
            {"SAM3D_DUMP_FIELD": "true", "SAM3D_SOLID_INTERIOR": "false"}, "sam3d")
        payload, _ = _sam3d_run(tmp_path, monkeypatch, settings)
        assert payload["dump_field"] is True
        assert payload["solid_interior"] is False


# The Blender agent's refit forms and the mode each one runs. Each sits in both bundles.
_REFIT_TEMPLATES = {"image-to-3d-blenderagent-refit": "refit",
                    "image-to-3d-blenderagent-refit-agent": "refit-agent"}
_REFIT_CASES = [(bundle, name) for bundle in ("vamsSchema", "vamsSchema-refine") for name in _REFIT_TEMPLATES]


@pytest.mark.unit
class TestTheRefitTemplates:
    """The refit form runs the scripted refit alone and offers none of the agent's settings; the refit-agent
    form runs the refit and then the agent and offers both. Both are in the main bundle, for a run on its own,
    and in the refine bundle, for the reconstruct-then-refine workflow's second step."""

    _AGENT_TAGS = {"BLENDERAGENT_GUIDANCE", "BLENDERAGENT_MAX_ITERATIONS", "BLENDERAGENT_MAX_MINUTES",
                   "BLENDERAGENT_REASONING_EFFORT", "BLENDERAGENT_RENDER_ENGINE", "BLENDERAGENT_RENDER_SAMPLES"}
    _REFIT_TAGS = {"BLENDERAGENT_REFIT_REBUILD": "none", "BLENDERAGENT_REFIT_RESOLUTION": 0.004,
                   "BLENDERAGENT_REFIT_OUTLINE_FIT": "true", "BLENDERAGENT_REFIT_RELIEF": "true",
                   "BLENDERAGENT_REFIT_RELIEF_STRENGTH": 0.01, "BLENDERAGENT_REFIT_RETOPO": "decimate"}

    @staticmethod
    def _load(bundle, name):
        path = os.path.join(os.path.dirname(_CONTAINER_DIR), bundle, "templates", f"{name}.json")
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    @pytest.mark.parametrize("bundle, name", _REFIT_CASES)
    def test_the_body_names_the_agents_model_and_the_forms_mode(self, bundle, name):
        body = _resolved_config_body(self._load(bundle, name))
        assert body["MODEL"] == "blenderagent"
        assert body["BLENDERAGENT_MODE"] == _REFIT_TEMPLATES[name]

    @pytest.mark.parametrize("bundle, name", _REFIT_CASES)
    def test_every_tag_reaches_the_body_quoted_by_its_type(self, bundle, name):
        template = self._load(bundle, name)
        placeholders = _config_body_placeholders(template["configBody"])
        for field in template["tagSchema"]:
            key, tag = field["tagKey"], "{{" + field["tagKey"] + "}}"
            assert placeholders.get(key) == key, key
            if field["type"] in _TYPED_TAG_TYPES:
                assert f'": {tag}' in template["configBody"], key
                assert f'"{tag}"' not in template["configBody"], key
            else:
                assert f'"{tag}"' in template["configBody"], key

    @pytest.mark.parametrize("bundle, name", _REFIT_CASES)
    def test_the_body_carries_only_keys_a_blender_agent_run_accepts(self, main_module, bundle, name):
        accepted = main_module.allowed_for_model(main_module.load_allowed_settings(), "blenderagent")
        body = _resolved_config_body(self._load(bundle, name))
        assert set(body) <= set(accepted), sorted(set(body) - set(accepted))

    @pytest.mark.parametrize("bundle, name", _REFIT_CASES)
    def test_the_refit_tags_offer_the_allow_lists_defaults(self, main_module, bundle, name):
        allowed = main_module.load_allowed_settings()["models"]["blenderagent"]
        fields = {field["tagKey"]: field for field in self._load(bundle, name)["tagSchema"]}
        for key, default in self._REFIT_TAGS.items():
            assert fields[key]["default"] == default, key
            assert allowed[key] == str(default), key

    @pytest.mark.parametrize("bundle, name", _REFIT_CASES)
    def test_only_the_refit_agent_form_offers_the_agents_settings(self, bundle, name):
        declared = {field["tagKey"] for field in self._load(bundle, name)["tagSchema"]}
        assert set(self._REFIT_TAGS) <= declared
        if _REFIT_TEMPLATES[name] == "refit":
            assert not declared & self._AGENT_TAGS, sorted(declared & self._AGENT_TAGS)
        else:
            assert self._AGENT_TAGS <= declared, sorted(self._AGENT_TAGS - declared)

    @pytest.mark.parametrize("bundle, name", _REFIT_CASES)
    def test_the_forms_defaults_resolve_to_the_settings_the_adapter_checks(self, main_module, bundle, name):
        from models.blenderagent import resolve_mode, resolve_refit_settings

        settings = main_module.resolve_settings(
            main_module.load_allowed_settings(), _resolved_config_body(self._load(bundle, name)), "blenderagent")
        assert resolve_mode(settings) == _REFIT_TEMPLATES[name]
        assert resolve_refit_settings(settings) == resolve_refit_settings({})

    @pytest.mark.parametrize("name", list(_REFIT_TEMPLATES))
    def test_both_bundles_carry_the_same_form_and_neither_makes_it_the_default(self, name):
        main = self._load("vamsSchema", name)
        assert main == self._load("vamsSchema-refine", name)
        assert main["isDefault"] is False

    @pytest.mark.parametrize("bundle, name", _REFIT_CASES)
    def test_every_tag_is_optional_with_a_default_and_every_fixed_set_is_an_enum(self, bundle, name):
        for field in self._load(bundle, name)["tagSchema"]:
            assert field.get("required") is False and field.get("default") is not None, field["tagKey"]
            if field["type"] == "enum":
                assert str(field["default"]) in field["enumValues"], field["tagKey"]
            assert field["type"] != "string" or field["tagKey"] in _FREE_TEXT_TAGS, field["tagKey"]
