"""`sanitize_asset_id` returns an id a new asset may use (#1946).

A character the new-asset rule does not admit is replaced with '_', including
the last one: '!', '#', '%', ')' and a symbol such as '™' used to survive
because the shared rule's last position was broader than its body.
"""

import pytest

from vamscli.commands.industry.engineering.plm.plm import sanitize_asset_id


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("part#", "part_"),
        ("part%", "part_"),
        ("part)", "part_"),
        ("part!", "part_"),
        ("part™", "part_"),
        ("part#mid", "part_mid"),
        ("parté", "part_"),
        ("part.name", "part.name"),
        ("part-name", "part-name"),
        ("part name", "part name"),
        ("part.", "part"),
        ("part ", "part"),
    ],
)
def test_it_returns_an_id_the_new_rule_accepts(value, expected):
    assert sanitize_asset_id(value) == expected
