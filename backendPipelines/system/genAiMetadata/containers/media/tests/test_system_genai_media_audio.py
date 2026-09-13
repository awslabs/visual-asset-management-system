#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Audio: header facts and tag text land in sys_media under the registry's names (sampleRate, channels,
bitrateKbps, tags.year as an int), each of tinytag's three no-facts outcomes (unrecognised container, invalid
header, empty tag object) is described without failing, and tag values are bounded."""

import pytest
from tinytag import TinyTag

from media_extractors import audio
from system_genai_media_fixtures import make_ctx, png_bytes, wav_bytes, write


@pytest.mark.unit
class TestExtractAudio:
    def test_wav_header_facts(self, tmp_path):
        path = write(tmp_path, "tone.wav", wav_bytes(seconds=2.0, rate=8000))
        result = audio.extract_audio(path, make_ctx(tmp_path, "tone.wav"))
        sys_media = result.attributes["sys_media"]
        assert result.file_class == "audio"
        assert sys_media["kind"] == "audio" and sys_media["container"] == "WAVE" and sys_media["decodable"] is True
        assert sys_media["durationSeconds"] == 2.0
        assert sys_media["sampleRate"] == 8000 and sys_media["channels"] == 1
        assert sys_media["bitsPerSample"] == 16 and sys_media["bitrateKbps"] == 128
        assert "tags" not in sys_media and "sampleRateHz" not in sys_media
        assert result.facts["duration"] == "2 s"
        assert result.facts["sampleRate"] == "8000 Hz" and result.facts["channels"] == "1"
        assert result.render_images == [] and result.render_skipped is None
        assert result.text_excerpt == ""

    def test_unrecognised_container_is_described_not_failed(self, tmp_path):
        # tinytag has no .aac parser and PNG bytes match no magic number: UnsupportedFormatError.
        path = write(tmp_path, "notaudio.aac", png_bytes(4, 4))
        result = audio.extract_audio(path, make_ctx(tmp_path, "notaudio.aac"))
        assert result.attributes["sys_media"] == {"kind": "audio", "container": "AAC", "decodable": False}
        assert result.warnings == ["Audio container was not recognised"]
        assert result.render_skipped is None

    def test_invalid_header_is_described_not_failed(self, tmp_path):
        # The extension selects the WAVE parser, which raises ParseError on a PNG header.
        path = write(tmp_path, "notaudio.wav", png_bytes(4, 4))
        result = audio.extract_audio(path, make_ctx(tmp_path, "notaudio.wav"))
        assert result.attributes["sys_media"] == {"kind": "audio", "container": "WAV", "decodable": False}
        assert len(result.warnings) == 1 and result.warnings[0].startswith("Audio header could not be parsed: ")
        assert result.render_skipped is None

    def test_header_without_facts_or_tags_is_not_decodable(self, tmp_path):
        # The MP3 parser scans for a frame sync and returns an empty tag object rather than raising.
        path = write(tmp_path, "notaudio.mp3", png_bytes(4, 4))
        result = audio.extract_audio(path, make_ctx(tmp_path, "notaudio.mp3"))
        assert result.attributes["sys_media"] == {"kind": "audio", "container": "MP3", "decodable": False}
        assert result.warnings == ["Audio header carried no stream facts or tags"]

    def test_tags_feed_the_excerpt_and_the_facts(self, tmp_path, monkeypatch):
        path = write(tmp_path, "song.wav", wav_bytes())
        real_get = audio.TinyTag.get

        def with_tags(target, **kwargs):
            parsed = real_get(target, **kwargs)
            parsed.title = "Song Title"
            parsed.artist = "Artist One"
            parsed.other["artist"] = ["Artist Two"]  # where tinytag files a field's further values
            parsed.year = "2026-03-01"  # tinytag types year as str | None
            return parsed

        monkeypatch.setattr(audio.TinyTag, "get", with_tags)
        result = audio.extract_audio(path, make_ctx(tmp_path, "song.wav"))
        sys_media = result.attributes["sys_media"]
        assert sys_media["tags"] == {"title": "Song Title", "artist": "Artist One, Artist Two", "year": 2026}
        assert sys_media["durationSeconds"] == 2.0 and sys_media["decodable"] is True
        assert result.facts["title"] == "Song Title" and result.facts["artist"] == "Artist One, Artist Two"
        assert result.text_excerpt == "title: Song Title\nartist: Artist One, Artist Two\nyear: 2026-03-01"

    def test_non_numeric_year_stays_text(self, tmp_path, monkeypatch):
        path = write(tmp_path, "song.wav", wav_bytes())
        real_get = audio.TinyTag.get

        def with_year(target, **kwargs):
            parsed = real_get(target, **kwargs)
            parsed.year = "unknown"
            return parsed

        monkeypatch.setattr(audio.TinyTag, "get", with_year)
        sys_media = audio.extract_audio(path, make_ctx(tmp_path, "song.wav")).attributes["sys_media"]
        # The raw record keeps the text; the promotion's number coercion is what drops it from ext_year.
        assert sys_media["tags"] == {"year": "unknown"}


@pytest.mark.unit
class TestTagText:
    def test_none_and_non_tag_are_empty(self):
        assert audio.tag_text(None) == {}
        assert audio.tag_text("not a tag") == {}
        assert audio.tag_text(TinyTag()) == {}

    def test_binary_entries_are_dropped_and_values_bounded(self):
        tag = TinyTag()
        tag.title = "x" * 900
        tag.track = 7
        tag.other["blob"] = [b"\x00\x01"]
        tag.other["mixed"] = ["text", b"\x00"]
        rendered = audio.tag_text(tag)
        assert rendered == {"title": "x" * audio.AUDIO_TAG_VALUE_MAX_CHARS, "track": "7"}

    def test_tag_count_is_capped(self):
        tag = TinyTag()
        tag.other.update({f"custom{index}": [f"v{index}"] for index in range(200)})
        assert len(audio.tag_text(tag)) == audio.AUDIO_MAX_TAGS
