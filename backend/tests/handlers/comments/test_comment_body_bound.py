# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`commentBody` is bounded on the way in, at both endpoints that accept one.

The comment handlers write `event["body"]["commentBody"]` straight into the comment item with no
Pydantic request model in front of it, so the `validate()` entry each handler declares is the only
length bound the field has. Two links have to hold for that bound to exist, and each is worthless
alone: the handler must name a validator that bounds length, and that validator must actually reject
an oversize value. Both are asserted here, with the name read out of the handler source so a
repointed call site fails rather than being silently exempted.

The module-scoped autouse fixtures in `test_add_comment_handler.py` / `test_edit_comment_handler.py`
replace `validate` with a stub that accepts everything, which is why this lives in its own file.
"""

import pathlib
import re
import sys

import pytest

# A body a caller can realistically submit: a pasted log excerpt in a review note. It has to stay
# under the bound, or this file would assert the endpoints reject their own legitimate traffic.
LONG_LEGITIMATE_BODY = 'a' * 8000

OVERSIZE_BODY = 'a' * 100000

HANDLERS = ('addComment.py', 'editComment.py')


def _validators():
    return sys.modules['common.validators']


def _declared_validator_name(handler_file_name):
    source = (pathlib.Path(__file__).resolve().parents[3]
              / 'backend' / 'handlers' / 'comments' / handler_file_name).read_text(encoding='utf-8')
    names = re.findall(r'"commentBody":\s*\{"value":[^}]*"validator":\s*"([A-Za-z_0-9]+)"', source)
    assert len(names) == 1, f"expected one commentBody validator entry in {handler_file_name}, got {names}"
    return names[0]


@pytest.mark.unit
@pytest.mark.parametrize('handler_file_name', HANDLERS)
class TestTheCommentBodyIsBoundedAtBothEndpoints:

    def test_the_declared_validator_rejects_an_oversize_body(self, handler_file_name):
        name = _declared_validator_name(handler_file_name)
        (valid, message) = _validators().validate(
            {'commentBody': {'value': OVERSIZE_BODY, 'validator': name}})
        assert valid is False, f"{handler_file_name} declares {name}, which accepts a 100 000-char body"
        assert 'commentBody' in message

    def test_the_declared_validator_accepts_a_long_legitimate_body(self, handler_file_name):
        """OVER-TIGHTENING CATCHER: a bound low enough to reject a long review note would be an
        outage on the comment endpoints, and passes the rejection test above just as well."""
        name = _declared_validator_name(handler_file_name)
        (valid, message) = _validators().validate(
            {'commentBody': {'value': LONG_LEGITIMATE_BODY, 'validator': name}})
        assert valid is True, f"{handler_file_name} declares {name}, which rejected 8000 chars: {message}"
