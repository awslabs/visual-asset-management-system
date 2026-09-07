"""Guard against Pydantic v1 `Field()` kwargs that silently validate nothing.

Pydantic v1 collects any keyword `Field()` does not recognize into `FieldInfo.extra` instead of
raising. A v2 spelling like `pattern=` therefore becomes an inert annotation: the model imports
cleanly, every test passes, and the field is entirely unconstrained. `regex=` is the v1 spelling.

`strip_whitespace=` is inert the same way — it is a `class Config` / `constr` option, not a
`Field()` constraint. No model declares it any more: a field that should trim wires
`common.validators.trim_name` as a `pre=True` validator, and a field that should not carries no
declaration at all. The two classes of field are enumerated as a RULE below rather than as an
inventory of survivors, so a newly added field lands on one side or the other loudly.

These tests introspect the parsed models rather than grepping the source, so a constraint declared
across several lines is covered the same as a single-line one.
"""

import importlib
import pkgutil

import pytest

import models

# Free-form caller text (`description`, `comment`) on a REQUEST model trims its surrounding
# whitespace, so a padded value and its clean spelling are one record and the length check is applied
# to what will be stored. The fields that deliberately do NOT trim are named below with their reason;
# everything else carrying one of those field names must trim, which is asserted as a rule rather than
# as a list of survivors — a newly added request field then fails here instead of quietly joining the
# untrimmed side.
NO_TRIM_FREE_TEXT = {
    # READ path. These are response and record models, so trimming would rewrite a row on the way
    # OUT — changing what a caller is told is stored rather than what gets stored.
    "models/assetExport.py::AssetExportAssetModel.description": "response",
    "models/assetsV3.py::AssetResponseModel.description": "response",
    "models/assetsV3.py::AssetVersionListItemModel.description": "response",
    "models/assetsV3.py::AssetVersionResponseModel.comment": "response",
    "models/assetsV3.py::CurrentVersionModel.description": "response",
    "models/databases.py::GetDatabaseResponseModel.description": "response",
    "models/pipelines.py::PipelineRecordV2.description": "record",
    "models/pipelines.py::PipelineResponseModel.description": "response",
    "models/pipelines.py::PipelineTemplateRecord.description": "record",
    "models/pipelines.py::TemplateResponseModel.description": "response",
    "models/roleConstraints.py::ConstraintResponseModel.description": "response",
    "models/roleConstraints.py::RoleResponseModel.description": "response",
    "models/tag.py::TagResponseModel.description": "response",
    "models/tag.py::TagTypeResponseModel.description": "response",
    "models/workflows.py::WorkflowRecordV2.description": "record",
    "models/workflows.py::WorkflowResponseModel.description": "response",
    # WRITE path, deliberately left untrimmed for now. None of these ever declared a trim, so there
    # is no inert declaration misreporting them, and none carries a min_length — so an untrimmed
    # value cannot be rejected for a length its trimmed form would satisfy, and no create/update pair
    # among them diverges (both sides are untrimmed). Trimming them is a behaviour change on a
    # separate set of endpoints and is not part of this one.
    "models/indexing.py::AssetIndexRequest.description": "no min_length; write path untrimmed",
    "models/pipelines.py::CreatePipelineRequestModel.description": "no min_length; pairs with Update",
    "models/pipelines.py::UpdatePipelineRequestModel.description": "no min_length; pairs with Create",
    "models/pipelines.py::CreateTemplateRequestModel.description": "no min_length; pairs with Update",
    "models/pipelines.py::UpdateTemplateRequestModel.description": "no min_length; pairs with Create",
    "models/pipelines.py::TemplateTagFieldModel.description": "no min_length; tag-schema field label",
    "models/roleConstraints.py::TemplateMetadata.description": "no min_length; template header",
    "models/roleConstraints.py::TemplateVariableDefinition.description": "no min_length; variable doc",
    "models/workflows.py::CreateWorkflowRequestModel.description": "no min_length; pairs with Update",
    "models/workflows.py::UpdateWorkflowRequestModel.description": "no min_length; pairs with Create",
}

# A uuid-shaped bucket id, which CreateDatabaseRequestModel requires.
_DEFAULT_BUCKET_ID = "11111111-2222-3333-4444-555555555555"


def _model_classes():
    """Every model class declared in `backend/models/`, as (module, class) pairs."""
    for module_info in pkgutil.iter_modules(models.__path__):
        try:
            module = importlib.import_module(f"models.{module_info.name}")
        except Exception:
            # A model module that cannot import under test env is covered by its own suite.
            continue
        for name in dir(module):
            candidate = getattr(module, name)
            fields = getattr(candidate, "__fields__", None)
            if isinstance(fields, dict) and getattr(candidate, "__module__", "") == module.__name__:
                yield module_info.name, name, candidate


def _swallowed(kwarg):
    """Fields whose `Field()` call passed `kwarg`, which pydantic v1 silently ignored."""
    found = []
    for module_name, class_name, cls in _model_classes():
        for field_name, field in cls.__fields__.items():
            if kwarg in (getattr(field.field_info, "extra", {}) or {}):
                found.append(f"models/{module_name}.py::{class_name}.{field_name}")
    return sorted(found)


def _tag_body(**overrides):
    body = {"tagName": "Prod", "description": "Production", "tagTypeName": "Environment"}
    body.update(overrides)
    return body


def _asset_body(**overrides):
    body = {
        "databaseId": "mydb",
        "assetId": "part.glb",
        "assetName": "Landing Gear",
        "description": "A description",
        "isDistributable": True,
    }
    body.update(overrides)
    return body


def _completed_file(relative_key):
    return {
        "relativeKey": relative_key,
        "uploadIdS3": "upload-1",
        "parts": [{"PartNumber": 1, "ETag": "etag-1"}],
    }


def _constraint_body(**overrides):
    body = {
        "identifier": "db-admin",
        "name": "Database Admin",
        "description": "Full database access",
        "objectType": "asset",
        "criteriaAnd": [{"field": "assetName", "operator": "contains", "value": "x"}],
        "groupPermissions": [
            {"groupId": "group-a", "permission": "GET", "permissionType": "allow"}
        ],
    }
    body.update(overrides)
    return body


@pytest.mark.unit
class TestNoDeadFieldKwargs:
    """`Field(pattern=...)` is the v2 spelling and constrains nothing in v1."""

    def test_no_field_declares_pattern(self):
        offenders = _swallowed("pattern")
        assert offenders == [], (
            "Field(pattern=...) is silently ignored by pydantic v1 and validates nothing. "
            "Use regex=. Offending fields:\n  " + "\n  ".join(offenders))

    def test_no_field_declares_strip_whitespace(self):
        """`strip_whitespace` on `Field()` lands in `FieldInfo.extra` and transforms nothing, so a
        padded value reaches the length check, the regex check and storage exactly as submitted. A
        declaration is therefore worse than nothing: it states a normalization the model does not
        perform.
        """
        offenders = _swallowed("strip_whitespace")
        assert offenders == [], (
            "strip_whitespace= on Field() is silently ignored by pydantic v1 and transforms "
            "nothing. To trim, wire common.validators.trim_name as a pre=True validator; to leave "
            "the value verbatim, declare nothing and say why in a comment. Offending fields:\n  "
            + "\n  ".join(offenders))

    def test_the_regex_convention_is_live_where_declared(self):
        """A field declaring a regex must expose it as the real v1 constraint."""
        live = [
            f"{module_name}::{class_name}.{field_name}"
            for module_name, class_name, cls in _model_classes()
            for field_name, field in cls.__fields__.items()
            if getattr(field.field_info, "regex", None)
        ]
        assert len(live) >= 26, f"Expected at least 26 live regex= constraints, found {len(live)}"


@pytest.mark.unit
class TestNameAndIdFieldsTrimWhitespace:
    """Names and ids trim the surrounding whitespace run; interior whitespace is preserved.

    `object_name_pattern` admits `\\s`, so without trimming ' Prod ', 'Prod\\n' and 'Prod' are three
    distinct records a user cannot tell apart, and a grant written against the clean name does not
    cover the padded one. The id patterns differ from each other and the difference decides the
    blast radius: `id_pattern` and `userid_pattern` carry no whitespace class at all, so trimming a
    databaseId or userId only accepts a spelling the rule itself refuses. `filename_pattern`, the
    asset-id rule, admits `\\s` everywhere except the final character, so a LEADING-whitespace asset
    id satisfies it — trimming one is the same near-duplicate reconciliation as trimming a name.
    """

    def test_a_tag_name_and_tag_type_name_trim(self):
        from models.tag import CreateTagRequestModel, UpdateTagRequestModel

        for model_cls in (CreateTagRequestModel, UpdateTagRequestModel):
            model = model_cls(**_tag_body(tagName=" Prod \n", tagTypeName="\tEnvironment "))
            assert model.tagName == "Prod"
            assert model.tagTypeName == "Environment"

    def test_a_tag_type_name_trims_on_the_tag_type_models(self):
        from models.tag import CreateTagTypeRequestModel, UpdateTagTypeRequestModel

        for model_cls in (CreateTagTypeRequestModel, UpdateTagTypeRequestModel):
            model = model_cls(tagTypeName=" Environment ", description="Deployment environment")
            assert model.tagTypeName == "Environment"

    def test_a_role_name_trims(self):
        from models.roleConstraints import CreateRoleRequestModel, UpdateRoleRequestModel

        for model_cls in (CreateRoleRequestModel, UpdateRoleRequestModel):
            model = model_cls(roleName=" admin ", description="Administrators")
            assert model.roleName == "admin"

    def test_a_constraint_identifier_name_and_group_id_trim(self):
        from models.roleConstraints import CreateConstraintRequestModel

        model = CreateConstraintRequestModel(**_constraint_body(
            identifier=" db-admin ",
            name=" Database Admin ",
            groupPermissions=[
                {"groupId": " group a ", "permission": "GET", "permissionType": "allow"}
            ],
        ))
        assert model.identifier == "db-admin"
        assert model.name == "Database Admin"
        assert model.groupPermissions[0].groupId == "group a"

    def test_a_template_name_trims(self):
        from models.roleConstraints import (
            TemplateConstraintDefinition,
            TemplateMetadata,
            TemplateVariableDefinition,
        )

        assert TemplateMetadata(name=" database-admin ").name == "database-admin"
        assert TemplateVariableDefinition(name=" DATABASE_ID ").name == "DATABASE_ID"
        assert TemplateConstraintDefinition(
            name=" Asset Read ",
            description="Read assets",
            objectType="asset",
            groupPermissions=[{"action": "GET", "type": "allow"}],
        ).name == "Asset Read"

    def test_a_user_id_trims_on_every_model_that_keys_a_row_on_it(self):
        from models.apiKeys import CreateApiKeyRequestModel
        from models.roleConstraints import (
            CreateUserRolesRequestModel,
            DeleteUserRolesRequestModel,
            UpdateUserRolesRequestModel,
            UserPermissionModel,
        )
        from models.user import CreateCognitoUserRequestModel

        assert CreateCognitoUserRequestModel(
            userId=" bob ", email="bob@example.com").userId == "bob"
        assert CreateApiKeyRequestModel(
            apiKeyName=" Build Key ", userId=" bob ", description="CI").userId == "bob"
        assert UserPermissionModel(
            userId=" bob ", permission="GET", permissionType="allow").userId == "bob"
        assert DeleteUserRolesRequestModel(userId=" bob ").userId == "bob"
        for model_cls in (CreateUserRolesRequestModel, UpdateUserRolesRequestModel):
            model = model_cls(userId=" bob ", roleName=[" admin ", "viewer"])
            assert model.userId == "bob"
            assert model.roleName == ["admin", "viewer"]

    def test_an_api_key_name_trims(self):
        from models.apiKeys import CreateApiKeyRequestModel, CreateUserApiKeyRequestModel

        assert CreateApiKeyRequestModel(
            apiKeyName=" Build Key ", userId="bob", description="CI").apiKeyName == "Build Key"
        assert CreateUserApiKeyRequestModel(
            apiKeyName=" Build Key ", description="CI",
            expiresAt="2027-01-01").apiKeyName == "Build Key"

    def test_an_asset_name_database_id_and_asset_id_trim(self):
        from models.assetsV3 import (
            CompleteExternalUploadRequestModel,
            CompleteUploadRequestModel,
            CopyFileRequestModel,
            CreateAssetRequestModel,
            IngestAssetCompleteRequestModel,
            IngestAssetInitializeRequestModel,
            InitializeUploadRequestModel,
        )

        model = CreateAssetRequestModel(**_asset_body(
            databaseId=" mydb ", assetId=" part.glb ", assetName=" Landing Gear \n"))
        assert (model.databaseId, model.assetId, model.assetName) == (
            "mydb", "part.glb", "Landing Gear")

        model = IngestAssetInitializeRequestModel(**_asset_body(
            databaseId=" mydb ", assetId=" part.glb ", assetName=" Landing Gear ",
            files=[{"relativeKey": "part.glb/a.txt", "num_parts": 1}]))
        assert (model.databaseId, model.assetId, model.assetName) == (
            "mydb", "part.glb", "Landing Gear")

        model = IngestAssetCompleteRequestModel(**_asset_body(
            databaseId=" mydb ", assetId=" part.glb ", assetName=" Landing Gear ",
            uploadId="u-1", files=[_completed_file("part.glb/a.txt")]))
        assert (model.databaseId, model.assetId, model.assetName) == (
            "mydb", "part.glb", "Landing Gear")

        for model_cls, files in (
            (InitializeUploadRequestModel, [{"relativeKey": "/a.txt", "num_parts": 1}]),
            (CompleteUploadRequestModel, [_completed_file("/a.txt")]),
            (CompleteExternalUploadRequestModel, [{"relativeKey": "/a.txt",
                                                  "tempKey": "tmp/a.txt"}]),
        ):
            model = model_cls(assetId=" part.glb ", databaseId=" mydb ",
                              uploadType="assetFile", files=files)
            assert (model.assetId, model.databaseId) == ("part.glb", "mydb")

        copy = CopyFileRequestModel(sourcePath="/a/b", destinationPath="/a/c",
                                    destinationAssetId=" part.glb ",
                                    destinationDatabaseId=" mydb ")
        assert (copy.destinationAssetId, copy.destinationDatabaseId) == ("part.glb", "mydb")

    def test_the_physna_viewer_database_id_and_asset_id_trim(self):
        from models.physnaViewer import PhysnaViewerRequestModel

        model = PhysnaViewerRequestModel(
            databaseId=" mydb ", assetId=" part.glb ", relativePath="/part.glb")
        assert (model.databaseId, model.assetId) == ("mydb", "part.glb")

    def test_a_leading_whitespace_asset_id_trims_on_every_path_that_addresses_an_asset(self):
        """The one id shape a caller can submit padded, so the one whose trim retargets a lookup.

        `filename_pattern` forbids a trailing space or dot but admits a leading one, so ' part.glb'
        is a legal asset id that a create request can persist. Every path that addresses an asset by
        id must therefore agree on the trimmed spelling, or one of them resolves to the padded row
        while the rest do not.
        """
        from models.assetsV3 import (
            CompleteUploadRequestModel,
            CopyFileRequestModel,
            CreateAssetRequestModel,
            InitializeUploadRequestModel,
        )
        from models.physnaViewer import PhysnaViewerRequestModel

        assert CreateAssetRequestModel(**_asset_body(assetId=" part.glb")).assetId == "part.glb"
        assert InitializeUploadRequestModel(
            assetId=" part.glb", databaseId="mydb", uploadType="assetFile",
            files=[{"relativeKey": "/a.txt", "num_parts": 1}]).assetId == "part.glb"
        assert CompleteUploadRequestModel(
            assetId=" part.glb", databaseId="mydb", uploadType="assetFile",
            files=[_completed_file("/a.txt")]).assetId == "part.glb"
        assert CopyFileRequestModel(
            sourcePath="/a/b", destinationPath="/a/c",
            destinationAssetId=" part.glb").destinationAssetId == "part.glb"
        assert PhysnaViewerRequestModel(
            databaseId="mydb", assetId=" part.glb", relativePath="/part.glb").assetId == "part.glb"

    def test_a_whitespace_only_name_is_refused(self):
        """Trimming leaves the empty string, which min_length=1 then rejects.

        Untrimmed, '   ' satisfies both `object_name_pattern` and min_length and becomes a tag
        whose name renders as nothing.
        """
        from aws_lambda_powertools.utilities.parser import ValidationError
        from models.tag import CreateTagRequestModel

        with pytest.raises(ValidationError):
            CreateTagRequestModel(**_tag_body(tagName="   "))

    def test_a_database_id_trims_surrounding_whitespace(self):
        from models.databases import CreateDatabaseRequestModel

        model = CreateDatabaseRequestModel(
            databaseId=" mydb ", description="A description", defaultBucketId=_DEFAULT_BUCKET_ID)
        assert model.databaseId == "mydb"

    @pytest.mark.parametrize("padded", [" GLOBAL ", " global ", "\tGloBal\n"])
    def test_padding_does_not_smuggle_a_reserved_database_id_past_its_guard(self, padded):
        """Trimming runs before the reserved-keyword check, not after it.

        `GLOBAL` is the unscoped keyword and may not be a database id. The check reads the value the
        root validator receives, so trimming ahead of it is what keeps a padded spelling from
        arriving as a distinct, accepted id.
        """
        from aws_lambda_powertools.utilities.parser import ValidationError
        from models.databases import CreateDatabaseRequestModel

        with pytest.raises(ValidationError):
            CreateDatabaseRequestModel(
                databaseId=padded, description="A description",
                defaultBucketId=_DEFAULT_BUCKET_ID)


@pytest.mark.unit
class TestTrimmingDoesNotReachBeyondNamesAndIds:
    """Controls. Trimming a path or a free-text field would rewrite what the caller submitted."""

    def test_a_clean_name_is_returned_verbatim(self):
        from models.assetsV3 import CreateAssetRequestModel
        from models.databases import CreateDatabaseRequestModel
        from models.tag import CreateTagRequestModel

        tag = CreateTagRequestModel(**_tag_body())
        assert (tag.tagName, tag.tagTypeName) == ("Prod", "Environment")
        asset = CreateAssetRequestModel(**_asset_body())
        assert (asset.databaseId, asset.assetId, asset.assetName) == (
            "mydb", "part.glb", "Landing Gear")
        assert CreateDatabaseRequestModel(
            databaseId="mydb", description="A description",
            defaultBucketId=_DEFAULT_BUCKET_ID).databaseId == "mydb"

    def test_interior_whitespace_in_a_name_survives(self):
        from models.assetsV3 import CreateAssetRequestModel
        from models.roleConstraints import GroupPermissionModel

        assert CreateAssetRequestModel(
            **_asset_body(assetName="  Landing  Gear  ")).assetName == "Landing  Gear"
        assert GroupPermissionModel(
            groupId="group a", permission="GET", permissionType="allow").groupId == "group a"

    def test_an_s3_key_keeps_its_surrounding_whitespace(self):
        """A trailing space is a legitimate S3 key character, so trimming a path would retarget
        the operation at a different object."""
        from models.assetsV3 import CopyFileRequestModel, MoveFileRequestModel

        for model_cls in (CopyFileRequestModel, MoveFileRequestModel):
            model = model_cls(sourcePath="/a/b ", destinationPath="/a/c ")
            assert (model.sourcePath, model.destinationPath) == ("/a/b ", "/a/c ")

    def test_the_physna_relative_path_keeps_its_surrounding_whitespace(self):
        from models.physnaViewer import PhysnaViewerRequestModel

        model = PhysnaViewerRequestModel(
            databaseId="mydb", assetId="part.glb", relativePath="/part.glb ")
        assert model.relativePath == "/part.glb "


@pytest.mark.unit
class TestFreeTextTrimsOnTheWriteFieldsThatDeclaredIt:
    """`description` and `comment` on the request models that carried the inert declaration now trim
    for real, and the trim runs BEFORE the length check.

    The ordering is the whole behaviour change, so it is asserted on the parsed value rather than on
    the declaration: a `pre=True` validator hands the trimmed string to `min_length`, which means a
    padded value whose trimmed form is too short is rejected where it used to be accepted and stored.
    """

    def test_every_free_text_field_either_trims_or_is_declared_not_to(self):
        """The RULE, so a newly added `description`/`comment` cannot quietly join the untrimmed side.

        Stated as a partition over the fields derived from the models rather than as a list of the
        ones that trim: an inventory goes stale silently, whereas this fails on a new field until it
        is either given a trim or named in NO_TRIM_FREE_TEXT with a reason.
        """
        trimmed, untrimmed = [], []
        for module_name, class_name, cls in _model_classes():
            for field_name, field in cls.__fields__.items():
                if field_name not in ("description", "comment"):
                    continue
                key = f"models/{module_name}.py::{class_name}.{field_name}"
                (trimmed if getattr(field, "pre_validators", None) else untrimmed).append(key)
        # Non-vacuity: the walk found the fields at all, and both sides are populated.
        assert len(trimmed) >= 21, (
            f"only {len(trimmed)} free-text field(s) trim; the walk found too few")
        assert untrimmed, "no untrimmed free-text field was found, so the partition below is vacuous"
        undeclared = sorted(set(untrimmed) - set(NO_TRIM_FREE_TEXT))
        assert undeclared == [], (
            "these free-text fields neither trim nor are declared in NO_TRIM_FREE_TEXT. Wire "
            "common.validators.trim_name as a pre=True validator, or add the field to that map with "
            "the reason it must keep its whitespace:\n  " + "\n  ".join(undeclared))
        stale = sorted(set(NO_TRIM_FREE_TEXT) & set(trimmed))
        assert stale == [], (
            "these fields are declared as deliberately untrimmed but now trim; remove them from "
            "NO_TRIM_FREE_TEXT:\n  " + "\n  ".join(stale))

    def test_a_padded_description_is_stored_trimmed_on_every_model_that_takes_one(self):
        from models.apiKeys import (CreateApiKeyRequestModel, CreateUserApiKeyRequestModel,
                                    UpdateApiKeyRequestModel, UpdateUserApiKeyRequestModel)
        from models.assetsV3 import (CreateAssetRequestModel, IngestAssetCompleteRequestModel,
                                     IngestAssetInitializeRequestModel, UpdateAssetRequestModel)
        from models.databases import CreateDatabaseRequestModel, UpdateDatabaseRequestModel
        from models.roleConstraints import (CreateConstraintRequestModel, CreateRoleRequestModel,
                                            TemplateConstraintDefinition, UpdateRoleRequestModel)
        from models.tag import (CreateTagRequestModel, CreateTagTypeRequestModel,
                                UpdateTagRequestModel, UpdateTagTypeRequestModel)

        padded, clean = "  A real description  ", "A real description"

        assert CreateApiKeyRequestModel(apiKeyName="k", userId="user-a",
                                        description=padded).description == clean
        assert CreateUserApiKeyRequestModel(apiKeyName="k", description=padded,
                                           expiresAt="2030-01-01").description == clean
        for model_cls in (UpdateApiKeyRequestModel, UpdateUserApiKeyRequestModel):
            assert model_cls(description=padded).description == clean

        assert CreateAssetRequestModel(
            **_asset_body(description=padded)).description == clean
        assert IngestAssetInitializeRequestModel(**_asset_body(
            description=padded,
            files=[{"relativeKey": "part.glb/a.txt", "num_parts": 1}])).description == clean
        assert IngestAssetCompleteRequestModel(**_asset_body(
            description=padded, uploadId="upload-1",
            files=[_completed_file("part.glb/a.txt")])).description == clean
        assert UpdateAssetRequestModel(description=padded).description == clean

        for model_cls in (CreateDatabaseRequestModel, UpdateDatabaseRequestModel):
            assert model_cls(databaseId="mydb", description=padded,
                             defaultBucketId=_DEFAULT_BUCKET_ID).description == clean

        assert CreateConstraintRequestModel(
            **_constraint_body(description=padded)).description == clean
        for model_cls in (CreateRoleRequestModel, UpdateRoleRequestModel):
            assert model_cls(roleName="admin", description=padded).description == clean
        assert TemplateConstraintDefinition(
            name="Asset Read", description=padded, objectType="asset",
            groupPermissions=[{"action": "GET", "type": "allow"}]).description == clean

        for model_cls in (CreateTagRequestModel, UpdateTagRequestModel):
            assert model_cls(**_tag_body(description=padded)).description == clean
        for model_cls in (CreateTagTypeRequestModel, UpdateTagTypeRequestModel):
            assert model_cls(tagTypeName="Environment", description=padded).description == clean

    def test_a_padded_version_comment_is_stored_trimmed(self):
        from models.assetsV3 import (CreateAssetVersionRequestModel,
                                     RevertAssetVersionRequestModel,
                                     UpdateAssetVersionRequestModel)

        assert CreateAssetVersionRequestModel(
            comment="  Second pass  ", useLatestFiles=True).comment == "Second pass"
        assert RevertAssetVersionRequestModel(
            assetVersionId="2", comment="  Rolled back  ").comment == "Rolled back"
        assert UpdateAssetVersionRequestModel(comment="  Amended  ").comment == "Amended"

    def test_interior_whitespace_in_free_text_survives(self):
        """Only the surrounding run is removed, so prose keeps its shape."""
        from models.assetsV3 import CreateAssetRequestModel

        assert CreateAssetRequestModel(**_asset_body(
            description="  Two  spaces  inside  ")).description == "Two  spaces  inside"

    def test_the_trim_runs_before_the_length_check(self):
        """The ordering, asserted as the rejection it causes: 6 raw characters, 2 after trimming,
        against min_length=4. Accepted and stored padded before the trim was wired; a 400 now.

        Paired with the accepting arm so a model that rejected EVERYTHING would not pass this.
        """
        from aws_lambda_powertools.utilities.parser import ValidationError
        from models.assetsV3 import CreateAssetRequestModel
        from models.databases import CreateDatabaseRequestModel

        with pytest.raises(ValidationError):
            CreateAssetRequestModel(**_asset_body(description="  ab  "))
        with pytest.raises(ValidationError):
            CreateDatabaseRequestModel(databaseId="mydb", description="  ab  ",
                                       defaultBucketId=_DEFAULT_BUCKET_ID)
        # Must-still-work: the same length, unpadded, still satisfies min_length=4.
        assert CreateAssetRequestModel(**_asset_body(description="abcd")).description == "abcd"
        assert CreateDatabaseRequestModel(
            databaseId="mydb", description="abcd",
            defaultBucketId=_DEFAULT_BUCKET_ID).description == "abcd"

    def test_a_non_string_description_reaches_the_fields_own_type_handling(self):
        """`trim_name` returns a non-string untouched, so the field's own type rule decides. Pydantic
        v1 coerces an int to `str` for a `str`-typed field, and that is what happens here -- the point
        is that the pre-validator does not raise `AttributeError` (which would surface as a 500
        instead of a 400) by calling `.strip()` on it."""
        from models.tag import CreateTagRequestModel

        assert CreateTagRequestModel(**_tag_body(description=123)).description == "123"
