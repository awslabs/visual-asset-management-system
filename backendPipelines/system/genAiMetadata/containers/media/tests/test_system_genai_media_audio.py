#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Audio: header facts and tag text land in sys_media under the registry's names (sampleRate, channels,
bitrateKbps, tags.year as an int), each of tinytag's three no-facts outcomes (unrecognised container, invalid
header, empty tag object) is described without failing, and tag values are bounded."""

import pytest
from tinytag import TinyTag

from media_extractors import audio
from system_genai_media_fixtures import (
    FFMPEG_HEADER_M4A_WITH_POSITION,
    FakeFfmpeg,
    make_ctx,
    png_bytes,
    wav_bytes,
    write,
)


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

    def test_a_position_tag_follows_the_geo_gate_in_the_record_and_the_excerpt(self, tmp_path, monkeypatch):
        path = write(tmp_path, "memo.wav", wav_bytes())
        real_get = audio.TinyTag.get

        def with_location(target, **kwargs):
            parsed = real_get(target, **kwargs)
            parsed.title = "Field memo"
            parsed.other["location"] = ["+47.6097-122.3331+056.000/"]  # the ©xyz atom as tinytag surfaces it
            return parsed

        monkeypatch.setattr(audio.TinyTag, "get", with_location)
        withheld = audio.extract_audio(path, make_ctx(tmp_path, "memo.wav"))
        sys_media = withheld.attributes["sys_media"]
        assert sys_media["hasGps"] is True and "gps" not in sys_media
        assert sys_media["tags"] == {"title": "Field memo"}
        assert withheld.text_excerpt == "title: Field memo" and "location" not in withheld.facts
        decoded = audio.extract_audio(path, make_ctx(tmp_path, "memo.wav", extract_geo_location=True))
        sys_media = decoded.attributes["sys_media"]
        assert sys_media["gps"] == {"latitude": 47.6097, "longitude": -122.3331, "altitude": 56.0}
        assert sys_media["tags"]["location"] == "+47.6097-122.3331+056.000/"
        assert decoded.facts["location"] == "47.6097, -122.3331"
        assert decoded.text_excerpt == "title: Field memo\nlocation: +47.6097-122.3331+056.000/"


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

    def test_atom_index_names_are_dropped(self):
        # How tinytag names an MP4 `keys`/`mdta` atom it cannot resolve: the 1-based index as four bytes.
        tag = TinyTag()
        tag.title = "Memo"
        tag.other["\x00\x00\x00\x01"] = ["-33.8651+151.2099/"]
        tag.other["\x00\x00\x00\x02"] = ["Lavf61.1.100"]
        tag.other["comment"] = ["kept"]
        assert audio.tag_text(tag) == {"title": "Memo", "comment": "kept"}


@pytest.mark.unit
class TestMp4FamilyHeader:
    """Audio in an ISO base-media container reads the ffmpeg header for the tag names tinytag left as atom
    indexes; every other container never runs ffmpeg."""

    def _m4a_tag(self, real_get):
        def with_indexed_atoms(target, **kwargs):
            parsed = real_get(target, **kwargs)
            parsed.mime_type = 'audio/mp4; codecs="mp4a.40.2"'
            parsed.title = "Field memo"
            parsed.other["\x00\x00\x00\x01"] = ["-33.8651+151.2099/"]
            parsed.other["\x00\x00\x00\x02"] = ["Lavf61.1.100"]
            return parsed
        return with_indexed_atoms

    def test_header_names_the_position_and_recording_time(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "ffmpeg-under-test")
        path = write(tmp_path, "memo.m4a", wav_bytes())  # tinytag parses by the bytes; the mime is stubbed
        monkeypatch.setattr(audio.TinyTag, "get", self._m4a_tag(audio.TinyTag.get))
        ffmpeg = FakeFfmpeg(header=FFMPEG_HEADER_M4A_WITH_POSITION)
        result = audio.extract_audio(path, make_ctx(tmp_path, "memo.m4a", extract_geo_location=True), run=ffmpeg)
        sys_media = result.attributes["sys_media"]
        assert len(ffmpeg.calls) == 1 and "-frames:a" in ffmpeg.calls[0]
        assert sys_media["container"] == "MP4" and sys_media["decodable"] is True
        assert sys_media["audioCodec"] == "aac" and sys_media["channelLayout"] == "mono"
        assert sys_media["durationSeconds"] == 2.0  # tinytag's own reading wins over the header's
        assert sys_media["tags"]["title"] == "Field memo"
        assert sys_media["tags"]["location"] == "-33.8651+151.2099/"
        assert sys_media["tags"]["creation_time"] == "2024-05-01T10:20:30.000000Z"
        assert sys_media["tags"]["encoder"] == "Lavf61.1.100"
        assert not any(key.startswith("\x00") for key in sys_media["tags"])
        assert sys_media["streamTags"] == {"0:0": {"handler_name": "SoundHandler", "vendor_id": "[0][0][0][0]"}}
        assert sys_media["hasGps"] is True
        assert sys_media["gps"] == {"latitude": -33.8651, "longitude": 151.2099, "altitude": None}
        assert result.facts["location"] == "-33.8651, 151.2099"
        assert result.facts["recorded"] == "2024-05-01T10:20:30.000000Z"
        assert "location: -33.8651+151.2099/" in result.text_excerpt
        assert result.warnings == []

    def test_header_position_is_withheld_when_the_gate_is_off(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "ffmpeg-under-test")
        path = write(tmp_path, "memo.m4a", wav_bytes())
        monkeypatch.setattr(audio.TinyTag, "get", self._m4a_tag(audio.TinyTag.get))
        result = audio.extract_audio(path, make_ctx(tmp_path, "memo.m4a"),
                                     run=FakeFfmpeg(header=FFMPEG_HEADER_M4A_WITH_POSITION))
        sys_media = result.attributes["sys_media"]
        assert sys_media["hasGps"] is True and "gps" not in sys_media
        assert "location" not in sys_media["tags"] and "location-eng" not in sys_media["tags"]
        assert sys_media["tags"]["creation_time"] == "2024-05-01T10:20:30.000000Z"
        assert "location" not in result.text_excerpt and "location" not in result.facts

    def test_header_recovers_an_m4a_tinytag_could_not_read(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "ffmpeg-under-test")
        path = write(tmp_path, "memo.m4a", png_bytes(4, 4))  # the MP4 parser finds no atom it understands
        result = audio.extract_audio(path, make_ctx(tmp_path, "memo.m4a", extract_geo_location=True),
                                     run=FakeFfmpeg(header=FFMPEG_HEADER_M4A_WITH_POSITION))
        sys_media = result.attributes["sys_media"]
        assert sys_media["decodable"] is True and sys_media["container"] == "M4A"
        assert sys_media["durationSeconds"] == 2.0 and sys_media["sampleRate"] == 44100 and sys_media["channels"] == 1
        assert sys_media["gps"] == {"latitude": -33.8651, "longitude": 151.2099, "altitude": None}
        # tinytag's own outcome (an empty tag object, or a parse error) is still reported beside the recovery.
        assert len(result.warnings) == 1 and result.warnings[0].startswith("Audio header ")

    def test_an_unparseable_m4a_with_no_header_stays_not_decodable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "ffmpeg-under-test")
        path = write(tmp_path, "memo.m4a", png_bytes(4, 4))
        not_media = FakeFfmpeg(header="memo.m4a: Invalid data found when processing input\n", probe_returncode=1)
        result = audio.extract_audio(path, make_ctx(tmp_path, "memo.m4a"), run=not_media)
        assert result.attributes["sys_media"] == {"kind": "audio", "container": "M4A", "decodable": False}

    def test_other_containers_never_run_ffmpeg(self, tmp_path):
        def never(argv, **kwargs):
            raise AssertionError(f"ffmpeg ran for a WAV: {argv}")

        path = write(tmp_path, "tone.wav", wav_bytes())
        result = audio.extract_audio(path, make_ctx(tmp_path, "tone.wav"), run=never)
        assert result.attributes["sys_media"]["container"] == "WAVE"
