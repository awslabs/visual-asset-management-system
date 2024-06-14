import pytest
from enum import StrEnum

from casbin import model
from casbin import enforcer
from casbin.persist.adapters import string_adapter


# Constants
class Actions(StrEnum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    GET = "GET"


def pytest_namespace():
    return {
        "casbin_enforcer": None,
        "databases": []
    }


def setup_policies():
    model_text = """
    [request_definition]
    r = sub, obj, act

    [policy_definition]
    p = sub, obj_rule, act, eft

    [role_definition]
    g = _, _

    [policy_effect]
    e = some(where (p.eft == allow)) && !some(where (p.eft == deny))

    [matchers]
    m = g(r.sub, 'role::admin') || g(r.sub, p.sub) && eval(p.obj_rule) && r.act == p.act
    """

    _policies = {
        "roles": [
            # Admins
            "g, user::admin-user@company.com, 'role::admin'",

            # DB Admins
            "g, user::db-admin-user@company.com, 'role::db-admin'",

            # Defense Users
            "g, user::defense-user-1@company.com, 'role::defense-user'",
            "g, user::defense-user-2@company.com, 'role::defense-user'",

            # Regular Users
            "g, user::regular-user-1@company.com, 'role::regular-user'",
            "g, user::regular-user-2@company.com, 'role::regular-user'",

            # Only assets user | No access to database
            "g, user::only-assets-user-1@company.com, 'role::only-assets-user'",

            # Role Hierarchy
            # db-admin > defense-user > regular-user
            "g, 'role::db-admin', 'role::defense-user'",
            "g, 'role::defense-user', 'role::regular-user'"
        ],
        "database_policies": [
            "p, 'role::db-admin', r.obj.object__type == 'database', read, allow",
            "p, 'role::defense-user', r.obj.databaseId == 'defense-assets', read, allow",
            "p, 'role::regular-user', r.obj.databaseId == 'low-sensitivity-assets', read, allow",
        ],
        "database_asset_policies": [
            "p, 'role::db-admin', r.obj.object__type == 'asset', read, allow",
            "p, 'role::defense-user', r.obj.object__type == 'asset' && r.obj.databaseId == 'defense-assets', read, allow",
            "p, 'role::regular-user', r.obj.object__type == 'asset' && r.obj.databaseId == 'low-sensitivity-assets', read, allow",
        ],
        "pipeline_policies": [],
        "workflow_policies": [],
        "user_admin_policies": [],
        "role_admin_policies": [],
        "global_tag_admin_policies": []
    }

    policy_text = "\n".join(
        ["\n".join(policy_list) for policy_list in filter(lambda x: len(x), list(_policies.values()))])

    # Create a Model
    new_model = model.Model()
    new_model.load_model_from_text(model_text)

    # Create a new StringAdapter (Policy)
    new_string_adapter = string_adapter.StringAdapter(policy_text)

    # Create Policy Enforcer using Model and Policy
    pytest.casbin_enforcer = enforcer.Enforcer(model=new_model, adapter=new_string_adapter, enable_log=True)


def setup_objects():
    setup_databases()
    setup_assets()


def setup_databases():
    pytest.databases = [
        {
            "databaseId": "db-admin-only",
            "acl": [
                "db-admin-user@company.com"
            ],
            "assetCount": "157",
            "dateCreated": "\"November 07 2023 - 12:07:48\"",
            "description": "DB Admin configuration related assets",
            "object__type": "database"
        },
        {
            "databaseId": "defense-assets",
            "acl": [
                "db-admin-user@company.com",
                "defense-user-1@company.com"
            ],
            "assetCount": "283",
            "dateCreated": "\"November 07 2023 - 12:07:48\"",
            "description": "High sensitivity assets related to defense",
            "object__type": "database"
        },
        {
            "databaseId": "low-sensitivity-assets",
            "acl": [
                "db-admin-user@company.com",
                "regular-user-1@company.com",
                "defense-user-1@company.com"
            ],
            "assetCount": "4298",
            "dateCreated": "\"November 07 2023 - 12:07:48\"",
            "description": "Low sensitivity assets for regular users",
            "object__type": "database"
        }
    ]


def setup_assets():
    pytest.assets = [
        {
            "databaseId": "db-admin-only",
            "assetId": "admin-asset-1",
            "assetLocation": {
                "Key": "admin-asset-1.obj"
            },
            "assetName": "Admin Asset 1",
            "assetType": ".obj",
            "currentVersion": {
                "Comment": "Changed boundary color",
                "DateModified": "November 17 2023 - 13:08:45",
                "description": "Changed boundary color",
                "S3Version": "",
                "specifiedPipelines": [],
                "Version": "2"
            },
            "description": "Changed boundary color",
            "executionId": "",
            "isDistributable": True,
            "isMultiFile": False,
            "pipelineId": "",
            "previewLocation": {
                "Key": "previews/Admin Asset 1/Preview.png"
            },
            "specifiedPipelines": [],
            "versions": [
                {
                    "Comment": "Original upload",
                    "DateModified": "November 07 2023 - 15:38:04",
                    "description": "3D model of an Admin",
                    "previewLocation": {
                        "Key": "previews/Admin Asset 1/Preview.png"
                    },
                    "S3Version": "",
                    "specifiedPipelines": [],
                    "Version": "1"
                },
                {
                    "Comment": "Changed boundary color",
                    "DateModified": "November 07 2023 - 15:38:04",
                    "description": "Changed boundary color",
                    "previewLocation": {
                        "Key": "previews/Admin Asset 1/Preview.png"
                    },
                    "S3Version": "",
                    "specifiedPipelines": [],
                    "Version": "2"
                }
            ],
            "object__type": "asset"
        },
        {
            "databaseId": "defense-assets",
            "assetId": "defense-asset-1",
            "assetLocation": {
                "Key": "defense-asset-1.obj"
            },
            "assetName": "Defense Asset 1",
            "assetType": ".obj",
            "currentVersion": {
                "Comment": "Changed boundary color",
                "DateModified": "November 17 2023 - 13:08:45",
                "description": "Changed boundary color",
                "S3Version": "",
                "specifiedPipelines": [],
                "Version": "2"
            },
            "description": "Changed boundary color",
            "executionId": "",
            "isDistributable": True,
            "isMultiFile": False,
            "pipelineId": "",
            "previewLocation": {
                "Key": "previews/Defense Asset 1/Preview.png"
            },
            "specifiedPipelines": [],
            "versions": [
                {
                    "Comment": "Original upload",
                    "DateModified": "November 07 2023 - 15:38:04",
                    "description": "3D model of a defense aircraft",
                    "previewLocation": {
                        "Key": "previews/Defense Asset 1/Preview.png"
                    },
                    "S3Version": "",
                    "specifiedPipelines": [],
                    "Version": "1"
                },
                {
                    "Comment": "Changed boundary color",
                    "DateModified": "November 07 2023 - 15:38:04",
                    "description": "Changed boundary color",
                    "previewLocation": {
                        "Key": "previews/Defense Asset 1/Preview.png"
                    },
                    "S3Version": "",
                    "specifiedPipelines": [],
                    "Version": "2"
                }
            ],
            "object__type": "asset"
        },
        {
            "databaseId": "low-sensitivity-assets",
            "assetId": "low-sensitivity-asset-1",
            "assetLocation": {
                "Key": "low-sensitivity-asset-1.obj"
            },
            "assetName": "Low Sensitivity Asset 1",
            "assetType": ".obj",
            "currentVersion": {
                "Comment": "Changed boundary color",
                "DateModified": "November 17 2023 - 13:08:45",
                "description": "Changed boundary color",
                "S3Version": "",
                "specifiedPipelines": [],
                "Version": "2"
            },
            "description": "Changed boundary color",
            "executionId": "",
            "isDistributable": True,
            "isMultiFile": False,
            "pipelineId": "",
            "previewLocation": {
                "Key": "previews/Low Sensitivity Asset 1/Preview.png"
            },
            "specifiedPipelines": [],
            "versions": [
                {
                    "Comment": "Original upload",
                    "DateModified": "November 07 2023 - 15:38:04",
                    "description": "3D model of a trophy",
                    "previewLocation": {
                        "Key": "previews/Low Sensitivity Asset 1/Preview.png"
                    },
                    "S3Version": "",
                    "specifiedPipelines": [],
                    "Version": "1"
                },
                {
                    "Comment": "Changed boundary color",
                    "DateModified": "November 07 2023 - 15:38:04",
                    "description": "Changed boundary color",
                    "previewLocation": {
                        "Key": "previews/Low Sensitivity Asset 1/Preview.png"
                    },
                    "S3Version": "",
                    "specifiedPipelines": [],
                    "Version": "2"
                }
            ],
            "object__type": "asset"
        }
    ]


def teardown():
    pass


class TestAuthorization:
    def setup_class(self):
        setup_policies()
        setup_objects()

    def teardown_class(self):
        teardown()

    # Database tests
    def test_db_admin_can_read_all_databases(self):
        for database in pytest.databases:
            assert pytest.casbin_enforcer.enforce(
                "user::db-admin-user@company.com",
                database,
                Actions.READ
            )

    def test_defense_user_can_not_read_db_admin_only_databases(self):
        for db_admin_only_database in list(filter(lambda x: x["databaseId"] == "db-admin-only", pytest.databases)):
            assert not pytest.casbin_enforcer.enforce(
                "user::defense-user-1@company.com",
                db_admin_only_database,
                Actions.READ
            )

    def test_defense_user_can_read_defense_databases(self):
        for defense_database in list(filter(lambda x: x["databaseId"] == "defense-assets", pytest.databases)):
            assert pytest.casbin_enforcer.enforce(
                "user::defense-user-1@company.com",
                defense_database,
                Actions.READ
            )

    def test_defense_user_can_read_low_sensitivity_databases(self):
        for low_sensitivity_database in list(filter(lambda x: x["databaseId"] == "low-sensitivity-assets", pytest.databases)):
            assert pytest.casbin_enforcer.enforce(
                "user::defense-user-1@company.com",
                low_sensitivity_database,
                Actions.READ
            )

    def test_regular_user_can_not_read_db_admin_only_databases(self):
        for db_admin_only_database in list(filter(lambda x: x["databaseId"] == "db-admin-only", pytest.databases)):
            assert not pytest.casbin_enforcer.enforce(
                "user::regular-user-1@company.com",
                db_admin_only_database,
                Actions.READ
            )

    def test_regular_user_can_not_read_defense_databases(self):
        for defense_database in list(filter(lambda x: x["databaseId"] == "defense-assets", pytest.databases)):
            assert not pytest.casbin_enforcer.enforce(
                "user::regular-user-1@company.com",
                defense_database,
                Actions.READ
            )

    def test_regular_user_can_read_low_sensitivity_databases(self):
        for low_sensitivity_database in list(filter(lambda x: x["databaseId"] == "low-sensitivity-assets", pytest.databases)):
            assert pytest.casbin_enforcer.enforce(
                "user::regular-user-1@company.com",
                low_sensitivity_database,
                Actions.READ
            )

    # Asset Tests
    def test_db_admin_can_read_all_assets(self):
        for asset in pytest.assets:
            assert pytest.casbin_enforcer.enforce(
                "user::db-admin-user@company.com",
                asset,
                Actions.READ
            )

    def test_defense_user_can_not_read_db_admin_only_assets(self):
        for db_admin_only_asset in list(filter(lambda x: x["databaseId"] == "db-admin-only", pytest.assets)):
            assert not pytest.casbin_enforcer.enforce(
                "user::defense-user-1@company.com",
                db_admin_only_asset,
                Actions.READ
            )

    def test_defense_user_can_read_defense_assets(self):
        for defense_asset in list(filter(lambda x: x["databaseId"] == "defense-assets", pytest.assets)):
            assert pytest.casbin_enforcer.enforce(
                "user::defense-user-1@company.com",
                defense_asset,
                Actions.READ
            )

    def test_defense_user_can_read_low_sensitivity_assets(self):
        for low_sensitivity_asset in list(filter(lambda x: x["databaseId"] == "low-sensitivity-assets", pytest.assets)):
            assert pytest.casbin_enforcer.enforce(
                "user::defense-user-1@company.com",
                low_sensitivity_asset,
                Actions.READ
            )

    def test_regular_user_can_not_read_db_admin_only_assets(self):
        for db_admin_only_asset in list(filter(lambda x: x["databaseId"] == "db-admin-only", pytest.assets)):
            assert not pytest.casbin_enforcer.enforce(
                "user::regular-user-1@company.com",
                db_admin_only_asset,
                Actions.READ
            )

    def test_regular_user_can_not_read_defense_assets(self):
        for defense_asset in list(filter(lambda x: x["databaseId"] == "defense-assets", pytest.assets)):
            assert not pytest.casbin_enforcer.enforce(
                "user::regular-user-1@company.com",
                defense_asset,
                Actions.READ
            )

    def test_regular_user_can_read_low_sensitivity_assets(self):
        for low_sensitivity_asset in list(filter(lambda x: x["databaseId"] == "low-sensitivity-assets", pytest.assets)):
            assert pytest.casbin_enforcer.enforce(
                "user::regular-user-1@company.com",
                low_sensitivity_asset,
                Actions.READ
            )

    # Role-based tests
    def test_admin_user_can_do_get_on_pipelines_url(self):
        assert pytest.casbin_enforcer.enforce(
            "role::admin",
            "/pipelines",
            Actions.GET
        )

    def test_only_assets_user_can_not_do_get_on_pipelines_url(self):
        assert not pytest.casbin_enforcer.enforce(
            "role::only-assets-user",
            "/pipelines",
            Actions.GET
        )