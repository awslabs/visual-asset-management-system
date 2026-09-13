#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every `sys_*` section this image writes carries, under the registry's exact camelCase names, the source
keys WP06d's `lambda/metadataCatalog.py` promotes to typed `ext_*` metadata and to the `location` GeoJSON
(master §3.6 "Promotion source contract"; spec §6.3 "complete technical record"). `common.PROMOTION_SOURCE_KEYS`
pins the contract; here each extractor runs on a fixture that carries every source and its section is checked
key by key with the JSON type the catalogue expects. A key renamed in an extractor but not in the constant (or
the reverse) fails here. Durable (root CLAUDE.md Rule 13): every name stays writable."""

import json

import pytest

from media_extractors import audio, data, documents, geo, images, text, tiles3d, video
from media_extractors.common import PROMOTION_SOURCE_KEYS
from system_genai_media_fixtures import (
    ENGLISH_TEXT, FFMPEG_HEADER_WITH_TAGS, SYDNEY_GPS, FakeFfmpeg, csv_bytes, fake_read_frames, fcs_bytes,
    geojson_collection_dict, jpeg_with_exif_bytes, make_ctx, minimal_pdf_bytes, tileset_dict, wav_bytes, write,
)

# The sys_media contract is served by two kinds; each writes the keys its container can carry, and the two
# together must be exactly the contract (a key neither kind writes would be promoted from nowhere).
VIDEO_KEYS = {"kind", "durationSeconds", "width", "height", "frameRate", "videoCodec", "audioCodec", "bitrateKbps",
              "channels", "sampleRate", "tags"}
AUDIO_KEYS = {"kind", "durationSeconds", "bitrateKbps", "channels", "sampleRate", "tags"}


def _missing(required, section):
    """Contract keys the section does not carry (empty when the section satisfies the contract)."""
    return set(required) - set(section)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@pytest.fixture(autouse=True)
def _fake_ffmpeg_exe(monkeypatch):
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", "ffmpeg-under-test")


@pytest.mark.unit
def test_the_checker_reports_a_missing_key():
    # Control for every assertion below: the comparison names what is absent and is empty only when nothing is.
    assert _missing(("a", "b"), {"a": 1}) == {"b"}
    assert _missing(("a", "b"), {"a": 1, "b": None, "c": 2}) == set()


@pytest.mark.unit
def test_sys_image_with_gps(tmp_path):
    path = write(tmp_path, "shot.jpg", jpeg_with_exif_bytes(gps=SYDNEY_GPS))
    sys_image = images.extract_image(
        path, make_ctx(tmp_path, "shot.jpg", extract_geo_location=True)).attributes["sys_image"]
    assert _missing(PROMOTION_SOURCE_KEYS["sys_image"], sys_image) == set()
    assert _missing(PROMOTION_SOURCE_KEYS["sys_image.exif"], sys_image["exif"]) == set()
    assert set(sys_image["exif"]["gps"]) == set(PROMOTION_SOURCE_KEYS["sys_image.exif.gps"])
    assert _is_number(sys_image["width"]) and _is_number(sys_image["height"]) and isinstance(sys_image["mode"], str)
    exif = sys_image["exif"]
    assert all(isinstance(exif[key], str) for key in ("make", "model", "dateTimeOriginal"))
    assert all(_is_number(exif["gps"][key]) for key in ("latitude", "longitude", "altitude"))


@pytest.mark.unit
def test_sys_media_video(tmp_path):
    path = write(tmp_path, "clip.mp4", b"\x00")
    sys_media = video.extract_video(
        path, make_ctx(tmp_path, "clip.mp4"), run=FakeFfmpeg(header=FFMPEG_HEADER_WITH_TAGS),
        read_frames=fake_read_frames).attributes["sys_media"]
    assert _missing(VIDEO_KEYS, sys_media) == set()
    assert sys_media["kind"] == "video"
    for key in ("durationSeconds", "width", "height", "frameRate", "bitrateKbps", "channels", "sampleRate"):
        assert _is_number(sys_media[key]), key
    assert isinstance(sys_media["videoCodec"], str) and isinstance(sys_media["audioCodec"], str)
    assert set(sys_media["tags"]) == set(PROMOTION_SOURCE_KEYS["sys_media.tags"])
    assert _is_number(sys_media["tags"]["year"])
    assert all(isinstance(sys_media["tags"][key], str) for key in ("title", "artist", "album"))


@pytest.mark.unit
def test_sys_media_audio(tmp_path, monkeypatch):
    path = write(tmp_path, "song.wav", wav_bytes())
    real_get = audio.TinyTag.get

    def with_tags(target, **kwargs):
        parsed = real_get(target, **kwargs)
        parsed.title, parsed.artist, parsed.album, parsed.year = "T", "A", "B", "2026"
        return parsed

    monkeypatch.setattr(audio.TinyTag, "get", with_tags)
    sys_media = audio.extract_audio(path, make_ctx(tmp_path, "song.wav")).attributes["sys_media"]
    assert _missing(AUDIO_KEYS, sys_media) == set()
    assert sys_media["kind"] == "audio"
    for key in ("durationSeconds", "bitrateKbps", "channels", "sampleRate"):
        assert _is_number(sys_media[key]), key
    assert set(sys_media["tags"]) == set(PROMOTION_SOURCE_KEYS["sys_media.tags"]) and sys_media["tags"]["year"] == 2026


@pytest.mark.unit
def test_the_two_media_kinds_cover_the_contract_exactly():
    assert VIDEO_KEYS | AUDIO_KEYS == set(PROMOTION_SOURCE_KEYS["sys_media"])
    assert AUDIO_KEYS <= VIDEO_KEYS


@pytest.mark.unit
def test_sys_document(tmp_path):
    path = write(tmp_path, "report.pdf", minimal_pdf_bytes(pages=2))
    sys_document = documents.extract_pdf(path, make_ctx(tmp_path, "report.pdf")).attributes["sys_document"]
    assert _missing(PROMOTION_SOURCE_KEYS["sys_document"], sys_document) == set()
    assert _is_number(sys_document["pageCount"]) and isinstance(sys_document["hasText"], bool)
    assert isinstance(sys_document["title"], str) and isinstance(sys_document["author"], str)
    assert sys_document["createdAt"] == "2026-01-02T03:04:05Z"


@pytest.mark.unit
def test_sys_text(tmp_path):
    path = write(tmp_path, "notes.txt", ENGLISH_TEXT.encode("utf-8"))
    sys_text = text.extract_text(path, make_ctx(tmp_path, "notes.txt")).attributes["sys_text"]
    assert _missing(PROMOTION_SOURCE_KEYS["sys_text"], sys_text) == set()
    assert isinstance(sys_text["encoding"], str) and isinstance(sys_text["language"], str)
    assert _is_number(sys_text["lineCount"]) and _is_number(sys_text["wordCount"])


@pytest.mark.unit
@pytest.mark.parametrize("name, body", [("parts.csv", csv_bytes()), ("sample.fcs", fcs_bytes())])
def test_sys_data(tmp_path, name, body):
    path = write(tmp_path, name, body)
    sys_data = data.extract_data(path, make_ctx(tmp_path, name)).attributes["sys_data"]
    assert _missing(PROMOTION_SOURCE_KEYS["sys_data"], sys_data) == set()
    assert _is_number(sys_data["columnCount"]) and _is_number(sys_data["rowCount"])
    assert isinstance(sys_data["columns"], list) and all(isinstance(c, str) for c in sys_data["columns"])


@pytest.mark.unit
def test_sys_tiles3d(tmp_path):
    sys_tiles3d = tiles3d.extract_tiles3d(tileset_dict(), make_ctx(tmp_path, "tileset.json")).attributes["sys_tiles3d"]
    assert _missing(PROMOTION_SOURCE_KEYS["sys_tiles3d"], sys_tiles3d) == set()
    assert _is_number(sys_tiles3d["geometricError"]) and _is_number(sys_tiles3d["tileCount"])
    assert isinstance(sys_tiles3d["region"], list) and len(sys_tiles3d["region"]) == 6
    assert all(isinstance(value, float) for value in sys_tiles3d["region"])


@pytest.mark.unit
def test_sys_geo(tmp_path):
    sys_geo = geo.extract_geo(geojson_collection_dict(), make_ctx(tmp_path, "mixed.json")).attributes["sys_geo"]
    assert _missing(PROMOTION_SOURCE_KEYS["sys_geo"], sys_geo) == set()
    assert _is_number(sys_geo["featureCount"])
    assert isinstance(sys_geo["geometryTypes"], list) and all(isinstance(t, str) for t in sys_geo["geometryTypes"])
    assert sys_geo["footprint"]["type"] == "Polygon" and json.dumps(sys_geo["footprint"])
