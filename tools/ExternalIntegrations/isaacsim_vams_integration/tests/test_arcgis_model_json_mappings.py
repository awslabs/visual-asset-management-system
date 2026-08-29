"""Static checks on the ArcGIS Pro connector's JSON model bindings.

The ArcGIS connector has no test project, so nothing in this repository executes
``Models/VamsModels.cs``. One defect class there is worse than a compile error: `VamsCliService`
deserializes with ``PropertyNameCaseInsensitive = true``, so two members of the same class whose
effective JSON names differ only in case — a computed convenience property ``Key`` alongside
``[JsonPropertyName("key")] FileKey``, say — collide while System.Text.Json builds the type's
metadata. That throws ``InvalidOperationException`` and fails the ENTIRE response, not just the one
field, and it happens at first deserialization: the C# compiler cannot see it, and neither can a
reviewer skimming for the attribute.

These tests parse the model file and assert the effective JSON names are unique per class. They live
in the Isaac Sim suite because it is the only Python test root under ``tools/ExternalIntegrations``;
they exercise no Isaac Sim code.
"""

import re
from pathlib import Path

import pytest

MODEL_FILE = (Path(__file__).resolve().parents[2]
              / "arcgispro-connector-for-vams" / "Models" / "VamsModels.cs")

# Classes the connector deserializes into. Asserted present so a parser that silently matched
# nothing cannot report the file clean.
EXPECTED_CLASSES = {
    "DatabaseListResponse", "Database", "AssetListResponse", "Asset", "CurrentVersion",
    "AssetLocation", "FileListResponse", "AssetFile", "AuthStatusResponse",
    "ProfileInfoResponse", "ProfileInfo", "VamsErrorResponse", "AssetDownloadResponse",
    "DownloadedFile", "FailedDownload",
}

# Computed convenience members on AssetFile. Each shadows or derives from a mapped field, so each
# must be excluded from serialization; `Key` is the one that actually collides.
ASSETFILE_COMPUTED_MEMBERS = {
    "Path", "Key", "Type", "HasPrimaryType", "State", "AddedAt", "LastModifiedDateTime",
}

_CLASS_RE = re.compile(r"^\s*public\s+class\s+(\w+)", re.MULTILINE)
# A property declaration, whether expression-bodied (`=> x;`), brace-bodied on the same line
# (`{ get; set; }`), or brace-bodied on the next line (nothing after the name).
_PROPERTY_RE = re.compile(r"^\s*public\s+.+?\s+(\w+)\s*(?:=>|\{|$)")
_ATTRIBUTE_RE = re.compile(r"^\s*\[(.+)\]\s*$")
_JSON_NAME_RE = re.compile(r'JsonPropertyName\s*\(\s*"([^"]*)"\s*\)')


def _parse_classes(source):
    """{class name: [(property name, effective json name or None, [attributes])]}.

    A deliberately shallow line parser: the model file is a flat list of DTOs with no nested types,
    and a real C# parser would be more machinery than the check is worth. ``_positive_control``
    tests below prove it actually resolves properties and actually fires on a collision.
    """
    bounds = [(m.group(1), m.start()) for m in _CLASS_RE.finditer(source)]
    classes = {}
    for index, (name, start) in enumerate(bounds):
        end = bounds[index + 1][1] if index + 1 < len(bounds) else len(source)
        classes[name] = _parse_properties(source[start:end])
    return classes


def _parse_properties(body):
    properties = []
    pending_attributes = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("///") or stripped.startswith("//"):
            continue
        attribute = _ATTRIBUTE_RE.match(line)
        if attribute:
            pending_attributes.append(attribute.group(1))
            continue
        prop = _PROPERTY_RE.match(line)
        if prop and not stripped.startswith("public class"):
            attributes = list(pending_attributes)
            json_name = None
            for text in attributes:
                found = _JSON_NAME_RE.search(text)
                if found:
                    json_name = found.group(1)
            properties.append((prop.group(1), json_name, attributes))
        pending_attributes = []
    return properties


def _is_ignored(attributes):
    return any("JsonIgnore" in text for text in attributes)


def _collisions(classes):
    """[(class, effective name lowered, [colliding property names])] for every case-insensitive
    duplicate among serialized members."""
    found = []
    for class_name, properties in classes.items():
        by_effective_name = {}
        for prop, json_name, attributes in properties:
            if _is_ignored(attributes):
                continue
            by_effective_name.setdefault((json_name or prop).lower(), []).append(prop)
        for effective, props in sorted(by_effective_name.items()):
            if len(props) > 1:
                found.append((class_name, effective, props))
    return found


@pytest.fixture(scope="module")
def classes():
    assert MODEL_FILE.is_file(), (
        f"{MODEL_FILE} not found — the ArcGIS model file moved, and this check would otherwise "
        "pass by resolving nothing"
    )
    return _parse_classes(MODEL_FILE.read_text(encoding="utf-8"))


class TestTheParserResolvesTheModelFile:
    """Positive controls. A checker that matches nothing also reports "clean"."""

    def test_every_expected_class_is_found(self, classes):
        assert EXPECTED_CLASSES <= set(classes)

    def test_the_file_listing_model_has_its_mapped_keys(self, classes):
        mapped = {json_name for _prop, json_name, _attrs in classes["AssetFile"] if json_name}
        assert {"fileName", "relativePath", "key", "size", "isFolder", "isArchived",
                "primaryType", "dateCreatedCurrentVersion", "contentType", "lastModified",
                "versionId", "etag", "previewFile"} <= mapped

    def test_the_download_response_model_has_its_mapped_keys(self, classes):
        mapped = {json_name for _prop, json_name, _attrs in classes["AssetDownloadResponse"]
                  if json_name}
        assert {"overall_success", "total_files", "successful_files", "failed_files",
                "failed_downloads"} <= mapped


class TestEffectiveJsonNamesAreUnique:
    """`PropertyNameCaseInsensitive = true` makes duplicate detection case-insensitive, so a
    collision throws while building type metadata and fails the whole response."""

    def test_no_class_has_a_case_insensitive_duplicate(self, classes):
        assert _collisions(classes) == []

    def test_every_computed_member_of_assetfile_is_ignored(self, classes):
        """The specific instance: `Key` derives from the mapped "key" field. Naming the members
        makes a dropped attribute a pointed failure rather than a generic collision report."""
        unignored = {prop for prop, _json_name, attributes in classes["AssetFile"]
                     if prop in ASSETFILE_COMPUTED_MEMBERS and not _is_ignored(attributes)}
        assert unignored == set(), f"missing [JsonIgnore] on: {sorted(unignored)}"

    def test_the_computed_members_are_all_still_present(self, classes):
        """Guards the assertion above against being satisfied by a rename or a deletion."""
        declared = {prop for prop, _json_name, _attrs in classes["AssetFile"]}
        assert ASSETFILE_COMPUTED_MEMBERS <= declared


class TestTheCollisionCheckerFires:
    """The check has to fail on known-bad input, or "no collisions" means nothing."""

    _BAD = '''
        public class AssetFile
        {
            [JsonPropertyName("key")]
            public string FileKey { get; set; } = string.Empty;

            public string Key => FileKey;
        }
    '''

    _GOOD = '''
        public class AssetFile
        {
            [JsonPropertyName("key")]
            public string FileKey { get; set; } = string.Empty;

            [JsonIgnore]
            public string Key => FileKey;
        }
    '''

    def test_a_missing_jsonignore_is_reported(self):
        parsed = _parse_classes(self._BAD)
        assert [prop for prop, _n, _a in parsed["AssetFile"]] == ["FileKey", "Key"]
        assert _collisions(parsed) == [("AssetFile", "key", ["FileKey", "Key"])]

    def test_the_jsonignore_form_is_accepted(self):
        parsed = _parse_classes(self._GOOD)
        assert _collisions(parsed) == []
