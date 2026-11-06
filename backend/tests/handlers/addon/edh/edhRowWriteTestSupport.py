# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Readers of the session-row writes an EDH handler makes through ``update_item`` on the faked table:
what each write set, and the TTL expiry a DELETED row is given. A write SETs ``#a<i>`` to ``:v<i>``; a
name or value its condition reads is placed under a placeholder of its own and is not counted as written.
``ConditionalRowTable`` applies those writes to one stored copy of the row as DynamoDB does."""

import re
import time
from numbers import Number

from botocore.exceptions import ClientError

_TOKEN = re.compile(r"\s*(<>|<=|>=|[()=<>,]|[#:]?\w+)")


def _set_attributes(call):
    """The attributes one update_item call SETs, by name."""
    names = call.kwargs["ExpressionAttributeNames"]
    values = call.kwargs["ExpressionAttributeValues"]
    return {name: values[":v" + placeholder[2:]] for placeholder, name in names.items()
            if placeholder.startswith("#a") and ":v" + placeholder[2:] in values}


def written_values(fakes, attribute):
    """Every value written for ``attribute`` across the row updates, in call order."""
    out = []
    for call in fakes.table.update_item.call_args_list:
        written = _set_attributes(call)
        if attribute in written:
            out.append(written[attribute])
    return out


def update_attribute_sets(fakes):
    """The attribute names of each row update, in call order, updatedAt aside."""
    return [set(_set_attributes(call)) - {"updatedAt"} for call in fakes.table.update_item.call_args_list]


def _comparable(stored, operand):
    """Whether DynamoDB compares the two values at all: both strings, or both numbers."""
    if isinstance(stored, str) and isinstance(operand, str):
        return True
    return (isinstance(stored, Number) and isinstance(operand, Number)
            and not isinstance(stored, bool) and not isinstance(operand, bool))


class ConditionalRowTable:
    """The faked table's update_item applied to one stored copy of the row as DynamoDB applies it: the
    condition is checked against the stored item first, and one that does not hold raises
    ConditionalCheckFailedException and writes nothing; otherwise the SET and REMOVE clauses are applied,
    creating the item under its key when there was none, and a write asking for ``ReturnValues='ALL_NEW'``
    is answered with the item it left. The stored item always carries its key. A condition is read as AND,
    OR and NOT over attribute_exists, attribute_not_exists, attribute_type and comparison terms, with
    parentheses. A value stored as None is DynamoDB's NULL; a comparison across types, or with an attribute
    the item lacks, does not hold -- except ``<>``, which does."""

    def __init__(self, **item):
        self.item = item

    @classmethod
    def missing(cls):
        """A table in which the row does not exist."""
        table = cls()
        table.item = None
        return table

    def __call__(self, **kwargs):
        names, values = kwargs.get("ExpressionAttributeNames", {}), kwargs.get("ExpressionAttributeValues", {})
        stored = None if self.item is None else {**kwargs["Key"], **self.item}
        condition = kwargs.get("ConditionExpression")
        if condition is not None and not _Condition(condition, stored or {}, names, values).holds():
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException",
                                         "Message": "The conditional request failed"}}, "UpdateItem")
        item = dict(stored if stored is not None else kwargs["Key"])
        sets, _, removes = kwargs["UpdateExpression"].partition(" REMOVE ")
        for assignment in sets.removeprefix("SET ").split(", "):
            name, value = assignment.split(" = ")
            item[names.get(name, name)] = values[value]
        for name in filter(None, removes.split(", ")):
            item.pop(names.get(name, name), None)
        self.item = item
        return {"Attributes": dict(item)} if kwargs.get("ReturnValues") == "ALL_NEW" else {}

    def read(self, **kwargs):
        """The faked table's get_item: a copy of the stored item, or no Item while the row does not exist."""
        return {} if self.item is None else {"Item": {**kwargs["Key"], **self.item}}


class _Condition:
    """One condition expression read against a stored item."""

    def __init__(self, expression, stored, names, values):
        self.tokens = _TOKEN.findall(expression)
        self.position = 0
        self.stored, self.names, self.values = stored, names, values

    def holds(self):
        result = self._disjunction()
        if self.position != len(self.tokens):
            raise AssertionError(f"a condition this fake does not read: {' '.join(self.tokens)}")
        return result

    def _take(self, expected=None):
        token = self.tokens[self.position]
        if expected is not None and token != expected:
            raise AssertionError(f"expected {expected!r} in the condition, read {token!r}")
        self.position += 1
        return token

    def _peek(self):
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def _disjunction(self):
        result = self._conjunction()
        while self._peek() == "OR":
            self._take()
            result = self._conjunction() or result
        return result

    def _conjunction(self):
        result = self._term()
        while self._peek() == "AND":
            self._take()
            result = self._term() and result
        return result

    def _operand(self, token):
        """(present, value) of a path or a value placeholder."""
        if token.startswith(":"):
            return True, self.values[token]
        name = self.names[token] if token.startswith("#") else token
        return name in self.stored, self.stored.get(name)

    def _term(self):
        token = self._take()
        if token == "NOT":
            return not self._term()
        if token == "(":
            result = self._disjunction()
            self._take(")")
            return result
        if token in ("attribute_exists", "attribute_not_exists", "attribute_type"):
            self._take("(")
            present, value = self._operand(self._take())
            if token == "attribute_type":
                self._take(",")
                _, wanted = self._operand(self._take())
                self._take(")")
                if wanted != "NULL":
                    raise AssertionError(f"an attribute type this fake does not read: {wanted}")
                return present and value is None
            self._take(")")
            return present if token == "attribute_exists" else not present
        operator = self._take()
        (present, stored), (_, operand) = self._operand(token), self._operand(self._take())
        if operator == "<>":
            return not (present and _comparable(stored, operand) and stored == operand)
        if not present or not _comparable(stored, operand):
            return False
        return {"=": stored == operand, "<": stored < operand, "<=": stored <= operand,
                ">": stored > operand, ">=": stored >= operand}[operator]


def is_about_a_week_ahead(expiry):
    """An integer epoch instant between six and eight days from now: the TTL a DELETED row is given."""
    return isinstance(expiry, int) and 6 * 86400 < expiry - int(time.time()) < 8 * 86400
