#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""conversion/3dBasic: the guards vendored from the 3D thumbnail container stay identical to it, and do
what the thumbnail container relies on them for.

A container's build context holds only its own directory, so ``scene_limits.py``, ``archive_guard.py``
and ``xml_guard.py`` here are copies of ``preview_pipeline/format_handlers/scene_limits.py``,
``preview_pipeline/utils/archive.py`` and the hardened-parsing part of
``preview_pipeline/format_handlers/xml_mesh_handler.py``. The drift tests below compare each copy with
its canonical file, apart from the import lines that point at this container's own modules, so a fix
made to one is made to the other (the same arrangement as ``compliance_output.py``)."""

import ast
import io
import os
import re
import sys
import zipfile
from unittest.mock import patch

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_LAMBDA_DIR = os.path.dirname(_HERE)
sys.path.insert(0, _LAMBDA_DIR)

_PIPELINES = os.path.normpath(os.path.join(_LAMBDA_DIR, "..", "..", ".."))
_CANONICAL = os.path.join(_PIPELINES, "preview", "3dThumbnail", "container", "preview_pipeline")

_NOTE = re.compile(r"(?m)^# (Vendored from|build context holds only|tests/test_3dbasic_vendored_guards\.py keeps).*\n")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _vendored(name):
    """A vendored file's text without its three-line origin note."""
    return _NOTE.sub("", _read(os.path.join(_LAMBDA_DIR, name)))


def _canonical(relative):
    return _read(os.path.join(_CANONICAL, relative))


def _canonical_constant(relative, name):
    match = re.search(rf"(?m)^{name}\s*=\s*([0-9_ *]+)$", _canonical(relative))
    assert match, f"{name} not found in {relative}"
    return eval(match.group(1).replace("_", ""), {"__builtins__": {}})  # nosec B307 - digits and * only


@pytest.mark.unit
class TestTheCopiesHaveNotDrifted:
    def test_scene_limits_matches_the_canonical_file_apart_from_its_imports(self):
        expected = _canonical("format_handlers/scene_limits.py").replace(
            "from ..resources import MAX_TRIANGLES\nfrom ..utils.logging import get_logger\n\nlogger = get_logger()\n",
            "from common.logger import safeLogger\n\n"
            "# The triangle budget of the thumbnail container's renderer (resources.MAX_TRIANGLES).\n"
            "MAX_TRIANGLES = 30_000_000\n\n"
            "logger = safeLogger(service=\"conversionTrimeshPipeline\")\n")
        assert "safeLogger" in expected, "the canonical import lines changed; update the vendored copy"
        assert _vendored("scene_limits.py") == expected

    def test_archive_guard_matches_the_canonical_file_apart_from_its_imports(self):
        expected = _canonical("utils/archive.py").replace(
            "from ..resources import free_disk_bytes, OUTPUT_DISK_HEADROOM_BYTES\n",
            "from size_check import free_disk_bytes\n\n"
            "# The headroom of the thumbnail container's work volume (resources.OUTPUT_DISK_HEADROOM_BYTES).\n"
            "OUTPUT_DISK_HEADROOM_BYTES = 1024 ** 3\n")
        assert "from size_check" in expected, "the canonical import line changed; update the vendored copy"
        assert _vendored("archive_guard.py") == expected

    @pytest.mark.parametrize("name", ["UnsafeXmlError", "_forbid", "_qualified", "_safe_expat", "parse_xml"])
    def test_each_xml_guard_function_is_the_canonical_one(self, name):
        source = _canonical("format_handlers/xml_mesh_handler.py")
        node = next(n for n in ast.parse(source).body if getattr(n, "name", None) == name)
        assert ast.get_source_segment(source, node) in _vendored("xml_guard.py")

    def test_the_vendored_xml_guard_holds_nothing_else_that_parses(self):
        names = [n.name for n in ast.parse(_vendored("xml_guard.py")).body if hasattr(n, "name")]
        assert names == ["UnsafeXmlError", "_forbid", "_qualified", "_safe_expat", "parse_xml"]

    def test_the_constants_the_copies_define_equal_the_canonical_ones(self):
        import archive_guard
        import scene_limits
        assert scene_limits.MAX_TRIANGLES == _canonical_constant("resources.py", "MAX_TRIANGLES")
        assert archive_guard.OUTPUT_DISK_HEADROOM_BYTES == _canonical_constant(
            "resources.py", "OUTPUT_DISK_HEADROOM_BYTES")


@pytest.mark.unit
class TestTheXmlGuard:
    def _parse(self, text):
        import xml_guard
        return xml_guard.parse_xml(io.BytesIO(text.encode("utf-8")))

    def test_a_plain_document_parses_with_namespaces(self):
        root = self._parse('<model xmlns="urn:x"><object id="1"/></model>')
        assert root.tag == "{urn:x}model" and root[0].tag == "{urn:x}object"

    def test_a_document_type_declaration_is_refused(self):
        import xml_guard
        with pytest.raises(xml_guard.UnsafeXmlError, match="document type declaration"):
            self._parse('<!DOCTYPE model SYSTEM "model.dtd"><model/>')

    def test_an_entity_declaration_is_refused(self):
        import xml_guard
        text = '<!DOCTYPE m [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><m>&b;</m>'
        with pytest.raises(xml_guard.UnsafeXmlError):
            self._parse(text)

    def test_an_external_entity_is_refused(self):
        import xml_guard
        with pytest.raises(xml_guard.UnsafeXmlError):
            self._parse('<!DOCTYPE m [<!ENTITY x SYSTEM "file:///etc/passwd">]><m>&x;</m>')

    def test_malformed_xml_is_a_value_error(self):
        with pytest.raises(ValueError, match="Invalid XML"):
            self._parse("<model><unclosed></model>")


@pytest.mark.unit
class TestTheSceneLimit:
    def test_nodes_past_the_bound_stop_the_walk(self):
        import scene_limits
        limit = scene_limits.SceneLimit("a 3MF file", max_triangles=100, max_nodes=3)
        assert [limit.visit() for _ in range(5)] == [True, True, True, False, False]
        assert limit.reached

    def test_triangles_past_the_bound_stop_placement_but_keep_the_crossing_mesh(self):
        import scene_limits
        limit = scene_limits.SceneLimit("a COLLADA file", max_triangles=100, max_nodes=1000)
        limit.add(30, 60)
        assert not limit.reached
        limit.add(60, 60)
        assert limit.reached and limit.triangles == 120

    def test_a_walk_counted_in_advance_that_does_not_fit_is_over_before_it_starts(self):
        import scene_limits
        limit = scene_limits.SceneLimit("a scene", max_triangles=100, max_nodes=10)
        assert limit.expect(10) and not limit.expect(11)


def _zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.unit
class TestTheArchiveGuard:
    def _write(self, tmp_path, entries):
        path = tmp_path / "a.zip"
        path.write_bytes(_zip(entries))
        return str(path)

    def test_a_small_archive_reports_its_uncompressed_size(self, tmp_path):
        import archive_guard
        path = self._write(tmp_path, {"3D/3dmodel.model": b"<model/>", "t.png": b"x" * 100})
        with patch.object(archive_guard, "free_disk_bytes", lambda p: 10 * 1024 ** 3):
            assert archive_guard.check_archive(path, str(tmp_path)) == 108

    def test_an_archive_larger_than_the_free_disk_is_refused_before_extraction(self, tmp_path):
        import archive_guard
        path = self._write(tmp_path, {"m.model": b"x" * 2048})
        with patch.object(archive_guard, "free_disk_bytes", lambda p: archive_guard.OUTPUT_DISK_HEADROOM_BYTES + 1024):
            with pytest.raises(archive_guard.ArchiveLimitError, match="uncompressed"):
                archive_guard.check_archive(path, str(tmp_path))

    def test_a_member_with_an_implausible_compression_ratio_is_refused(self, tmp_path):
        import archive_guard
        path = self._write(tmp_path, {"bomb.model": b"\0" * (80 * 1024 ** 2)})
        with patch.object(archive_guard, "free_disk_bytes", lambda p: 100 * 1024 ** 3):
            with pytest.raises(archive_guard.ArchiveLimitError, match="compression ratio"):
                archive_guard.check_archive(path, str(tmp_path))

    def test_too_many_members_are_refused(self, tmp_path):
        import archive_guard
        path = self._write(tmp_path, {f"f{i}": b"" for i in range(20)})
        with patch.object(archive_guard, "MAX_MEMBERS", 10), \
                patch.object(archive_guard, "free_disk_bytes", lambda p: 100 * 1024 ** 3):
            with pytest.raises(archive_guard.ArchiveLimitError, match="members"):
                archive_guard.check_archive(path, str(tmp_path))
