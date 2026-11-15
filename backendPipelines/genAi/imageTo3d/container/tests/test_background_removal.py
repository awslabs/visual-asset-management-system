#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The background-removal backends, and the prompted one in particular.

The mask is not a preprocessing detail. SAM 3D Objects reads the object out of the alpha channel, so the
mask IS one of its two inputs, and every other model is handed a cutout built from it. A backend that
returns the wrong region does not degrade a result, it reconstructs the wrong thing.

SAM is the only backend that takes a prompt, and the only one that can therefore be aimed at the WRONG
object. Its prompt parsing and its guards are covered here directly; its model call is covered with
injected stand-ins, because torch and transformers are not installed in the test environment and the mask
selection and the coverage guard are the parts most likely to be wrong -- a live GPU round trip is a poor
place to discover an argmax along the wrong axis.
"""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

import preprocess

TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vamsSchema", "templates")


def _templates():
    for name in sorted(os.listdir(TEMPLATE_DIR)):
        if name.endswith(".json"):
            with open(os.path.join(TEMPLATE_DIR, name), encoding="utf-8") as handle:
                yield name, json.load(handle)


@pytest.mark.unit
class TestTheBackendListAgreesWithTheTemplates:
    """A backend the container accepts but no template offers is unreachable; one a template offers and the
    container rejects fails the run at preprocessing, after the instance is up. Neither is a build error."""

    def test_every_template_offers_exactly_the_backends_the_container_implements(self):
        for name, template in _templates():
            fields = {t["tagKey"]: t for t in template.get("tagSchema", [])}
            assert "BACKGROUND_REMOVAL" in fields, name
            assert (set(fields["BACKGROUND_REMOVAL"]["enumValues"])
                    == set(preprocess.BACKGROUND_REMOVAL_BACKENDS)), name

    def test_every_backend_but_none_has_an_implementation(self):
        for backend in preprocess.BACKGROUND_REMOVAL_BACKENDS:
            if backend == "none":
                continue
            assert backend in preprocess._BACKENDS, backend

    def test_an_unknown_backend_is_rejected_by_name(self, tmp_path):
        with pytest.raises(preprocess.PreprocessError, match="Unknown background removal backend"):
            preprocess.remove_background("x.png", str(tmp_path), "sam4")


@pytest.mark.unit
class TestTheWeightCacheKnowsWhatABackendNeeds:
    """A backend whose repository the cache does not know about makes the cache report a HIT on the model's
    weights alone, so nothing restores the backend's and preprocessing downloads them -- on a GPU instance,
    after the image pull, 2.4 GB for SAM. Declaring it does not remove a cold first download, since the
    stage restores from S3 rather than pre-fetching; it makes the check honest and gets the weights into
    the next backup."""

    def test_the_backends_with_huggingface_weights_declare_them(self):
        assert preprocess.backend_repos("sam") == (preprocess.SAM_MODEL_REPO,)
        assert preprocess.backend_repos("rmbg2") == ("briaai/RMBG-2.0",)

    def test_the_backends_without_them_declare_nothing(self):
        # rembg and birefnet fetch ONNX files from a GitHub release, not from HuggingFace.
        for backend in ("none", "rembg", "birefnet", "", None, "unknown"):
            assert preprocess.backend_repos(backend) == ()


@pytest.mark.unit
class TestTheSamPromptIsResolvedInNormalisedCoordinates:
    """Normalised rather than pixel coordinates, so one value keeps its meaning if the photograph is
    re-exported at another resolution."""

    def test_the_default_is_a_point_at_the_centre(self):
        for value in ("", None, "centre", "center", "auto", "  CENTRE  "):
            prompt, described = preprocess.resolve_sam_prompt(value, (1280, 720))
            assert prompt == {"input_points": [[[640, 360]]]}, value
            assert "centre" in described

    def test_two_numbers_are_a_point(self):
        prompt, described = preprocess.resolve_sam_prompt("0.5,0.25", (801, 401))
        assert prompt == {"input_points": [[[400, 100]]]}
        assert "point" in described

    def test_four_numbers_are_a_box(self):
        prompt, _described = preprocess.resolve_sam_prompt("0.25,0.5,0.75,1.0", (401, 201))
        assert prompt == {"input_boxes": [[[100, 100, 300, 200]]]}

    def test_a_box_given_corners_in_either_order_is_normalised(self):
        """An operator reading a picture may drag from any corner, so the two are sorted rather than
        assumed. An unsorted box has negative width and SAM would return something arbitrary."""
        forward, _ = preprocess.resolve_sam_prompt("0.25,0.5,0.75,1.0", (401, 201))
        reversed_corners, _ = preprocess.resolve_sam_prompt("0.75,1.0,0.25,0.5", (401, 201))
        assert forward == reversed_corners

    @pytest.mark.parametrize("value,reason", [
        ("nonsense", "not a coordinate"),
        ("0.5", "coordinate"),
        ("0.1,0.2,0.3", "coordinate"),
        ("0.1,0.2,0.3,0.4,0.5", "coordinate"),
        ("640,360", "outside 0-1"),
        ("-0.1,0.5", "outside 0-1"),
        ("0.5,0.5,0.5,0.9", "no area"),
    ])
    def test_a_prompt_it_cannot_honour_raises_rather_than_defaulting(self, value, reason):
        """Falling back to the centre would be the worst outcome: the prompt exists to name one object
        among several, so ignoring it produces a confident reconstruction of the wrong one."""
        with pytest.raises(preprocess.PreprocessError, match=reason):
            preprocess.resolve_sam_prompt(value, (1280, 720))


def _fake_sam(monkeypatch, masks, scores):
    """Stand in for torch and transformers with a SAM that returns known masks and scores."""
    import numpy as np

    class Tensor:
        def __init__(self, value):
            self.value = np.asarray(value)

        def cpu(self):
            return self

        def detach(self):
            return self

        def numpy(self):
            return self.value

        def __getitem__(self, index):
            return Tensor(self.value[index])

        def __len__(self):
            return len(self.value)

    class Inputs(dict):
        def to(self, _device):
            return self

    outputs = MagicMock()
    outputs.pred_masks = Tensor(np.zeros((1, 1, len(masks), 4, 4)))
    outputs.iou_scores = Tensor(np.asarray([[scores]]))

    processor = MagicMock()
    processor.return_value = Inputs({"original_sizes": Tensor([[4, 4]]),
                                     "reshaped_input_sizes": Tensor([[4, 4]])})
    processor.image_processor.post_process_masks.return_value = [[np.asarray(masks)]]

    model = MagicMock()
    model.return_value = outputs

    torch = MagicMock()
    torch.cuda.is_available.return_value = False
    monkeypatch.setitem(sys.modules, "torch", torch)

    transformers = MagicMock()
    transformers.SamModel.from_pretrained.return_value = model
    transformers.SamProcessor.from_pretrained.return_value = processor
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    return transformers


def _photo(tmp_path, size=(4, 4)):
    from PIL import Image

    path = tmp_path / "subject.png"
    Image.new("RGB", size, (30, 60, 90)).save(path)
    return str(path)


@pytest.mark.unit
class TestSamTakesItsBestProposedMask:
    """SAM proposes three masks for one prompt -- roughly the part, the object and the group it belongs
    to -- with its own quality estimate for each. Taking the first would take whichever scale the decoder
    happened to order first."""

    def test_the_highest_scoring_mask_is_the_one_used(self, tmp_path, monkeypatch):
        import numpy as np
        from PIL import Image

        wanted = np.zeros((4, 4), bool)
        wanted[1:3, 1:3] = True          # a quarter of the frame
        other = np.ones((4, 4), bool)
        other[0, 0] = False
        _fake_sam(monkeypatch, [other, wanted, other], [0.10, 0.90, 0.20])

        out = tmp_path / "cut.png"
        preprocess._remove_background_sam(_photo(tmp_path), str(out), {})

        alpha = np.asarray(Image.open(out).convert("RGBA"))[..., 3]
        assert (alpha > 127).tolist() == wanted.tolist()

    def test_the_output_is_an_rgba_cutout_carrying_the_mask_in_alpha(self, tmp_path, monkeypatch):
        """Every model downstream reads the subject out of the alpha channel; SAM 3D reads it as its
        mask input directly."""
        import numpy as np
        from PIL import Image

        mask = np.zeros((4, 4), bool)
        mask[1:3, 1:3] = True
        _fake_sam(monkeypatch, [mask], [0.9])

        out = tmp_path / "cut.png"
        preprocess._remove_background_sam(_photo(tmp_path), str(out), {})

        image = Image.open(out)
        assert image.mode == "RGBA"
        assert set(np.unique(np.asarray(image)[..., 3]).tolist()) == {0, 255}

    def test_a_shape_it_does_not_recognise_raises_instead_of_guessing_an_axis(self, tmp_path, monkeypatch):
        """A shape change upstream would otherwise select along the wrong axis and return a mask for
        something else, which no later stage can detect."""
        import numpy as np

        _fake_sam(monkeypatch, np.zeros((4, 4), bool), [0.9])

        with pytest.raises(preprocess.PreprocessError, match="expected one score per mask"):
            preprocess._remove_background_sam(_photo(tmp_path), str(tmp_path / "cut.png"), {})


@pytest.mark.unit
class TestSamRefusesAMaskThatMissedTheSubject:
    """A prompt on the background segments the background, and the run then reconstructs a wall with
    every gate criterion passing. The share of the frame a mask covers is the available check."""

    def test_a_mask_covering_almost_everything_is_rejected(self, tmp_path, monkeypatch):
        import numpy as np

        _fake_sam(monkeypatch, [np.ones((4, 4), bool)], [0.99])

        with pytest.raises(preprocess.PreprocessError, match="most likely on the background"):
            preprocess._remove_background_sam(_photo(tmp_path), str(tmp_path / "cut.png"), {})

    def test_a_mask_covering_almost_nothing_is_rejected(self, tmp_path, monkeypatch):
        import numpy as np

        speck = np.zeros((64, 64), bool)
        speck[0, 0] = True
        _fake_sam(monkeypatch, [speck], [0.99])

        with pytest.raises(preprocess.PreprocessError, match="most likely on the background"):
            preprocess._remove_background_sam(_photo(tmp_path, size=(64, 64)),
                                              str(tmp_path / "cut.png"), {})

    def test_the_prompt_is_named_in_the_refusal(self, tmp_path, monkeypatch):
        """The operator has to know which prompt produced it, since the fix is to move the prompt."""
        import numpy as np

        _fake_sam(monkeypatch, [np.ones((4, 4), bool)], [0.99])

        with pytest.raises(preprocess.PreprocessError, match="'0.9,0.9'"):
            preprocess._remove_background_sam(
                _photo(tmp_path), str(tmp_path / "cut.png"), {"SAM_PROMPT": "0.9,0.9"})


@pytest.mark.unit
class TestTheBackendReachesItsSettings:
    def test_the_prompt_travels_from_the_run_settings(self, tmp_path, monkeypatch):
        import numpy as np

        mask = np.zeros((256, 256), bool)
        mask[64:192, 64:192] = True
        transformers = _fake_sam(monkeypatch, [mask], [0.9])

        preprocess.prepare_input_image(
            _photo(tmp_path, size=(256, 256)), str(tmp_path), "sam", {"SAM_PROMPT": "0.25,0.75"})

        processor = transformers.SamProcessor.from_pretrained.return_value
        _args, kwargs = processor.call_args
        assert kwargs["input_points"] == [[[64, 191]]], "the prompt reached the processor"

    def test_the_model_repository_can_be_overridden(self, tmp_path, monkeypatch):
        """The base and large checkpoints share this class and processor and trade quality for speed."""
        import numpy as np

        mask = np.zeros((4, 4), bool)
        mask[1:3, 1:3] = True
        transformers = _fake_sam(monkeypatch, [mask], [0.9])

        preprocess._remove_background_sam(
            _photo(tmp_path), str(tmp_path / "cut.png"), {"SAM_MODEL_REPO": "facebook/sam-vit-base"})

        transformers.SamModel.from_pretrained.assert_called_once_with("facebook/sam-vit-base")
