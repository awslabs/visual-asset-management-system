#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The analysis prompts put the file's content first, and a corrected asset text reaches a seeded re-run.

A test deployment showed an asset description written as a test note ("a video over 3,500 MiB, moov atom at the
end") echoed into a video's title and first sentence even when its frames were informative, a description built
from the asset text alone when the frames showed little, and a seeded re-run that kept the old wording after the
asset text was corrected, because the prompt asked the model to keep a previous analysis's wording.

The file analysis, the asset summary (and the video window prompt, tested in the media image's suite) now say that
the asset's name, description and tags are context, and that the descriptive fields name what the file itself shows
or contains. Each successful file analysis records a digest of the asset text it was given (genai_context_digest,
beside genai_model); a seeded run keeps the previous analysis's wording only when that digest matches the asset text
it is given, and otherwise (the text changed, or an analysis from before the digest was written) gives the previous
analysis for reference only. The output schema, the structured-output tool and the guarded prompt parts are
unchanged; the new instructions are the pipeline's own words and travel in the plain block.
"""

import hashlib
import json

import pytest

import sysgenai_harness as h
import test_sysgenai_asset_summary as summary_tests
import test_sysgenai_generate_metadata as gm_tests

# The new sentences, pinned as text.
# D117: frames of a screen recording (a web application's screens) were taken for screenshots of the system that
# stores the file, and the asset text was described instead. The system prompt and the closing line now say the
# images are the file's own, and that a screen they show is what the file records.
FILE_IMAGES_SYSTEM = (
    "The rendered views or frames come from the file itself (a video's frames, a document's pages, the image, or "
    "views rendered from a 3D file), not screenshots of the system that stores it: when they show a software "
    "window, web page or other user interface, the file records that screen, so the title and description name the "
    "kind of application or screen and what it shows. ")
FILE_IMAGES_NOTE = "They are the file's own content, not screenshots of the system that stores it."
SYSTEM_CONTENT_FIRST = (
    "The asset's name, description and tags are context about the asset the file belongs to: they may describe the "
    "upload, a project or a test rather than this file, so do not present them as the file's content. "
    "The title, description and keywords must name what the file itself shows or contains, as its rendered views "
    "or frames, its extracted text and its attributes show it. "
    + FILE_IMAGES_SYSTEM
    + "When the file shows little you can name, say so plainly rather than describe the asset text as the file's "
    "content. ")
USER_CONTENT_FIRST = ("The asset's name, description and tags are context about the asset this file belongs to; the "
                      "title, description and keywords name what this file itself shows or contains.")
KEEP_WORDING = "A previous analysis of this file is included; keep its wording where it still describes the file."
CONTEXT_ONLY = ("A previous analysis of this file is included for reference only: the asset's name, description or "
                "tags may have changed since it was made, so describe the file afresh rather than keep its wording.")
SUMMARY_CONTENT_FIRST = (
    "The asset's name, description and tags are context: they may describe the upload, a project or a test rather "
    "than what the asset contains, so do not present them as its content. "
    "The title, description and keywords must name what the asset's files contain, as their descriptions say. "
    "When those descriptions say little you can name, say so plainly rather than describe the asset text as the "
    "asset's content. ")
SUMMARY_USER_CONTENT_FIRST = ("The asset's name, description and tags are context; the title, description and "
                              "keywords name what its files contain, as their descriptions say.")
# The output schema part of the file analysis's system prompt, as it was before this change.
ANALYSIS_SCHEMA_TEXT = (
    "Return ONLY a JSON object with exactly these keys: "
    '"title" (string, at most 80 characters naming the content), '
    '"description" (string, at most 1200 characters of plain prose describing what the file contains and depicts), '
    '"keywords" (array of 8 to 15 lowercase strings of one to three words each), '
    '"category" (one category from CATEGORY OPTIONS), '
    '"subcategory" (one of that category\'s subcategories, or null), '
    '"style" (one of STYLE OPTIONS, or null), '
    '"materials" (array of strings from MATERIAL OPTIONS, empty when none is visible), '
    '"colors" (array of strings from COLOR OPTIONS, the dominant color first, empty when none applies), '
    '"objects" (array of strings naming the distinct objects, parts or entities visible or described; empty when '
    'none), '
    '"complexity" (one of "low", "medium", "high"), '
    '"orientation" (for 3D content the up axis and facing direction, e.g. "Z-up, front faces -Y"; otherwise null), '
    '"sizeEstimate" (the real-world size in words when it can be judged, otherwise null), '
    '"textSummary" (string, at most 2000 characters summarising any text content, or null when the file carries no '
    'text). '
    "Do not wrap the JSON in markdown fences and do not add any other text.")
ANALYSIS_TOOL_NAMES = ("title", "description", "keywords", "category", "subcategory", "style", "materials", "colors",
                       "objects", "complexity", "orientation", "sizeEstimate", "textSummary")
# The envelope's asset text (test_sysgenai_generate_metadata._envelope).
ASSET_TEXT = {"assetName": "Gear Pump", "description": "A brass gear pump", "tags": ["pump", "brass"]}
DIGEST_KEY = "genai_context_digest"


def _digest(asset_data):
    """The asset-text digest, computed here independently of the module: sha256 of the canonical JSON of the
    name, description and non-blank tags."""
    tags = [str(tag) for tag in asset_data.get("tags") or [] if str(tag).strip()]
    text = json.dumps({"name": asset_data.get("assetName") or "", "description": asset_data.get("description") or "",
                       "tags": tags}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _envelope_with(asset_data=None, file_metadata=None, file_attributes=None, asset_metadata=None):
    envelope = gm_tests._envelope(file_metadata=file_metadata, file_attributes=file_attributes,
                                  asset_metadata=asset_metadata)
    if asset_data is not None:
        envelope["assets"][0]["assetData"] = asset_data
    return envelope


def _analyse(envelope=None, config=None, env=None, bedrock=None):
    s3 = gm_tests._seed(h.FakeS3(), envelope=envelope, config=config)
    bedrock = bedrock or h.FakeBedrock([gm_tests._reply()])
    mod, _state = gm_tests._run(gm_tests._state(), s3, bedrock, env=env)
    return mod, s3, bedrock


def _summary(s3):
    return s3.json_at("abkt", gm_tests.SUMMARY_KEY)


@pytest.mark.unit
class TestTheFileAnalysisPutsContentFirst:
    def test_the_system_prompt_says_the_asset_text_is_context(self):
        mod, _s3, bedrock = _analyse()
        system = bedrock.calls[0]["system"][0]["text"]
        assert SYSTEM_CONTENT_FIRST in system
        # The output schema is the one the prompt asked for before.
        assert system.endswith(ANALYSIS_SCHEMA_TEXT)
        assert system.index("optionally rendered views or frames of the file. ") < system.index(SYSTEM_CONTENT_FIRST)
        assert tuple(name for name, *_rest in mod.ANALYSIS_TOOL_FIELDS) == ANALYSIS_TOOL_NAMES

    def test_the_user_message_carries_the_content_first_instruction_with_the_asset_text(self):
        _mod, _s3, bedrock = _analyse()
        text = gm_tests._user_text(bedrock)
        assert "Asset description: A brass gear pump" in text and USER_CONTENT_FIRST in text
        assert text.index("Asset description:") < text.index(USER_CONTENT_FIRST)

    def test_without_asset_text_the_instruction_is_not_given(self):
        _mod, _s3, bedrock = _analyse(_envelope_with(asset_data={"assetName": "", "description": "", "tags": []}))
        text = gm_tests._user_text(bedrock)
        assert "Asset name:" not in text and USER_CONTENT_FIRST not in text

    def test_with_a_guardrail_the_instruction_is_plain_and_the_guarded_text_is_unchanged(self):
        _mod, _s3, bedrock = _analyse(env=gm_tests.GUARDRAIL_ENV)
        content = bedrock.calls[0]["messages"][0]["content"]
        plain, guarded = content[0]["text"], content[1]["guardContent"]["text"]["text"]
        assert USER_CONTENT_FIRST in plain and USER_CONTENT_FIRST not in guarded
        assert "context about the asset" not in guarded
        # The guarded text is the file-derived input as before: it starts with the asset text and holds no
        # instruction of the pipeline's.
        assert guarded.startswith(
            "Asset name: Gear Pump\nAsset description: A brass gear pump\nAsset tags: pump, brass")
        for sentence in (KEEP_WORDING, CONTEXT_ONLY, SYSTEM_CONTENT_FIRST.strip()):
            assert sentence not in guarded


@pytest.mark.unit
class TestTheImagesAreTheFilesOwn:
    """D117: the images are the file's own content, also when they show a software screen."""

    def test_the_system_prompt_says_the_images_come_from_the_file(self):
        _mod, _s3, bedrock = _analyse()
        system = bedrock.calls[0]["system"][0]["text"]
        assert FILE_IMAGES_SYSTEM in system
        # Between the sentence that names the file's content and the one about a file that shows little.
        assert (system.index("its extracted text and its attributes show it. ") < system.index(FILE_IMAGES_SYSTEM)
                < system.index("When the file shows little you can name"))

    @pytest.mark.parametrize("manifest,state,count", [
        (lambda: gm_tests._manifest(), {}, 2),
        (lambda: gm_tests._image_manifest(), {"fileClass": "image", "renderBranch": "MEDIA"}, 1),
        (lambda: gm_tests._manifest(images=4, fileClass="video", renderBranch="MEDIA"),
         {"fileClass": "video", "fileExt": ".mp4", "renderBranch": "MEDIA"}, 4),
    ])
    def test_the_closing_line_says_so_after_the_image_count(self, manifest, state, count):
        s3 = gm_tests._seed(h.FakeS3(), manifest=manifest())
        bedrock = h.FakeBedrock([gm_tests._reply()])
        gm_tests._run(gm_tests._state(**state), s3, bedrock)
        text = gm_tests._user_text(bedrock)
        closing = f"{count} rendered view(s) or frame(s) of the file follow. {FILE_IMAGES_NOTE}"
        assert closing in text
        # The closing line comes after the file-derived parts and before the content-first instruction.
        assert text.index("File attributes (JSON):") < text.index(closing) < text.index(USER_CONTENT_FIRST)

    def test_without_images_the_note_is_not_given(self):
        manifest = gm_tests._manifest(images=0, fileClass="other", renderBranch="MEDIA", renderSkipped="unsupported")
        s3 = gm_tests._seed(h.FakeS3(), manifest=manifest)
        bedrock = h.FakeBedrock([gm_tests._reply()])
        gm_tests._run(gm_tests._state(fileClass="other", renderBranch="MEDIA", renderSkipped="unsupported"), s3,
                      bedrock)
        text = gm_tests._user_text(bedrock)
        assert "No rendered views are available" in text and FILE_IMAGES_NOTE not in text

    def test_with_a_guardrail_both_sentences_are_plain(self):
        _mod, _s3, bedrock = _analyse(env=gm_tests.GUARDRAIL_ENV)
        content = bedrock.calls[0]["messages"][0]["content"]
        plain, guarded = content[0]["text"], content[1]["guardContent"]["text"]["text"]
        assert f"2 rendered view(s) or frame(s) of the file follow. {FILE_IMAGES_NOTE}" in plain
        assert FILE_IMAGES_NOTE not in guarded and "screenshots" not in guarded
        assert FILE_IMAGES_SYSTEM.strip() not in guarded
        assert FILE_IMAGES_SYSTEM in bedrock.calls[0]["system"][0]["text"]


@pytest.mark.unit
class TestThePreviousAnalysisWording:
    """With seeding and key reuse on (the defaults) the previous analysis is in the prompt; its wording is kept only
    when the asset text it was made from is the asset text of this run."""

    PREVIOUS = {"genai_title": "Large-file video B - 3.9 GB H.264 video", "PART_NO": "GP-100"}

    def _run_with(self, stored_digest, asset_data=None, **kwargs):
        attributes = {"sys_file": '{"name":"pump.glb"}'}
        if stored_digest is not None:
            attributes[DIGEST_KEY] = stored_digest
        envelope = _envelope_with(asset_data=asset_data, file_metadata=dict(self.PREVIOUS),
                                  file_attributes=attributes, **kwargs)
        return _analyse(envelope)

    def test_an_unchanged_asset_text_keeps_the_previous_wording(self):
        _mod, s3, bedrock = self._run_with(_digest(ASSET_TEXT))
        text = gm_tests._user_text(bedrock)
        assert "Previous analysis of this file:\n- title: Large-file video B" in text
        assert KEEP_WORDING in text and CONTEXT_ONLY not in text
        assert _summary(s3)["keyReuse"]["previousAnalysisUse"] == "keepWording"

    def test_a_changed_asset_text_gives_the_previous_analysis_for_reference_only(self):
        corrected = dict(ASSET_TEXT, description="Warehouse recording")
        _mod, s3, bedrock = self._run_with(_digest(ASSET_TEXT), asset_data=corrected)
        text = gm_tests._user_text(bedrock)
        assert "Previous analysis of this file:\n- title: Large-file video B" in text
        assert CONTEXT_ONLY in text and KEEP_WORDING not in text
        assert _summary(s3)["keyReuse"]["previousAnalysisUse"] == "contextOnlyAssetTextChanged"
        assert "previous-analysis" in _summary(s3)["sourceModalities"]

    @pytest.mark.parametrize("change", [{"assetName": "Warehouse recording B"}, {"tags": ["pump"]}],
                             ids=["name", "tags"])
    def test_a_changed_name_or_tags_counts_as_a_change(self, change):
        _mod, s3, bedrock = self._run_with(_digest(ASSET_TEXT), asset_data=dict(ASSET_TEXT, **change))
        assert CONTEXT_ONLY in gm_tests._user_text(bedrock)
        assert _summary(s3)["keyReuse"]["previousAnalysisUse"] == "contextOnlyAssetTextChanged"

    def test_an_analysis_from_before_the_digest_is_reference_only(self):
        _mod, s3, bedrock = self._run_with(None)
        text = gm_tests._user_text(bedrock)
        assert CONTEXT_ONLY in text and KEEP_WORDING not in text
        assert _summary(s3)["keyReuse"]["previousAnalysisUse"] == "contextOnlyNoRecord"

    def test_with_a_guardrail_the_wording_note_is_plain(self):
        attributes = {DIGEST_KEY: _digest(ASSET_TEXT)}
        envelope = _envelope_with(file_metadata=dict(self.PREVIOUS), file_attributes=attributes)
        _mod, _s3, bedrock = _analyse(envelope, env=gm_tests.GUARDRAIL_ENV)
        content = bedrock.calls[0]["messages"][0]["content"]
        plain, guarded = content[0]["text"], content[1]["guardContent"]["text"]["text"]
        assert KEEP_WORDING in plain and "- title: Large-file video B" in guarded
        assert KEEP_WORDING not in guarded and "- title: Large-file video B" not in plain

    def test_no_previous_analysis_no_wording_note(self):
        config = dict(gm_tests.DEFAULT_CONFIG, seedWithExistingMetadata=False)
        envelope = _envelope_with(file_metadata=dict(self.PREVIOUS), file_attributes={DIGEST_KEY: _digest(ASSET_TEXT)})
        _mod, s3, bedrock = _analyse(envelope, config=config)
        text = gm_tests._user_text(bedrock)
        assert KEEP_WORDING not in text and CONTEXT_ONLY not in text
        assert _summary(s3)["keyReuse"]["previousAnalysisUse"] is None

    @pytest.mark.parametrize("source, use", [("file:/models/pump.glb", "keepWording"),
                                             ("file:/models/other.glb", "contextOnlyNoRecord")],
                             ids=["this-file", "another-file"])
    def test_the_asset_target_uses_the_record_of_the_file_that_wrote_the_asset_values(self, source, use):
        asset_metadata = {"genai_asset_title": "Pump assembly", "genai_asset_source": source}
        envelope = _envelope_with(asset_metadata=asset_metadata,
                                  file_attributes={DIGEST_KEY: _digest(ASSET_TEXT)})
        config = dict(gm_tests.DEFAULT_CONFIG, metadataWriteTarget="asset")
        _mod, s3, bedrock = _analyse(envelope, config=config)
        text = gm_tests._user_text(bedrock)
        assert "- title: Pump assembly" in text
        assert (KEEP_WORDING in text) is (use == "keepWording")
        assert _summary(s3)["keyReuse"]["previousAnalysisUse"] == use


@pytest.mark.unit
class TestTheDigestRow:
    def test_a_successful_analysis_writes_the_asset_text_digest_beside_the_provenance(self):
        _mod, s3, _bedrock = _analyse()
        rows = gm_tests._rows(s3, gm_tests.ATTRIBUTE_KEY)
        assert rows[DIGEST_KEY] == {"metadataKey": DIGEST_KEY, "metadataValue": _digest(ASSET_TEXT),
                                    "metadataValueType": "string"}
        keys = list(rows)
        assert keys.index("genai_model") < keys.index("genai_generated_at") < keys.index(DIGEST_KEY)

    def test_a_failed_analysis_writes_no_digest(self):
        from botocore.exceptions import ClientError
        denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "Converse")
        _mod, s3, _bedrock = _analyse(bedrock=h.FakeBedrock([denied]))
        assert _summary(s3)["status"] == "FAILED"
        # The pre-model provenance rows land; the digest is left to the analysis the file's rows come from.
        rows = gm_tests._rows(s3, gm_tests.ATTRIBUTE_KEY)
        assert "genai_model" in rows and DIGEST_KEY not in rows

    def test_an_attributes_only_run_writes_no_digest(self):
        s3 = gm_tests._seed(h.FakeS3())
        gm_tests._run(gm_tests._state(genAiAnalysisEnabled=False), s3, h.FakeBedrock([gm_tests._reply()]))
        assert DIGEST_KEY not in gm_tests._rows(s3, gm_tests.ATTRIBUTE_KEY)

    def test_the_digest_is_not_shown_to_the_model(self):
        envelope = _envelope_with(file_attributes={DIGEST_KEY: _digest(ASSET_TEXT)})
        _mod, _s3, bedrock = _analyse(envelope)
        assert _digest(ASSET_TEXT) not in gm_tests._user_text(bedrock)


@pytest.mark.unit
class TestTheAssetSummaryPutsContentFirst:
    def test_the_system_prompt_and_the_plain_instruction_say_the_asset_text_is_context(self):
        _mod, _s3, bedrock, _state = summary_tests._summarize()
        call = bedrock.calls[0]
        system = call["system"][0]["text"]
        assert SUMMARY_CONTENT_FIRST in system
        assert system.index("rather than file by file. ") < system.index(SUMMARY_CONTENT_FIRST) < system.index(
            "Return ONLY a JSON object")
        assert SUMMARY_USER_CONTENT_FIRST in summary_tests._prompt(bedrock)

    def test_with_a_guardrail_the_instruction_is_plain(self):
        _mod, _s3, bedrock, _state = summary_tests._summarize(env=summary_tests.GUARDRAIL_ENV)
        content = bedrock.calls[0]["messages"][0]["content"]
        plain, guarded = content[0]["text"], content[1]["guardContent"]["text"]["text"]
        assert SUMMARY_USER_CONTENT_FIRST in plain and SUMMARY_USER_CONTENT_FIRST not in guarded
        assert guarded.startswith("Asset name: Gear Pump\nAsset description: A pump\nAsset tags: pump")
