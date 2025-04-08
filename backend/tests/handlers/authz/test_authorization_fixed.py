# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from enum import StrEnum
from unittest.mock import patch, MagicMock

# Import actual implementation
from backend.backend.handlers.authz import CasbinEnforcer
from backend.backend.common.constants import PERMISSION_CONSTRAINT_FIELDS

# Constants
class Actions(StrEnum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    GET = "GET"


def pytest_namespace():
    return {
        "databases": [],
        "assets": []
    }


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


# Mock policy data for different user roles
db_admin_policy = {
    "enforce": {
        "database": {
            "db-admin-only": True,
            "defense-assets": True,
            "low-sensitivity-assets": True
        },
        "asset": {
            "db-admin-only": True,
            "defense-assets": True,
            "low-sensitivity-assets": True
        }
    }
}

defense_user_policy = {
    "enforce": {
        "database": {
            "db-admin-only": False,
            "defense-assets": True,
            "low-sensitivity-assets": True
        },
        "asset": {
            "db-admin-only": False,
            "defense-assets": True,
            "low-sensitivity-assets": True
        }
    }
}

regular_user_policy = {
    "enforce": {
        "database": {
            "db-admin-only": False,
            "defense-assets": False,
            "low-sensitivity-assets": True
        },
        "asset": {
            "db-admin-only": False,
            "defense-assets": False,
            "low-sensitivity-assets": True
        }
    }
}

admin_policy = {
    "enforce": {
        "api": {
            "/pipelines": True
        }
    }
}

only_assets_user_policy = {
    "enforce": {
        "api": {
            "/pipelines": False
        }
    }
}


class TestAuthorization:
    def setup_class(self):
        setup_objects()

    def teardown_class(self):
        pass

    def test_db_admin_can_read_all_databases(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["db-admin-user@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "database":
                return db_admin_policy["enforce"]["database"].get(obj["databaseId"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that db admin can read all databases
        for database in pytest.databases:
            assert enforcer.enforce(database, Actions.READ)

    def test_defense_user_can_not_read_db_admin_only_databases(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["defense-user-1@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "database":
                return defense_user_policy["enforce"]["database"].get(obj["databaseId"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that defense user cannot read db admin only databases
        for db_admin_only_database in list(filter(lambda x: x["databaseId"] == "db-admin-only", pytest.databases)):
            assert not enforcer.enforce(db_admin_only_database, Actions.READ)

    def test_defense_user_can_read_defense_databases(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["defense-user-1@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "database":
                return defense_user_policy["enforce"]["database"].get(obj["databaseId"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that defense user can read defense databases
        for defense_database in list(filter(lambda x: x["databaseId"] == "defense-assets", pytest.databases)):
            assert enforcer.enforce(defense_database, Actions.READ)

    def test_defense_user_can_read_low_sensitivity_databases(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["defense-user-1@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "database":
                return defense_user_policy["enforce"]["database"].get(obj["databaseId"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that defense user can read low sensitivity databases
        for low_sensitivity_database in list(filter(lambda x: x["databaseId"] == "low-sensitivity-assets", pytest.databases)):
            assert enforcer.enforce(low_sensitivity_database, Actions.READ)

    def test_regular_user_can_not_read_db_admin_only_databases(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["regular-user-1@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "database":
                return regular_user_policy["enforce"]["database"].get(obj["databaseId"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that regular user cannot read db admin only databases
        for db_admin_only_database in list(filter(lambda x: x["databaseId"] == "db-admin-only", pytest.databases)):
            assert not enforcer.enforce(db_admin_only_database, Actions.READ)

    def test_regular_user_can_not_read_defense_databases(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["regular-user-1@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "database":
                return regular_user_policy["enforce"]["database"].get(obj["databaseId"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that regular user cannot read defense databases
        for defense_database in list(filter(lambda x: x["databaseId"] == "defense-assets", pytest.databases)):
            assert not enforcer.enforce(defense_database, Actions.READ)

    def test_regular_user_can_read_low_sensitivity_databases(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["regular-user-1@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "database":
                return regular_user_policy["enforce"]["database"].get(obj["databaseId"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that regular user can read low sensitivity databases
        for low_sensitivity_database in list(filter(lambda x: x["databaseId"] == "low-sensitivity-assets", pytest.databases)):
            assert enforcer.enforce(low_sensitivity_database, Actions.READ)

    # Asset Tests
    def test_db_admin_can_read_all_assets(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["db-admin-user@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "asset":
                return db_admin_policy["enforce"]["asset"].get(obj["databaseId"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that db admin can read all assets
        for asset in pytest.assets:
            assert enforcer.enforce(asset, Actions.READ)

    def test_defense_user_can_not_read_db_admin_only_assets(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["defense-user-1@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "asset":
                return defense_user_policy["enforce"]["asset"].get(obj["databaseId"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that defense user cannot read db admin only assets
        for db_admin_only_asset in list(filter(lambda x: x["databaseId"] == "db-admin-only", pytest.assets)):
            assert not enforcer.enforce(db_admin_only_asset, Actions.READ)

    def test_defense_user_can_read_defense_assets(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["defense-user-1@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "asset":
                return defense_user_policy["enforce"]["asset"].get(obj["databaseId"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that defense user can read defense assets
        for defense_asset in list(filter(lambda x: x["databaseId"] == "defense-assets", pytest.assets)):
            assert enforcer.enforce(defense_asset, Actions.READ)

    def test_defense_user_can_read_low_sensitivity_assets(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["defense-user-1@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "asset":
                return defense_user_policy["enforce"]["asset"].get(obj["databaseId"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that defense user can read low sensitivity assets
        for low_sensitivity_asset in list(filter(lambda x: x["databaseId"] == "low-sensitivity-assets", pytest.assets)):
            assert enforcer.enforce(low_sensitivity_asset, Actions.READ)

    def test_regular_user_can_not_read_db_admin_only_assets(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["regular-user-1@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "asset":
                return regular_user_policy["enforce"]["asset"].get(obj["databaseId"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that regular user cannot read db admin only assets
        for db_admin_only_asset in list(filter(lambda x: x["databaseId"] == "db-admin-only", pytest.assets)):
            assert not enforcer.enforce(db_admin_only_asset, Actions.READ)

    def test_regular_user_can_not_read_defense_assets(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["regular-user-1@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "asset":
                return regular_user_policy["enforce"]["asset"].get(obj["databaseId"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that regular user cannot read defense assets
        for defense_asset in list(filter(lambda x: x["databaseId"] == "defense-assets", pytest.assets)):
            assert not enforcer.enforce(defense_asset, Actions.READ)

    def test_regular_user_can_read_low_sensitivity_assets(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["regular-user-1@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "asset":
                return regular_user_policy["enforce"]["asset"].get(obj["databaseId"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that regular user can read low sensitivity assets
        for low_sensitivity_asset in list(filter(lambda x: x["databaseId"] == "low-sensitivity-assets", pytest.assets)):
            assert enforcer.enforce(low_sensitivity_asset, Actions.READ)

    # Role-based tests
    def test_admin_user_can_do_get_on_pipelines_url(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["admin-user@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "api":
                return admin_policy["enforce"]["api"].get(obj["route__path"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that admin user can do GET on pipelines URL
        assert enforcer.enforce({"object__type": "api", "route__path": "/pipelines"}, Actions.GET)

    def test_only_assets_user_can_not_do_get_on_pipelines_url(self):
        # Create CasbinEnforcer with mock claims
        enforcer = CasbinEnforcer({"tokens": ["only-assets-user-1@company.com"]})
        
        # Override the service_object.enforce method to use our policy
        def mock_enforce(obj, act):
            if obj["object__type"] == "api":
                return only_assets_user_policy["enforce"]["api"].get(obj["route__path"], False)
            return False
        
        enforcer.service_object.enforce = mock_enforce
        
        # Test that only assets user cannot do GET on pipelines URL
        assert not enforcer.enforce({"object__type": "api", "route__path": "/pipelines"}, Actions.GET)
