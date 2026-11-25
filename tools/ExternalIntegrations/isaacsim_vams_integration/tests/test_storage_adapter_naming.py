"""Asset folder names in the Content window: '<asset name> (<asset id>)', at most 255 bytes, full id kept."""
import re

import pytest

from vams.connector.isaacsim.storage_adapter.naming import MAX_SEGMENT_BYTES, asset_folder, asset_id_of

# The VAMS backend's own patterns (backend/backend/common/validators.py): object_name_pattern for asset
# names, filename_pattern for asset ids.
NAME = re.compile(r'^[a-zA-Z0-9\-._\s]{1,256}$')
ASSET_ID = re.compile(r'^(?!.*[<>:"\/\\|?*])(?!.*[.\s]$)[\w\s.,\'-]{1,254}[^.\s]$')
UUID_ID = "x02834e7b-a2c8-4802-8e6c-5b3ab18e2206"

CASES = [
    ("scene", UUID_ID, "scene (x02834e7b-a2c8-4802-8e6c-5b3ab18e2206)"),
    ("robot arm v2.1", UUID_ID, "robot arm v2.1 (x02834e7b-a2c8-4802-8e6c-5b3ab18e2206)"),
    ("A" * 256, UUID_ID, None),
    ("word " * 51 + "x", UUID_ID, None),
    ("tabs\tand\nnewlines  here", UUID_ID, "tabs and newlines here (x02834e7b-a2c8-4802-8e6c-5b3ab18e2206)"),
    ("n" * 256, "z" * 250, "(" + "z" * 250 + ")"),
    ("n" * 10, "z" * 253, "(" + "z" * 253 + ")"),
    ("n" * 10, "z" * 254, "z" * 254),
    ("n" * 10, "\u00e9" * 120, "n" * 10 + " (" + "\u00e9" * 120 + ")"),
    ("name", "abc)", "name (abc))"),
    ("name", "abc(", "name (abc()"),
    ("name", "it's, ok-1", "name (it's, ok-1)"),
]


@pytest.mark.parametrize("name,asset_id,expected", CASES)
def test_folder_name_fits_keeps_the_full_id_and_parses_back(name, asset_id, expected):
    assert NAME.fullmatch(name)
    assert ASSET_ID.fullmatch(asset_id)
    folder = asset_folder(name, asset_id)
    assert len(folder.encode()) <= MAX_SEGMENT_BYTES
    assert "/" not in folder and folder == folder.strip()
    assert asset_id_of(folder) == asset_id
    if expected is not None:
        assert folder == expected


def test_a_name_that_does_not_fit_is_cut_and_marked():
    folder = asset_folder("A" * 256, UUID_ID)
    assert len(folder.encode()) == MAX_SEGMENT_BYTES
    assert folder.endswith("... (" + UUID_ID + ")")


def test_a_cut_never_leaves_a_trailing_space_before_the_marker():
    folder = asset_folder("word " * 51 + "x", UUID_ID)
    assert " ..." not in folder


def test_the_name_part_is_ignored_when_resolving():
    assert asset_id_of("a name from before a rename (" + UUID_ID + ")") == UUID_ID


def test_a_bare_id_resolves_to_itself():
    assert asset_id_of(UUID_ID) == UUID_ID


def test_a_custom_limit_is_honoured():
    folder = asset_folder("a long asset name", UUID_ID, limit=50)
    assert len(folder.encode()) <= 50 and asset_id_of(folder) == UUID_ID
