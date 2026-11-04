# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Multi-view input: which view is which, and what happens when a model cannot use several.

Both halves of this fail SILENTLY if they are wrong, which is why they are tested rather than trusted.

A wrong view assignment produces a confident reconstruction conditioned on the wrong faces of the subject.
Nothing errors, the print gate passes, and the only symptom is a mesh that is subtly wrong in a way no
number in the report can show. That is the argument for assigning by FILENAME rather than by position:
position is decided by whatever ordering the manifest or a directory listing happens to give, `back.png`
sorts before `front.png`, and an operator who lists views in another order gets no signal at all.

A model that cannot read several views must REFUSE them, not ignore them. Reconstructing from the first of
four supplied views is also a wrong answer that looks like a right one — the run succeeds, the output is
plausible, and the three views the operator supplied and paid to prepare are silently discarded.
"""

import importlib.util
import os

import pytest

from models.hunyuan3d import VIEW_NAMES, Hunyuan3dAdapter, assign_views
from models.sam3d import Sam3dAdapter
from models.stable3dgen import Stable3DGenAdapter
from models.step1x3d import Step1X3dAdapter
from models.trellis2 import Trellis2Adapter
from models.triposr import TripoSRAdapter

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SINGLE_VIEW_ADAPTERS = (Sam3dAdapter, Stable3DGenAdapter, Step1X3dAdapter, Trellis2Adapter,
                         TripoSRAdapter)


@pytest.mark.unit
class TestViewAssignment:
    def test_one_image_is_the_front_view_and_needs_no_token(self):
        """A single-photograph run is what this pipeline has always done; it must not now need renaming."""
        assert assign_views(["/tmp/work/user_supplied_nobg_framed.png"]) == {
            "front": "/tmp/work/user_supplied_nobg_framed.png"}

    def test_each_view_is_taken_from_its_own_filename(self):
        paths = [f"/tmp/cat_{name}.png" for name in VIEW_NAMES]
        assigned = assign_views(paths)
        assert set(assigned) == set(VIEW_NAMES)
        for name in VIEW_NAMES:
            assert assigned[name].endswith(f"_{name}.png")

    def test_the_order_the_files_arrive_in_does_not_change_the_assignment(self):
        """The whole point of assigning by name: ordering cannot silently rotate the subject.

        `back` sorts before `front` alphabetically, so a positional scheme keyed on a sorted listing would
        map these two sets differently while both look correct.
        """
        forward = [f"/tmp/cat_{name}.png" for name in ("front", "left", "back", "right")]
        shuffled = [f"/tmp/cat_{name}.png" for name in ("back", "right", "front", "left")]
        assert assign_views(forward) == assign_views(shuffled)

    def test_a_filename_naming_no_view_is_refused(self):
        with pytest.raises(ValueError, match="Cannot tell which view"):
            assign_views(["/tmp/cat_front.png", "/tmp/cat_photo2.png"])

    def test_a_filename_naming_two_views_is_refused(self):
        """'front_left.png' is a real thing an operator would write, and it is genuinely ambiguous."""
        with pytest.raises(ValueError, match="Cannot tell which view"):
            assign_views(["/tmp/cat_front.png", "/tmp/cat_front_left.png"])

    def test_two_files_claiming_one_view_are_refused(self):
        with pytest.raises(ValueError, match="both name the 'front' view"):
            assign_views(["/tmp/a_front.png", "/tmp/b_front.png"])

    def test_a_multi_view_set_without_a_front_view_is_refused(self):
        """The texture stage paints from the front view alone, so its absence is not recoverable."""
        with pytest.raises(ValueError, match="needs a front view"):
            assign_views(["/tmp/cat_left.png", "/tmp/cat_back.png"])

    def test_view_names_match_what_upstream_indexes(self):
        """Upstream's view lookup is a bare dict index, so a name it does not know raises KeyError deep
        inside its pipeline. These four are the keys its multi-view processor declares."""
        assert VIEW_NAMES == ("front", "left", "back", "right")


@pytest.mark.unit
class TestOnlyAModelThatCanUseThemAcceptsThem:
    def test_hunyuan3d_declares_it_accepts_several_views(self):
        assert Hunyuan3dAdapter.ACCEPTS_MULTIPLE_VIEWS is True

    @pytest.mark.parametrize("adapter", _SINGLE_VIEW_ADAPTERS,
                             ids=[a.KEY for a in _SINGLE_VIEW_ADAPTERS])
    def test_every_other_model_declares_it_does_not(self, adapter):
        assert adapter.ACCEPTS_MULTIPLE_VIEWS is False

    @pytest.mark.parametrize("adapter", _SINGLE_VIEW_ADAPTERS + (Hunyuan3dAdapter,),
                             ids=[a.KEY for a in _SINGLE_VIEW_ADAPTERS + (Hunyuan3dAdapter,)])
    def test_every_adapter_accepts_the_argument_so_the_contract_is_uniform(self, adapter):
        """generate.py calls every adapter the same way; one that did not take the keyword would raise
        TypeError on a GPU instance instead of the legible refusal."""
        import inspect

        assert "extra_images" in inspect.signature(adapter.generate).parameters

    def test_a_multi_view_run_on_a_single_view_model_is_refused_before_any_gpu_work(self):
        """The refusal has to happen in dispatch, ahead of ensure_ready and the upstream call."""
        import generate as generate_module

        with pytest.raises(ValueError, match="reconstructs from a single image"):
            generate_module.generate("/tmp/a_front.png", "/tmp/work", {"MODEL": "stable3dgen"},
                                     extra_images=["/tmp/a_left.png"])

    def test_a_multi_view_hunyuan3d_run_uses_the_multi_view_checkpoint(self):
        """The single-view DiT cannot read a view dict, so the count must select the checkpoint."""
        from models.hunyuan3d import MULTI_VIEW_REPO, MULTI_VIEW_SUBFOLDER

        assert MULTI_VIEW_REPO in Hunyuan3dAdapter.HF_REPOS, (
            "the multi-view weights must be pre-fetched, not downloaded mid-run")
        assert MULTI_VIEW_SUBFOLDER == "hunyuan3d-dit-v2-mv"


@pytest.mark.unit
class TestTheContainerReadsEveryViewItIsGiven:
    @pytest.fixture(scope="class")
    def main_module(self):
        spec = importlib.util.spec_from_file_location(
            "imageto3d_container_main_mv", os.path.join(_CONTAINER_DIR, "__main__.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_a_definition_with_no_inputfiles_list_still_works(self, main_module, monkeypatch):
        """The list is additive: a definition written before it existed, or a direct container
        invocation, must behave exactly as it did."""
        seen = []
        monkeypatch.setattr(main_module, "download_input_image",
                            lambda entry: seen.append(entry) or f"/tmp/{entry['objectKey']}")
        paths = main_module.download_input_images(
            {"inputFile": {"bucketName": "b", "objectKey": "one.png"}})
        assert len(paths) == 1 and len(seen) == 1

    def test_every_entry_in_the_list_is_downloaded(self, main_module, monkeypatch):
        monkeypatch.setattr(main_module, "download_input_image",
                            lambda entry: f"/tmp/{entry['objectKey']}")
        paths = main_module.download_input_images({
            "inputFile": {"bucketName": "b", "objectKey": "cat_front.png"},
            "inputFiles": [{"bucketName": "b", "objectKey": f"cat_{n}.png"} for n in VIEW_NAMES],
        })
        assert len(paths) == 4

    def test_a_repeated_file_is_downloaded_once(self, main_module, monkeypatch):
        """Two manifest entries can name one file, and conditioning on the same view twice is not what
        the operator asked for."""
        monkeypatch.setattr(main_module, "download_input_image",
                            lambda entry: f"/tmp/{entry['objectKey']}")
        paths = main_module.download_input_images({
            "inputFiles": [{"bucketName": "b", "objectKey": "cat_front.png"},
                           {"bucketName": "b", "objectKey": "cat_front.png"},
                           {"bucketName": "b", "objectKey": "cat_back.png"}],
        })
        assert len(paths) == 2

    def test_a_definition_carrying_no_usable_file_raises(self, main_module):
        with pytest.raises(ValueError, match="no usable input file"):
            main_module.download_input_images({"inputFiles": []})
