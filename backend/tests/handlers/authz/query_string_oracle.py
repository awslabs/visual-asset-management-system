# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A small model of how OpenSearch answers the query_string clauses the search builders emit, over the
field kinds the VAMS indexes map, for the two engines VAMS runs on.

* Analyzed text (``str_*``, ``list_tags``): lower-cased words; a ``<field>.keyword`` reference holds the
  field's exact value.
* ``flat_object`` (``MD_``, ``AB_``): one Lucene field of leaf values (``<root>._value``), one of
  ``<root>.<key>=<value>`` terms (``<root>._valueAndPath``) and the root field itself, holding the key
  paths (``<root>.<key>``). A query names the root or a key (``<root>.<key>``), and the two engines answer
  it the same way (#1285):

  - Exact (term, phrase) and prefix queries are rewritten: on the root to the leaf values, on a key to
    that key's value; ``<root>._value`` is read as a key called ``_value``, which no record has.
  - Other wildcard queries are not rewritten: on the root they match the key paths, on a key nothing, and
    on ``<root>._value`` / ``<root>._valueAndPath`` the values / ``path=value`` terms.

  Observed on Amazon OpenSearch Serverless (a test collection) and on a provisioned OpenSearch 3.5 domain
  (``PROVISIONED_3_5_PROBES`` below). An earlier model had provisioned domains rewrite every wildcard
  query; the live domain does not. ``ENGINES`` stays a parameter of every test, so a difference found
  later has one place to go.

A term is quoted (exact), plain (exact), a prefix (its only wildcard one trailing ``*``) or a wildcard.
Clauses join terms with ``AND`` or with whitespace under default_operator OR. Anything else raises.
"""

import re

ENGINES = ("serverless", "provisioned")
FLAT_OBJECT_ROOTS = ("MD_", "AB_")

# query_string filters sent directly to the asset and file indexes of a provisioned OpenSearch 3.5 domain
# holding the records in PROBE_ASSETS / PROBE_FILES, and the number of records each matched there:
# (index, query_string, hits). test_search_metadata_patterns_both_engines.py holds the model to them.
PROVISIONED_3_5_PROBES = (
    ("asset", "MD_:PRJ\\-325", 3),
    ("asset", "MD_:Train*", 3),
    ("asset", "MD_:PRJ*", 4),
    ("asset", "MD_:*325", 0),
    ("asset", "MD_:*Beta", 0),
    ("asset", "MD_:Tr?in\\-Alpha", 0),
    ("asset", "MD_:*PRJ\\-325*", 0),
    ("asset", "MD_:*RJ*", 0),
    ("asset", "MD_._value:PRJ\\-325", 0),
    ("asset", "MD_._value:Train*", 0),
    ("asset", "MD_._value:*325", 3),
    ("asset", "MD_._value:*Beta", 1),
    ("asset", "MD_._value:*PRJ\\-325*", 3),
    ("asset", "MD_._value:Tr*n\\-A*", 2),
    ("asset", "MD_._value:*housing\\ insp*", 1),
    ("asset", "MD_._value:*housing* AND MD_._value:*inspection*", 1),
    ("asset", "MD_.ProjectCode:PRJ\\-325", 3),
    ("asset", "MD_.ProjectCode:PRJ*", 4),
    ("asset", "MD_.ProjectCode:*325", 0),
    ("asset", "MD_.TrainSet:*Beta", 0),
    ("asset", "MD_._valueAndPath:MD_.ProjectCode\\=*325", 3),
    ("asset", "MD_._valueAndPath:MD_.TrainSet\\=*Beta", 1),
    ("asset", "MD_._valueAndPath:MD_.TrainSet\\=Tr?in\\-Alpha", 2),
    ("asset", "MD_._valueAndPath:MD_.ProjectCode\\=PRJ*", 0),
    ("file", "MD_:*log", 0),
    ("file", "MD_:maint*", 1),
    ("file", "MD_:*maintenance*", 0),
    ("file", "MD_:*maintenance* OR AB_:*maintenance*", 0),
    ("file", "MD_._value:*log", 1),
    ("file", "MD_._value:*log OR AB_._value:*log", 1),
    ("file", "MD_._value:*ator\\ No* OR AB_._value:*ator\\ No*", 1),
    ("file", "MD_._value:*maintenance*", 2),
    ("file", "MD_._value:*maintenance* OR AB_._value:*maintenance*", 2),
    ("file", "MD_._valueAndPath:MD_.docType\\=*Notes", 1),
)

# The probed records: the metadata (and, on files, attribute) values that the counts above depend on.
PROBE_ASSETS = {
    "fz-a/cad-part": {"str_assetname": "cad-part", "MD_": {"ProjectCode": "PRJ-325"}},
    "fz-a/operator-notes": {"str_assetname": "operator-notes",
                            "MD_": {"ProjectCode": "PRJ-325", "TrainSet": "Train-Alpha"}},
    "fz-b/delete-me": {"str_assetname": "delete-me", "MD_": {"ProjectCode": "PRJ-325"}},
    "fz-b/checklist": {"str_assetname": "checklist", "MD_": {"ProjectCode": "PRJ-999"}},
    "fz-a/gearbox": {"str_assetname": "gearbox",
                     "MD_": {"TrainSet": "Train-Beta", "Inspection": "gearbox housing inspection"}},
    "fz-b/gear-photo": {"str_assetname": "gear-photo", "MD_": {"TrainSet": "Train-Alpha"}},
    "fz-b/gearbox-stl": {"str_assetname": "gearbox-stl", "MD_": {"Inspection": "gearbox housing review"}},
}
PROBE_FILES = {
    "pump-manual/centrifugal_pump_manual.pdf": {
        "str_key": "/centrifugal_pump_manual.pdf", "str_assetname": "pump-manual",
        "MD_": {"category": "maintenance log"}, "AB_": {"sys_format": "PDF"}},
    "operator-notes/operator_notes.txt": {
        "str_key": "/operator_notes.txt", "str_assetname": "operator-notes",
        "MD_": {"docType": "Operator Notes",
                "genai_keywords": "operator notes, night shift, maintenance log, conveyor belt"},
        "AB_": {"sys_format": "TXT"}},
    "sensor-log/sensor_readings.csv": {
        "str_key": "/sensor_readings.csv", "str_assetname": "sensor-log", "MD_": {}, "AB_": {"sys_format": "CSV"}},
}


def tokens(text):
    """query_string tokens: the text split at unescaped whitespace outside quotes and parentheses."""
    out, current, depth, quoted, i = [], "", 0, False, 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            current += text[i:i + 2]
            i += 2
            continue
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        if ch.isspace() and not quoted and depth == 0:
            if current:
                out.append(current)
            current = ""
        else:
            current += ch
        i += 1
    if current:
        out.append(current)
    return out


def field_split(token):
    """(field, value) of ``field:value`` at the first unescaped colon outside quotes, else (None, token)."""
    i, quoted = 0, False
    while i < len(token):
        ch = token[i]
        if ch == "\\":
            i += 2
            continue
        if ch == '"':
            quoted = not quoted
        if ch == "(" and not quoted:
            return None, token
        if ch == ":" and not quoted:
            return token[:i], token[i + 1:]
        i += 1
    return None, token


def unescape(term):
    return re.sub(r"\\(.)", r"\1", term)


def _unescaped_wildcards(term):
    """Positions of the unescaped ``*`` and ``?`` of ``term``."""
    found, i = [], 0
    while i < len(term):
        if term[i] == "\\":
            i += 2
            continue
        if term[i] in "*?":
            found.append((i, term[i]))
        i += 1
    return found


def kind(term):
    """'exact', 'prefix', 'wildcard' or 'exists' for one query_string term."""
    if len(term) >= 2 and term.startswith('"') and term.endswith('"'):
        return "exact"
    if term == "*":
        return "exists"
    wildcards = _unescaped_wildcards(term)
    if not wildcards:
        return "exact"
    if wildcards == [(len(term) - 1, "*")]:
        return "prefix"
    return "wildcard"


def _pattern(term, lower):
    out, i = [], 0
    while i < len(term):
        ch = term[i]
        if ch == "\\" and i + 1 < len(term):
            out.append(re.escape(term[i + 1].lower() if lower else term[i + 1]))
            i += 2
            continue
        out.append("(?s:.*)" if ch == "*" else "(?s:.)" if ch == "?" else re.escape(ch.lower() if lower else ch))
        i += 1
    return re.compile("".join(out))


def _flat_object_terms(document, field, term_kind):
    """The terms a query of ``term_kind`` on flat_object ``field`` is matched against, or None when the
    field is not a flat_object field. The same on both engines."""
    root, _, key = field.partition(".")
    if root not in FLAT_OBJECT_ROOTS:
        return None
    leaves = document.get(root) or {}
    values = [str(v) for v in leaves.values()]
    rewritten = term_kind in ("exact", "prefix", "exists")
    if not key:
        if rewritten:
            return values
        return [f"{root}.{k}" for k in leaves]
    if key in ("_value", "_valueAndPath"):
        if rewritten:
            return [str(leaves[key])] if key in leaves else []
        return values if key == "_value" else [f"{root}.{k}={v}" for k, v in leaves.items()]
    if rewritten:
        return [str(leaves[key])] if key in leaves else []
    return []


def term_matches(term, document, field, engine):
    """Whether one query_string term on ``field`` matches ``document``."""
    assert engine in ENGINES
    term_kind = kind(term)
    flat = _flat_object_terms(document, field, term_kind)
    if flat is not None:
        if term_kind == "exists":
            return bool(flat)
        if term_kind == "exact":
            text = term[1:-1] if term.startswith('"') and term.endswith('"') else term
            return unescape(text) in flat
        if term_kind == "prefix":
            return any(v.startswith(unescape(term[:-1])) for v in flat)
        return any(_pattern(term, lower=False).fullmatch(v) for v in flat)
    if field.endswith(".keyword"):
        value = document.get(field[: -len(".keyword")])
        values = [str(v) for v in (value if isinstance(value, list) else [value]) if v is not None]
        if term_kind == "exact":
            text = term[1:-1] if term.startswith('"') and term.endswith('"') else term
            return unescape(text) in values
        return any(_pattern(term, lower=False).fullmatch(v) for v in values)
    return _text_term_matches(term, document, field)


def _words(text):
    return [w for w in re.split(r"[^0-9a-z]+", text.lower()) if w]


def _text_term_matches(term, document, field):
    value = document.get(field)
    texts = [str(v) for v in (value if isinstance(value, list) else [value] if value is not None else [])]
    words_by_value = [_words(v) for v in texts]
    if term.startswith('"') and term.endswith('"') and len(term) >= 2:
        phrase = _words(unescape(term[1:-1]))
        return any(phrase == ws[i:i + len(phrase)] for ws in words_by_value for i in range(len(ws)))
    if _unescaped_wildcards(term):
        regex = _pattern(term, lower=True)
        return any(regex.fullmatch(w) for ws in words_by_value for w in ws)
    wanted = _words(unescape(term))
    # A term the analyzer splits matches when any of its words does (default_operator OR inside it)
    return any(w in ws for ws in words_by_value for w in wanted)


def all_fields(document):
    """The fields an unscoped term with no field list is searched in: every top-level field."""
    return list(document)


def matches(text, document, engine, fields=None):
    """Whether query_string ``text`` (default_operator OR) matches ``document`` on ``engine``. An unscoped
    term is searched in ``fields``, or in every field when the clause names none."""
    parts = tokens(text)
    operands = [t for t in parts if t not in ("AND", "OR")]
    every = "AND" in parts
    if every:
        assert "OR" not in parts, "mixed operators are not modelled"

    def operand(token, default_fields):
        field, value = field_split(token)
        scope = [field] if field else default_fields
        if value.startswith("(") and value.endswith(")"):
            return matches(value[1:-1], document, engine, scope)
        return any(term_matches(value, document, f, engine) for f in scope)

    default_fields = fields or all_fields(document)
    results = [operand(t, default_fields) for t in operands]
    return all(results) if every else any(results)


def clause_matches(clause, document, engine):
    """Whether one builder clause (``term`` on a keyword subfield, or ``query_string``) matches."""
    if "term" in clause:
        (field, value), = clause["term"].items()
        return term_matches(f'"{value}"', document, field, engine)
    if "bool" in clause:
        body = clause["bool"]
        must = body.get("must", [])
        should = body.get("should", [])
        if must and not all(clause_matches(c, document, engine) for c in must):
            return False
        if should:
            return sum(clause_matches(c, document, engine) for c in should) >= body.get("minimum_should_match", 1)
        return True
    body = clause["query_string"]
    assert body.get("default_operator", "OR") == "OR"
    return matches(body["query"], document, engine, body.get("fields"))


def unscoped_terms(text):
    """Terms of a field-scoped query_string that carry no field and so would search every field."""
    return [t for t in tokens(text) if t not in ("AND", "OR") and field_split(t)[0] is None]
