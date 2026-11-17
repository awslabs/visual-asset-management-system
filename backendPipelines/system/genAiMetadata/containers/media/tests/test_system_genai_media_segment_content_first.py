#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The video window prompt puts the frames first: the asset's name and the whole video's title, category and
description are context, and the window's fields name what its frames show (the content-first rule of the file
analysis, lambda/generateMetadata.py). The output keys and the guarded parts are unchanged; the new instruction is
the pipeline's own words and travels in the plain block."""

import pytest

from system_genai_media_fixtures import FakeS3
from test_system_genai_media_segment_handler import (
    GUARDRAIL_ENV, FakeBedrockRuntime, FakeEvents, _load, _reply, _run, _seed,
)

# D117: frames of a screen recording were taken for screenshots of the system that stores the video. The window
# prompt now says the frames are the video's own, also when they show a software screen.
FRAMES_ARE_THE_VIDEO = (
    "The frames come from the video itself, not screenshots of the system that stores it: when they show a "
    "software window, web page or other user interface, the video records that screen, so the description names "
    "the kind of application or screen and what it shows. ")
FRAMES_NOTE = "They are the video's own frames, not screenshots of the system that stores it."
SYSTEM_CONTENT_FIRST = (
    "The asset's name and the whole video's title, category and description are context: they may describe the "
    "upload, a project or a test rather than this window, so do not present them as the window's content. "
    "The description, keywords, objects and actions must name what the frames of this window show. "
    + FRAMES_ARE_THE_VIDEO
    + "When the frames show little you can name, say so plainly rather than describe that context as the window's "
    "content. ")
USER_CONTENT_FIRST = ("The asset name and the whole video's title, category and description are context; describe "
                      "what these frames show. " + FRAMES_NOTE)
# The output schema part of the window prompt, as it was before this change.
SEGMENT_SCHEMA_TEXT = (
    "Return ONLY a JSON object with exactly these keys: "
    '"description" (string, at most 400 characters of plain prose describing what the frames show), '
    '"keywords" (array of at most 10 lowercase strings of one to three words each), '
    '"objects" (array of at most 10 strings naming the distinct objects or entities visible), '
    '"actions" (array of at most 5 strings naming what is happening), '
    '"textSeen" (string of at most 200 characters with any legible text in the frames, or null). '
    "Do not wrap the JSON in markdown fences and do not add any other text.")


@pytest.mark.unit
class TestTheWindowPromptPutsTheFramesFirst:
    def test_the_system_prompt_says_the_asset_and_video_text_is_context(self, monkeypatch):
        bedrock = FakeBedrockRuntime([_reply()])
        module = _load(monkeypatch, _seed(FakeS3()), bedrock, FakeEvents())
        assert _run(module)["status"] == "SUCCEEDED"
        system = bedrock.calls[0]["system"][0]["text"]
        assert SYSTEM_CONTENT_FIRST in system and system.endswith(SEGMENT_SCHEMA_TEXT)
        assert system.index("frames taken inside the window. ") < system.index(SYSTEM_CONTENT_FIRST)
        assert module.SEGMENT_JSON_KEYS == ("description", "keywords", "objects", "actions", "textSeen")

    def test_with_a_guardrail_the_instruction_is_plain_and_the_guarded_text_is_unchanged(self, monkeypatch):
        bedrock = FakeBedrockRuntime([_reply()])
        module = _load(monkeypatch, _seed(FakeS3()), bedrock, FakeEvents(), env=GUARDRAIL_ENV)
        _run(module)
        content = bedrock.calls[0]["messages"][0]["content"]
        plain, guarded = content[0]["text"], content[1]["guardContent"]["text"]["text"]
        assert plain.endswith(USER_CONTENT_FIRST) and USER_CONTENT_FIRST not in guarded
        assert guarded == ("Asset name: Factory Tour\nFile: /clips/tour.mp4\nVideo title: Factory floor tour\n"
                           "Video category: Industrial Equipment\nVideo description: A walk through the assembly hall.")

    def test_without_any_context_the_instruction_is_not_given(self, monkeypatch):
        module = _load(monkeypatch, _seed(FakeS3()), FakeBedrockRuntime([_reply()]), FakeEvents())
        blocks = module.build_user_blocks("", "video (footage)", "/clips/tour.mp4", "0:10-0:20", 92.48, {}, 3)
        assert USER_CONTENT_FIRST not in blocks[0]["text"]
        blocks = module.build_user_blocks("Factory Tour", "video (footage)", "/clips/tour.mp4", "0:10-0:20", 92.48,
                                          {}, 3)
        assert USER_CONTENT_FIRST in blocks[0]["text"]


@pytest.mark.unit
class TestTheFramesAreTheVideosOwn:
    """D117: the frames are the video's own, also when they show a software screen."""

    def test_the_system_prompt_says_the_frames_come_from_the_video(self, monkeypatch):
        bedrock = FakeBedrockRuntime([_reply()])
        module = _load(monkeypatch, _seed(FakeS3()), bedrock, FakeEvents())
        _run(module)
        system = bedrock.calls[0]["system"][0]["text"]
        assert FRAMES_ARE_THE_VIDEO in system
        assert (system.index("must name what the frames of this window show. ") < system.index(FRAMES_ARE_THE_VIDEO)
                < system.index("When the frames show little you can name"))

    def test_the_instruction_says_so_after_the_frame_note(self, monkeypatch):
        module = _load(monkeypatch, _seed(FakeS3()), FakeBedrockRuntime([_reply()]), FakeEvents())
        text = module.build_user_blocks("Warehouse recording B", "video (footage)", "/clips/rec.mp4",
                                        "0:10-0:20", 92.48, {}, 3)[0]["text"]
        assert text.index("The 3 frames that follow were taken inside the window") < text.index(FRAMES_NOTE)
        # Without a guardrail the plain instructions come first, then the asset name and file path.
        assert text.index(FRAMES_NOTE) < text.index("Asset name: Warehouse recording B")

    def test_with_a_guardrail_both_sentences_are_plain(self, monkeypatch):
        bedrock = FakeBedrockRuntime([_reply()])
        module = _load(monkeypatch, _seed(FakeS3()), bedrock, FakeEvents(), env=GUARDRAIL_ENV)
        _run(module)
        content = bedrock.calls[0]["messages"][0]["content"]
        plain, guarded = content[0]["text"], content[1]["guardContent"]["text"]["text"]
        assert FRAMES_NOTE in plain and FRAMES_NOTE not in guarded and "screenshots" not in guarded
        assert FRAMES_ARE_THE_VIDEO in bedrock.calls[0]["system"][0]["text"]
