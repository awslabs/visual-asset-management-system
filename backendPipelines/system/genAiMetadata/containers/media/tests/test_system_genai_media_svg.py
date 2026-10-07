#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""SVG: size and viewBox come from the XML, no raster is produced (the SVG source is the analysis input),
and an SVG that declares XML entities is described without being parsed."""

import pytest

from media_extractors import svg
from system_genai_media_fixtures import make_ctx, svg_bytes, write


@pytest.mark.unit
class TestExtractSvg:
    def test_size_viewbox_counts_and_text(self, tmp_path):
        path = write(tmp_path, "logo.svg", svg_bytes())
        result = svg.extract_svg(path, make_ctx(tmp_path, "logo.svg"))
        sys_image = result.attributes["sys_image"]
        assert result.file_class == "image"
        assert sys_image["format"] == "SVG" and sys_image["vector"] is True
        assert (sys_image["width"], sys_image["height"]) == (200.0, 100.0)
        assert (sys_image["widthUnit"], sys_image["heightUnit"]) == ("px", "px")
        assert sys_image["viewBox"] == [0.0, 0.0, 200.0, 100.0]
        assert sys_image["elementCount"] == 3
        assert sys_image["elementCounts"] == {"svg": 1, "rect": 1, "text": 1}
        assert sys_image["hasText"] is True and sys_image["textContent"] == "Label"
        assert result.render_images == [] and result.render_skipped is None
        assert result.text_excerpt.startswith("<?xml")
        assert result.facts["dimensions"] == "200 x 100 px"
        assert result.facts["imageFormat"] == "SVG"
        assert result.facts["elements"] == "3 elements"

    def test_units_and_viewbox_fallback(self, tmp_path):
        data = b'<svg xmlns="http://www.w3.org/2000/svg" width="12.5mm" viewBox="0 0 50 25"><g/></svg>'
        path = write(tmp_path, "part.svg", data)
        sys_image = svg.extract_svg(path, make_ctx(tmp_path, "part.svg")).attributes["sys_image"]
        assert sys_image["width"] == 12.5 and sys_image["widthUnit"] == "mm"
        # Height is absent on the root, so the viewBox supplies it.
        assert sys_image["height"] == 25.0
        assert sys_image["hasText"] is False and "textContent" not in sys_image

    def test_entity_declarations_are_not_parsed(self, tmp_path):
        data = (b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]>'
                b'<svg xmlns="http://www.w3.org/2000/svg"><text>&a;</text></svg>')
        path = write(tmp_path, "bomb.svg", data)
        result = svg.extract_svg(path, make_ctx(tmp_path, "bomb.svg"))
        assert result.attributes["sys_image"] == {"format": "SVG", "vector": True, "sizeBytes": len(data)}
        assert "entities" in result.warnings[0]
        assert result.text_excerpt == ""

    def test_svg_doctype_with_public_identifier_still_parses(self, tmp_path):
        data = (b'<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
                b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
                b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="5"><rect/></svg>')
        path = write(tmp_path, "export.svg", data)
        result = svg.extract_svg(path, make_ctx(tmp_path, "export.svg"))
        assert result.warnings == []
        assert result.attributes["sys_image"]["elementCount"] == 2

    def test_malformed_xml_keeps_the_text_excerpt(self, tmp_path):
        data = b'<svg xmlns="http://www.w3.org/2000/svg"><rect></svg>'
        path = write(tmp_path, "bad.svg", data)
        result = svg.extract_svg(path, make_ctx(tmp_path, "bad.svg"))
        assert "not well-formed" in result.warnings[0]
        assert result.text_excerpt == data.decode()
        assert result.attributes["sys_image"]["format"] == "SVG"

    def test_oversized_svg_is_header_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(svg, "SVG_MAX_BYTES", 10)
        path = write(tmp_path, "big.svg", svg_bytes())
        result = svg.extract_svg(path, make_ctx(tmp_path, "big.svg"))
        assert "parse budget" in result.warnings[0]
        assert "elementCount" not in result.attributes["sys_image"]

    def test_excerpt_respects_max_text_chars(self, tmp_path):
        path = write(tmp_path, "logo.svg", svg_bytes())
        result = svg.extract_svg(path, make_ctx(tmp_path, "logo.svg", max_text_chars=30))
        assert len(result.text_excerpt) <= 30


@pytest.mark.unit
def test_parse_length():
    assert svg.parse_length("200") == (200.0, "px")
    assert svg.parse_length("12.5mm") == (12.5, "mm")
    assert svg.parse_length(" 40 % ") == (40.0, "%")
    assert svg.parse_length("auto") is None
    assert svg.parse_length(None) is None
