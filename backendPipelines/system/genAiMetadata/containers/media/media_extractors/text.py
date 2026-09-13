# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Text and code (.txt .md .json .xml .yaml .yml .toml .ini .cfg .inf .log .py .js .ts .sql .sh .ps1 .ipynb
.html .htm): encoding, line and word counts, a language guess and an excerpt -> sys_text. A `.json` tileset is
promoted to tiles3d and a GeoJSON `.json` to `data` (sys_geo); bytes that are not text at all demote the file to
`other`."""

import json
import os
import re
from html import unescape
from html.parser import HTMLParser
from typing import Dict, Optional, Tuple

from charset_normalizer import from_bytes

from . import geo, tiles3d
from .common import (
    CLASS_TEXT,
    TEXT_HEAD_BYTES,
    BranchResult,
    ExtractContext,
    human_count,
    other_fallback,
    truncate_text,
)

_CODE_LANGUAGES = {".py": "python", ".js": "javascript", ".ts": "typescript", ".sql": "sql", ".sh": "shell",
                   ".ps1": "powershell"}
_KINDS = {".html": "html", ".htm": "html", ".xml": "xml", ".md": "markdown", ".json": "json", ".yaml": "yaml",
          ".yml": "yaml", ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".inf": "ini", ".log": "log",
          ".ipynb": "notebook", ".txt": "plain"}
# Function words per language; the guess is the language whose words appear most in the first 5,000 words.
_STOPWORDS = {
    "en": {"the", "and", "of", "to", "in", "is", "that", "for", "with", "as", "on", "it", "this", "are", "be"},
    "de": {"der", "die", "und", "das", "ist", "nicht", "mit", "ein", "eine", "auf", "für", "den", "von", "zu", "sich"},
    "fr": {"le", "la", "les", "et", "des", "est", "une", "pour", "dans", "que", "qui", "pas", "sur", "avec", "un"},
    "es": {"el", "la", "los", "las", "de", "que", "y", "en", "un", "una", "por", "con", "para", "es", "del"},
    "it": {"il", "la", "di", "che", "e", "un", "una", "per", "non", "con", "sono", "del", "della", "gli", "le"},
    "pt": {"o", "a", "os", "as", "de", "que", "e", "do", "da", "em", "um", "uma", "para", "com", "não"},
    "nl": {"de", "het", "een", "en", "van", "is", "dat", "op", "te", "zijn", "voor", "met", "niet", "aan", "ook"},
}
_MIN_WORDS_FOR_GUESS = 20
_STOPWORD_SAMPLE = 5000
_CONTROL_BYTES = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_WORD = re.compile(r"\S+")
_LETTERS = re.compile(r"[^\W\d_]+")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_CHUNK = 1024 * 1024
_TITLE_MAX_CHARS = 500
# A .json larger than the head is read whole up to this size so a root tileset is recognised whatever its size.
JSON_PARSE_MAX_BYTES = 64 * 1024 * 1024


def decode_head(raw: bytes) -> Tuple[Optional[str], Optional[str]]:
    """(text, Python codec name), or (None, None) when the bytes are not text. UTF-8 (with or without a BOM)
    and BOM-marked UTF-16 are recognised directly; anything else goes through charset-normalizer."""
    if not raw:
        return "", "utf_8"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace"), "utf_16"
    if _CONTROL_BYTES.search(raw[:8192]):
        return None, None
    try:
        return raw.decode("utf-8-sig"), ("utf_8_sig" if raw.startswith(b"\xef\xbb\xbf") else "utf_8")
    except UnicodeDecodeError:
        pass
    best = from_bytes(raw).best()
    if best is None:
        return None, None
    return str(best), best.encoding


class _VisibleText(HTMLParser):
    """Collects text nodes, skipping <script> and <style> bodies."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def strip_html(markup: str) -> Tuple[str, Optional[str]]:
    """(visible text, <title> or None)."""
    parser = _VisibleText()
    parser.feed(markup)
    parser.close()
    title = _TITLE.search(markup)
    return " ".join(parser.parts), (unescape(title.group(1)).strip() if title else None)


def notebook_text(text: str) -> Tuple[str, Dict[str, object]]:
    """Cell sources joined in order plus cell counts and the kernel name; a notebook that does not parse as
    one is returned as the plain text it is."""
    try:
        notebook = json.loads(text)
    except ValueError:
        return text, {}
    cells = notebook.get("cells") if isinstance(notebook, dict) else None
    if not isinstance(cells, list):
        return text, {}
    parts = []
    counts: Dict[str, int] = {}
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        kind = str(cell.get("cell_type", "raw"))
        counts[kind] = counts.get(kind, 0) + 1
        source = cell.get("source", "")
        parts.append("".join(source) if isinstance(source, list) else str(source))
    facts: Dict[str, object] = {
        "notebookCells": len(cells), "codeCells": counts.get("code", 0), "markdownCells": counts.get("markdown", 0)}
    kernel = ((notebook.get("metadata") or {}).get("kernelspec") or {}).get("name")
    if kernel:
        facts["kernel"] = str(kernel)
    return "\n\n".join(parts), facts


def guess_language(text: str) -> str:
    words = _LETTERS.findall(text.lower())
    if len(words) < _MIN_WORDS_FOR_GUESS:
        return "unknown"
    sample = words[:_STOPWORD_SAMPLE]
    scores = {language: sum(1 for word in sample if word in stopwords) for language, stopwords in _STOPWORDS.items()}
    language, best = max(scores.items(), key=lambda item: item[1])
    return language if best >= max(3, len(sample) * 0.02) else "unknown"


def count_newlines(path: str) -> int:
    total = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                return total
            total += chunk.count(b"\n")


def load_json_document(path: str, head: str, encoding: str, truncated: bool, size: int) -> Tuple[Optional[object], Optional[str]]:
    """(parsed document, warning). The decoded head is parsed when it is the whole file; a larger file is
    re-read whole, up to JSON_PARSE_MAX_BYTES, so a tileset root bigger than the head still parses. Beyond
    the bound, or when the text is not JSON, the document is None and the warning says why."""
    if not truncated:
        text = head
    elif size > JSON_PARSE_MAX_BYTES:
        return None, f"JSON of {size:,} bytes is above the {JSON_PARSE_MAX_BYTES:,}-byte parse bound; indexed as plain text"
    else:
        with open(path, "rb") as handle:
            text = handle.read().decode(encoding or "utf-8", "replace")
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, f"JSON does not parse ({exc}); indexed as plain text"


def _line_count(path: str, text: str, truncated: bool) -> int:
    """Lines in the file: counted over the decoded text when the whole file was read, otherwise streamed over
    the file's bytes. A trailing newline ends the last line rather than starting another."""
    if truncated:
        with open(path, "rb") as handle:
            handle.seek(-1, os.SEEK_END)
            ends_with_newline = handle.read(1) == b"\n"
        return count_newlines(path) + (0 if ends_with_newline else 1)
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def extract_text(path: str, ctx: ExtractContext) -> BranchResult:
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        head = handle.read(TEXT_HEAD_BYTES)
    truncated = size > len(head)
    text, encoding = decode_head(head)
    if text is None:
        return other_fallback(ctx, "not decodable as text")
    extension = ctx.extension.lower()
    result = BranchResult(file_class=CLASS_TEXT)
    if extension == ".json":
        parsed, parse_warning = load_json_document(path, text, encoding, truncated, size)
        if parse_warning:
            result.warnings.append(parse_warning)
        if tiles3d.is_tileset(parsed):
            return tiles3d.extract_tiles3d(parsed, ctx)
        if geo.is_geojson(parsed):
            return geo.extract_geo(parsed, ctx)
    code_language = _CODE_LANGUAGES.get(extension)
    kind = "code" if code_language else _KINDS.get(extension, "plain")
    body = text
    sys_text: Dict[str, object] = {
        "kind": kind, "encoding": encoding, "sizeBytes": size, "truncatedForAnalysis": truncated}
    if kind == "html":
        body, title = strip_html(text)
        if title:
            sys_text["title"] = title[:_TITLE_MAX_CHARS]
            result.facts["title"] = title[:_TITLE_MAX_CHARS]
    elif kind == "notebook":
        body, notebook_facts = notebook_text(text)
        sys_text.update(notebook_facts)
    if code_language:
        sys_text["codeLanguage"] = code_language
    else:
        # `language` is a promotion source (ext_language): written only when the guess settled on a language.
        language = guess_language(body)
        if language != "unknown":
            sys_text["language"] = language
    sys_text["lineCount"] = _line_count(path, text, truncated)
    sys_text["wordCount"] = len(_WORD.findall(body))
    if not truncated:
        sys_text["chars"] = len(text)
    result.attributes["sys_text"] = sys_text
    result.facts["lines"] = human_count(int(sys_text["lineCount"]), "line")
    result.facts["words"] = human_count(int(sys_text["wordCount"]), "word")
    result.facts["encoding"] = str(encoding)
    result.facts["textKind"] = code_language or kind
    if sys_text.get("language"):
        result.facts["language"] = str(sys_text["language"])
    result.text_excerpt = truncate_text(body, ctx.max_text_chars)
    return result
