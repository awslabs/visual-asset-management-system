# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An AST scanner for the two failure directions of one assertion mistake.

A test that asserts a BOUND or a REFUSAL can be wrong in two opposite ways, and both have been
found repeatedly in this suite:

  - OVER-TIGHT. The assertion pins a count, an order, an exact call sequence, or the exact text of
    a read expression. A strictly SAFER implementation -- an extra idempotent read, a re-check, an
    extra audit record, a widened filter, a reordered or widened projection, a TIGHTER cap -- then
    fails it, so the next author is trained toward the narrow implementation.
  - VACUOUS. The assertion iterates a collection of recorded calls and holds the only claim the
    test makes, with nothing establishing that the collection is non-empty. Zero recorded calls
    satisfy ``all(...)``, so a change that stops issuing the read entirely passes.

They are the same mistake seen from two sides: one cannot pass a safer implementation, the other
cannot fail a broken one. This module finds both, and ``test_assertion_shapes.py`` runs it as an
ordinary pytest test with a positive control, so a blind scanner cannot report zero.

WHAT THIS DELIBERATELY DOES NOT DO. There are thousands of legitimate ``assert_called_once`` uses
in this suite on ALLOW paths -- "the write happened", "the notification was sent" -- where pinning
one call is the property under test. A blanket ban would be wrong and would be ignored. The
distinction that matters is the PATH: pinning a count where the property is "this was refused" or
"this was bounded" is the defect. That distinction is not recoverable from the syntax, so the
enforcement scope is an explicit file list (see ``test_assertion_shapes.py``) rather than the whole
tree, and the subjects are narrowed to the two named collection families below.

THE SUBJECTS, AND THE HOLE THAT LET THE CLASS BACK IN. The first version of this scanner recognized
only RECORDED_NAMES -- ``calls``, ``call_args_list``, ``uploads`` -- so a length pinned over a
collection the code under test RETURNED (``assert len(labels) == MAX_REFERENCING_WORKFLOWS``, the
exact assertion one sweep had just replaced with ``<=``) was invisible: the pin could be planted back
into a cleaned file with every control still green, and TIGHTENING the production cap then turned the
test red while the scanner said nothing. PRODUCED_NAMES closes that direction, ``_accessor_call``
closes the same pin made one helper removed, and both are matched on whole identifier segments so
``input``/``output`` are not read as recorded writes.

WHAT IS STILL NOT SEEN, stated rather than implied: a length or sequence pinned over a local name in
neither family (``assert len(rows) == 3``) and a count compared to a local variable rather than to a
literal or a named cap. Widening the length rule to every collection was measured at 262 candidates
tree-wide, the large majority of them the legitimate accepted-at-exactly-the-limit control
(``len(model.metadata) == MAX_METADATA_ITEMS_PER_REQUEST`` after parsing a request AT the cap), which
is the "flags everything, gets ignored" failure mode. Naming the collection families is the trade.

Every finding is a candidate for a human to read, not a verdict. Findings carry the category, the
line, and the source line so a reviewer can dispose of them.
"""

import ast
import re
from pathlib import Path

# Names whose value is a recorded sequence of calls/reads. ``pager.calls`` and ``table.calls`` come
# from tests/pagingStub.py and the local recording stubs; ``call_args_list`` / ``mock_calls`` come
# from unittest.mock; ``state["uploads"]`` is the recording-dict idiom used by the physna harness.
RECORDED_NAMES = frozenset({
    "calls",
    "call_args_list",
    "mock_calls",
    "resumed_from",
    "uploads",
    "records",
    "reads",
    "writes",
    "deletes",
    "puts",
    "requests",
    "invocations",
})

# Names whose value is a collection the code under test PRODUCED on a bound or refusal path: the
# warning, label, and error lists a save returns when it truncated, refused, or reported. Tracked
# apart from RECORDED_NAMES because the mistakes differ. A recorded call list is iterated vacuously;
# a produced list is pinned by its LENGTH or by the exact wording of one entry. Pinning the length
# fails every implementation that reports the same fact in a different number of strings -- including
# a TIGHTER production cap, which is the strictly safer change and the one that has to stay green.
PRODUCED_NAMES = frozenset({
    "warnings",
    "labels",
    "errors",
    "messages",
    "flagged",
    "reported",
})

# unittest.mock assertions that pin a call COUNT (or a whole call sequence) rather than asserting
# that a call is among those made. ``assert_any_call`` is the containment form and is not listed.
PINNING_MOCK_METHODS = frozenset({
    "assert_called_once",
    "assert_called_once_with",
    "assert_has_calls",
})

# Read kwargs whose exact text a strictly safer implementation legitimately rewrites: a filter can
# be narrowed with an extra condition, a projection widened with a fourth attribute, an update
# expression reordered. Pinning the literal fails all of those. ``IndexName`` is deliberately
# absent: an index name is an identity, not an expression, so pinning it is a content assertion.
REWRITABLE_READ_KWARGS = frozenset({
    "ProjectionExpression",
    "FilterExpression",
    "KeyConditionExpression",
    "ConditionExpression",
    "UpdateExpression",
})

# Fragments that mark a constant as a BOUND rather than as an identity. Pinning a requested read
# SIZE equal to one of these fails every tighter page size, which is the safer implementation.
BOUND_CONSTANT_FRAGMENTS = ("MAX", "CAP", "CEILING", "LIMIT", "PAGE_SIZE", "BUDGET", "BOUND")

# Read kwargs that ASK for a quantity of data. ``Limit == PAGE_SIZE`` pins the request size to the
# top of its budget, so a read that asks for less -- strictly cheaper, strictly safer -- fails.
# Deliberately narrow: ``len(x) == MAX_...`` is NOT included, because the accepted-at-exactly-the-
# limit boundary control ("a value of exactly MAX_LENGTH parses") legitimately pins that equality.
COST_READ_KWARGS = frozenset({"Limit", "MaxKeys", "MaxItems", "PageSize", "MaxResults"})

CATEGORIES = (
    "pinned-call-count",
    "pinned-exact-sequence",
    "pinned-read-expression",
    "pinned-bound-equality",
    "pinned-source-text-count",
    "pinned-collection-length",
    "pinned-message-wording",
    "wording-classified-collection",
    "vacuous-recorded-iteration",
)


class Finding:
    """One candidate occurrence: where it is, which direction of the mistake, and the source line."""

    def __init__(self, path, line, category, detail, source_line):
        self.path = path
        self.line = line
        self.category = category
        self.detail = detail
        self.source_line = source_line

    @property
    def key(self):
        return (self.category, self.line)

    def __repr__(self):
        return f"{self.path}:{self.line} [{self.category}] {self.detail} -- {self.source_line}"


def _text(node):
    try:
        return ast.unparse(node)
    except Exception:                                        # pragma: no cover - unparse is total
        return ""


def _is_recorded(node, derived=frozenset()):
    """True when this expression evaluates to a recorded sequence of calls or reads.

    ``derived`` carries the local names a function has bound to a recorded collection
    (``attempted = [c.kwargs for c in table.query.call_args_list]``), so the same two mistakes made
    one variable removed are still seen.
    """
    if isinstance(node, ast.Attribute):
        return node.attr in RECORDED_NAMES
    if isinstance(node, ast.Name):
        return node.id in RECORDED_NAMES or node.id in derived
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return node.slice.value in RECORDED_NAMES
    return False


def _mentions_recorded(node, derived=frozenset()):
    return any(_is_recorded(child, derived) for child in ast.walk(node))


def _segments(name):
    """The lowercase word segments of an identifier (``_ownership_lookup_calls`` -> ... ``calls``)."""
    segments = []
    for chunk in re.split(r"[^A-Za-z0-9]+", name or ""):
        segments += [part.lower()
                     for part in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", chunk)]
    return segments


def _accessor_call(node, names):
    """A call to a helper whose NAME says it returns one of ``names`` (``_requests_sent(client)``).

    Both mistakes are routinely made one helper removed -- ``assert _deleted_keys(batch) == [...]``
    rather than ``assert batch.delete_item.call_args_list == [...]`` -- and a helper call is what a
    subject list of bare attribute names cannot see.

    Matching is on whole identifier SEGMENTS, never on substrings: ``put`` occurs inside ``input``
    and ``output``, so a substring rule would read every ``_input_exists_in_s3`` return-value
    assertion as a pinned write sequence.
    """
    if not isinstance(node, ast.Call):
        return False
    name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
    if not isinstance(name, str):
        return False
    return any(segment in names for segment in _segments(name))


def _is_produced(node, derived=frozenset()):
    """True when this expression evaluates to a collection the code under test produced."""
    if isinstance(node, ast.Attribute):
        return node.attr in PRODUCED_NAMES
    if isinstance(node, ast.Name):
        return node.id in PRODUCED_NAMES or node.id in derived
    return _accessor_call(node, PRODUCED_NAMES)


def _mentions_produced(node, derived=frozenset()):
    return any(_is_produced(child, derived) for child in ast.walk(node))


def _is_tracked(node, derived=frozenset()):
    """Recorded calls, produced collections, and either of them reached through a helper call."""
    return (_is_recorded(node, derived) or _is_produced(node, derived)
            or _accessor_call(node, RECORDED_NAMES))


def _mentions_tracked(node, derived=frozenset()):
    return any(_is_tracked(child, derived) for child in ast.walk(node))


def _derived_names(func):
    """Local names bound DIRECTLY to a recorded collection, or to a comprehension over one.

    Deliberately narrow -- a single assignment step from a recorded collection -- so an ordinary
    list of expected values never gets treated as a record of calls.
    """
    derived = set()
    for _ in range(3):                        # a short chain (calls -> keys -> paths) still resolves
        for node in ast.walk(func):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if value is None:
                continue
            sources = []
            if isinstance(value, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
                sources = [generator.iter for generator in value.generators]
            elif isinstance(value, ast.Call) and value.args:
                sources = list(value.args)
            elif isinstance(value, (ast.Name, ast.Attribute, ast.Subscript)):
                sources = [value]
            if not any(_is_recorded(source, derived) for source in sources):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    derived.add(target.id)
    return frozenset(derived)


def _len_of(node):
    """The argument of a ``len(...)`` call, or None."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len"
            and len(node.args) == 1):
        return node.args[0]
    return None


def _is_bound_constant(node):
    """A reference to a named cap/ceiling constant (``MAX_...``, ``..._CEILING``, ``..._LIMIT``)."""
    name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
    if not isinstance(name, str) or not name.isupper():
        return False
    return any(fragment in name for fragment in BOUND_CONSTANT_FRAGMENTS)


def _pinned_length_value(node):
    """A value that turns ``len(collection) == value`` into an exact-count pin.

    Zero is excluded: ``len(x) == 0`` is the refusal-by-absence form and is what the categories here
    recommend. ``True``/``False`` are excluded because a bool is an ``int`` in Python and
    ``len(x) == True`` is a typo, not a count pin.
    """
    if (isinstance(node, ast.Constant) and isinstance(node.value, int)
            and not isinstance(node.value, bool)):
        return node.value != 0
    return False


def _is_prose(node):
    """A string literal that reads as prose (a phrase), not as an identifier.

    Classifying a returned message set by a prose phrase pins the WORDING: a reworded message moves
    to the other class silently. An identifier (``db1:wf1``, ``WorkflowsByDateGSI``) carries no
    space and is content, not wording.
    """
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return False
    text = node.value.strip()
    if len(text) < 5 or text[:1] in ("{", "["):
        # A serialized dict or list is content, not wording -- ``'{"score": 0.9}'`` carries a space
        # and would otherwise read as a phrase.
        return False
    return " " in text and len(re.findall(r"[A-Za-z]{2,}", text)) >= 2


def _positive_lower_bound(compare):
    """Collections whose ``len(...)`` this comparison holds above zero.

    ``len(x) > 0``, ``len(x) >= 1``, ``2 <= len(x) <= CEILING``, ``len(x) != 0`` and ``len(x) == 3``
    all establish non-emptiness; ``len(x) <= CEILING`` and ``len(x) == 0`` do not.
    """
    bounded = set()
    operands = [compare.left] + list(compare.comparators)
    for index, op in enumerate(compare.ops):
        left, right = operands[index], operands[index + 1]
        for side, other, len_on_left in ((left, right, True), (right, left, False)):
            inner = _len_of(side)
            if inner is None:
                continue
            zero = isinstance(other, ast.Constant) and other.value == 0
            if isinstance(op, ast.NotEq) and zero:
                bounded.add(_text(inner))
            elif isinstance(op, ast.Eq) and not zero:
                bounded.add(_text(inner))
            elif len_on_left and isinstance(op, (ast.Gt, ast.GtE)):
                bounded.add(_text(inner))
            elif not len_on_left and isinstance(op, (ast.Lt, ast.LtE)):
                # ``2 <= len(x)`` -- the len sits on the RIGHT of the operator.
                bounded.add(_text(inner))
    return bounded


def _guarded_collections(func, derived=frozenset()):
    """Collection texts this function proves non-empty, so an iteration over them is not vacuous.

    Any of: a bare ``assert <collection>``; a ``len()`` comparison with a positive lower bound; a
    membership assertion over a comprehension of the collection (``X in [c["k"] for c in calls]``,
    which cannot hold on an empty collection); an index into the collection (``calls[0]``, which
    raises rather than passing); or a ``pager.assert_paged_to_exhaustion()`` call, whose own
    assertion is that every cursor handed out was resumed from.
    """
    guarded = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            if _is_recorded(node.test, derived):
                guarded.add(_text(node.test))
            for compare in [n for n in ast.walk(node.test) if isinstance(n, ast.Compare)]:
                guarded |= _positive_lower_bound(compare)
                for op, comparator in zip(compare.ops, compare.comparators):
                    if isinstance(op, (ast.In, ast.NotIn)):
                        for child in ast.walk(comparator):
                            if _is_recorded(child, derived):
                                guarded.add(_text(child))
        elif isinstance(node, ast.Subscript) and not isinstance(node.slice, ast.Slice):
            if _is_recorded(node.value, derived):
                guarded.add(_text(node.value))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "assert_paged_to_exhaustion":
                owner = _text(node.func.value)
                guarded.add(f"{owner}.calls")
                guarded.add(f"{owner}.resumed_from")
    return guarded


def _nonempty_literal(node):
    """A literal container with at least one element."""
    return isinstance(node, (ast.List, ast.Tuple, ast.Set)) and bool(node.elts)


def _comparison_forces_nonempty(compare, node):
    """True when this comparison cannot hold if ``node`` evaluates to an empty collection.

    ``{"/a", "/b"} <= {rel for rel, _b, _k in uploads}`` is a superset test against a non-empty
    literal: it fails on an empty collection, so the iteration is NOT vacuous even though nothing
    else in the function bounds the length. The mirror image (``{comprehension} <= {"/a", "/b"}``,
    "nothing outside this set was read") passes on an empty collection and stays a finding.
    """
    operands = [compare.left] + list(compare.comparators)
    for index, op in enumerate(compare.ops):
        left, right = operands[index], operands[index + 1]
        holds_node = (node in ast.walk(left), node in ast.walk(right))
        if isinstance(op, ast.Eq):
            if (holds_node[0] and _nonempty_literal(right)) or (
                    holds_node[1] and _nonempty_literal(left)):
                return True
        if isinstance(op, (ast.LtE, ast.Lt)) and holds_node[1] and _nonempty_literal(left):
            return True
        if isinstance(op, (ast.GtE, ast.Gt)) and holds_node[0] and _nonempty_literal(right):
            return True
        if isinstance(op, ast.In) and holds_node[1]:
            return True
    return False


def _parents(root):
    """child -> parent for every node under ``root``."""
    parents = {}
    for node in ast.walk(root):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _passes_on_empty(node, stop, parents):
    """Whether the assertion around ``node`` still PASSES when the iterated collection is empty.

    ``all(...)`` is True over an empty collection, so it passes and asserts nothing; ``any(...)`` is
    False, so an empty collection FAILS it and the assertion is self-guarding -- unless it is
    negated (``assert not any(...)``), which passes again. Returns None when neither aggregator is
    involved, leaving the comparison rule to decide.
    """
    aggregator = None
    negations = 0
    current = node
    while current is not None and current is not stop:
        parent = parents.get(current)
        if (aggregator is None and isinstance(parent, ast.Call)
                and isinstance(parent.func, ast.Name) and parent.func.id in ("all", "any")):
            aggregator = parent.func.id
        if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not):
            negations += 1
        current = parent
    if aggregator is None:
        return None
    passes = aggregator == "all"
    return passes if negations % 2 == 0 else not passes


def _iterated_recorded(node, derived=frozenset()):
    """The recorded collection an ``all()``/``any()`` comprehension or a ``for`` statement walks."""
    if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
        for generator in node.generators:
            if _is_recorded(generator.iter, derived):
                return generator.iter
    if isinstance(node, ast.For) and _is_recorded(node.iter, derived):
        return node.iter
    return None


def _comprehension_filter_prose(comprehension_node):
    """Prose literals this comprehension uses as a membership FILTER (``[w for w in x if 'p' in w]``).

    The filter is what splits one returned collection into two classes, which is the shape that
    turns a rewording into a silent reclassification. A prose literal elsewhere in the
    comprehension (in the element expression, say) is not that shape.
    """
    found = []
    for generator in comprehension_node.generators:
        for test in generator.ifs:
            for node in ast.walk(test):
                if not isinstance(node, ast.Compare):
                    continue
                for op in node.ops:
                    if isinstance(op, (ast.In, ast.NotIn)) and _is_prose(node.left):
                        found.append(node)
    return found


def _scan_function(path, func, lines, findings):
    derived = _derived_names(func)
    guarded = _guarded_collections(func, derived)
    parents = _parents(func)
    seen = set()

    def add(node, category, detail):
        line = getattr(node, "lineno", func.lineno)
        if (line, category) in seen:
            return
        seen.add((line, category))
        source = lines[line - 1].strip() if 0 < line <= len(lines) else ""
        findings.append(Finding(path, line, category, detail, source))

    for node in ast.walk(func):
        # --- Direction 1: pins -------------------------------------------------------------
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in PINNING_MOCK_METHODS:
                add(node, "pinned-call-count",
                    f"{node.func.attr} pins how many calls happened; an idempotent retry or an "
                    f"extra safety re-check fails it")

        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            for compare in _comprehension_filter_prose(node):
                add(compare, "wording-classified-collection",
                    f"the collection is split by the prose substring {compare.left.value!r}; a "
                    f"reworded message changes class silently")

        if isinstance(node, ast.Compare):
            operands = [node.left] + list(node.comparators)
            for index, op in enumerate(node.ops):
                if not isinstance(op, ast.Eq):
                    continue
                left, right = operands[index], operands[index + 1]
                for side, other in ((left, right), (right, left)):
                    if isinstance(side, ast.Attribute) and side.attr == "call_count":
                        add(node, "pinned-call-count",
                            f"{_text(side)} is pinned to an exact value; assert an upper bound "
                            f"instead")
                    inner = _len_of(side)
                    if inner is not None and _mentions_recorded(inner, derived):
                        if not (isinstance(other, ast.Constant) and other.value == 0):
                            add(node, "pinned-call-count",
                                f"len({_text(inner)}) is pinned to an exact value; assert an upper "
                                f"bound instead")
                    elif inner is not None and _mentions_tracked(inner, derived):
                        # The same pin over a collection the code PRODUCED rather than over a record
                        # of calls. Split in two because the safer implementation differs: pinned to
                        # a cap, the safer change is TIGHTENING the cap; pinned to a plain count, it
                        # is reporting the same facts in a different number of strings.
                        if _is_bound_constant(other):
                            add(node, "pinned-bound-equality",
                                f"len({_text(inner)}) is pinned EQUAL to the bound {_text(other)}; "
                                f"tightening that constant is strictly safer and would fail this, "
                                f"so compare with <=")
                        elif _pinned_length_value(other):
                            add(node, "pinned-collection-length",
                                f"len({_text(inner)}) is pinned to exactly {_text(other)}; assert "
                                f"an upper bound, or the SET of things named, instead")
                    if (isinstance(side, (ast.List, ast.Tuple)) and side.elts
                            and _mentions_tracked(other, derived)):
                        add(node, "pinned-exact-sequence",
                            f"{_text(other)} is compared to an exact sequence; use set containment "
                            f"over the meaningful tuples instead")
                    if (isinstance(side, ast.Subscript) and isinstance(side.slice, ast.Constant)
                            and side.slice.value in REWRITABLE_READ_KWARGS
                            and isinstance(other, ast.Constant)):
                        add(node, "pinned-read-expression",
                            f"{side.slice.value} is pinned to an exact literal; a widened filter "
                            f"or projection is safer and would fail this")
                    if (isinstance(side, ast.Subscript) and not isinstance(side.slice, ast.Slice)
                            and _is_prose(other) and _mentions_produced(side, derived)
                            and not (isinstance(side.slice, ast.Constant)
                                     and side.slice.value in REWRITABLE_READ_KWARGS)):
                        add(node, "pinned-message-wording",
                            f"{_text(side)} is pinned to the exact wording {other.value!r}; a "
                            f"reworded message fails it although the property still holds -- assert "
                            f"the identifier the message must name instead")
                    if (isinstance(side, ast.Call) and isinstance(side.func, ast.Attribute)
                            and side.func.attr == "count"):
                        add(node, "pinned-source-text-count",
                            f"{_text(side)} pins an exact source-text occurrence count")
                    if (isinstance(side, ast.Subscript) and isinstance(side.slice, ast.Constant)
                            and side.slice.value in COST_READ_KWARGS
                            and _is_bound_constant(other)):
                        add(node, "pinned-bound-equality",
                            f"the requested {side.slice.value} is pinned EQUAL to the bound "
                            f"{_text(other)}; a read that asks for less is cheaper and safer and "
                            f"would fail this, so compare with <=")

        # --- Direction 2: vacuity ----------------------------------------------------------
        iterated = _iterated_recorded(node, derived)
        if iterated is None:
            continue
        collection = _text(iterated)
        if collection in guarded:
            continue
        if isinstance(node, ast.For):
            body = [stmt for stmt in node.body if not isinstance(stmt, ast.Pass)]
            if not body or not all(isinstance(stmt, ast.Assert) for stmt in body):
                continue
            add(node, "vacuous-recorded-iteration",
                f"the for-loop over {collection} holds the only assertion and nothing proves "
                f"{collection} is non-empty; zero recorded calls pass")
        else:
            # Only an assert's TEST makes a claim. The same comprehension inside an assert's failure
            # MESSAGE (``assert x == y, f"... {[c for c in calls]}"``) asserts nothing and is not a
            # finding.
            tests = [n.test for n in ast.walk(func)
                     if isinstance(n, ast.Assert) and node in ast.walk(n.test)]
            if not tests:
                continue
            # ``assert any(... for c in calls)`` FAILS on an empty collection, so it guards itself;
            # ``assert all(...)`` and ``assert not any(...)`` both pass on one.
            if any(_passes_on_empty(node, test, parents) is False for test in tests):
                continue
            if any(_comparison_forces_nonempty(compare, node) for compare in ast.walk(func)
                   if isinstance(compare, ast.Compare) and node in ast.walk(compare)):
                continue
            add(node, "vacuous-recorded-iteration",
                f"the comprehension over {collection} is asserted with nothing proving "
                f"{collection} is non-empty; zero recorded calls pass")


def scan_source(source, path="<string>"):
    """Every candidate occurrence in one module's source, ordered by line."""
    tree = ast.parse(source)
    lines = source.splitlines()
    collected = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _scan_function(path, node, lines, collected)
    # A nested function is walked both on its own and as part of its parent, so one occurrence can
    # be reported twice. The line + category pair is the identity.
    findings = []
    seen = set()
    for finding in collected:
        if finding.key in seen:
            continue
        seen.add(finding.key)
        findings.append(finding)
    findings.sort(key=lambda finding: (finding.line, finding.category))
    return findings


def scan_file(path):
    path = Path(path)
    return scan_source(path.read_text(encoding="utf-8"), path.as_posix())
