# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# Vendored from backendPipelines/preview/3dThumbnail/container/preview_pipeline/format_handlers/xml_mesh_handler.py (the hardened parsing section): a container's
# build context holds only its own directory, so the copy cannot be imported across pipelines.
# tests/test_3dbasic_vendored_guards.py keeps it identical apart from the import lines below.

"""
Hardened XML parsing for the XML-based mesh formats (3MF, COLLADA).

Every input is untrusted. A document is parsed with the standard library's expat parser, hardened the
way ``defusedxml`` hardens it (ARCC BSC12, secure XML parsing): a document type declaration, an entity
declaration or an external entity reference aborts the parse, and parameter entities are never read.
Neither format needs a DTD, so no legitimate file is refused, and no entity expansion ("billion
laughs") or external fetch (XXE: local files, URLs) can happen. The tree is built with
``ElementTree.TreeBuilder``; ``ElementTree.parse`` is never called on input.
"""

import xml.etree.ElementTree as ElementTree  # nosec B405 - TreeBuilder/Element only; parsing is _safe_expat
from xml.parsers import expat  # nosec B407 - hardened in _safe_expat


class UnsafeXmlError(ValueError):
    pass


def _forbid(kind):
    def handler(*_args):
        raise UnsafeXmlError(f"XML {kind} is not allowed in a 3D model file")
    return handler


def _qualified(name: str) -> str:
    """expat's ``uri}local`` (namespace_separator "}") as ElementTree's ``{uri}local``."""
    return "{" + name if "}" in name else name


def _safe_expat(builder: ElementTree.TreeBuilder):
    """An expat parser feeding ``builder`` that refuses DTDs, entity declarations and external
    entities and never reads parameter entities."""
    parser = expat.ParserCreate(namespace_separator="}")  # nosec B407 - handlers below refuse DTDs/entities
    parser.StartDoctypeDeclHandler = _forbid("document type declaration")
    parser.EntityDeclHandler = _forbid("entity declaration")
    parser.UnparsedEntityDeclHandler = _forbid("entity declaration")
    parser.ExternalEntityRefHandler = _forbid("external entity reference")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartElementHandler = lambda name, attributes: builder.start(
        _qualified(name), {_qualified(key): value for key, value in attributes.items()})
    parser.EndElementHandler = lambda name: builder.end(_qualified(name))
    parser.CharacterDataHandler = builder.data
    return parser


def parse_xml(source, chunk_bytes: int = 4 * 1024 ** 2):
    """The root element of an XML file path or binary file object, parsed by ``_safe_expat``."""
    builder = ElementTree.TreeBuilder()
    parser = _safe_expat(builder)
    handle = open(source, "rb") if isinstance(source, str) else source
    try:
        while True:
            chunk = handle.read(chunk_bytes)
            if not chunk:
                break
            parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except expat.ExpatError as e:
        raise ValueError(f"Invalid XML: {e}")
    finally:
        if isinstance(source, str):
            handle.close()
    return builder.close()
