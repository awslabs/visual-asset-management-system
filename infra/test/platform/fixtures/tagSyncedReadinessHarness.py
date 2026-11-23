# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Runs the REAL `reindex_utility.py --tag-synced` entry point (infra/deploymentDataMigration/tools/
# tag_synced_objects.py) with the REAL backend location-index module up to and just past its readiness
# check, against a fake boto3 session, and prints one JSON object per scenario. migrationTooling.test.ts
# reads it. botocore and the backend's logger are replaced by small fakes so the harness needs only the
# Python standard library; the migration tools carry no committed Python tests (infra Development Rule 6).
#
# Usage: tagSyncedReadinessHarness.py <tag_synced_objects.py> <backend source root>
import importlib.util
import json
import logging
import sys
import types

PREFIX = "/vams-test/resourceNames"
ASSET_TABLE = "vams-test-assetStorageTable"
BUCKETS_TABLE = "vams-test-s3AssetBucketsStorageTable"


class ClientError(Exception):
    def __init__(self, error_response, operation_name):
        self.response = error_response
        self.operation_name = operation_name
        code = error_response.get("Error", {}).get("Code", "")
        super().__init__(f"An error occurred ({code}) when calling the {operation_name} operation")


class Config:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _install_fakes():
    botocore = types.ModuleType("botocore")
    config = types.ModuleType("botocore.config")
    config.Config = Config
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.ClientError = ClientError
    botocore.config, botocore.exceptions = config, exceptions
    sys.modules.update({"botocore": botocore, "botocore.config": config, "botocore.exceptions": exceptions})
    custom_logging = types.ModuleType("customLogging")
    custom_logging.__path__ = []
    logger = types.ModuleType("customLogging.logger")
    logger.safeLogger = lambda *args, **kwargs: logging.getLogger("harness")
    custom_logging.logger = logger
    sys.modules.update({"customLogging": custom_logging, "customLogging.logger": logger})


class FakeSsm:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def get_parameter(self, Name, **kwargs):
        self.calls.append(Name)
        if isinstance(self.answer, Exception):
            raise self.answer
        return {"Parameter": {"Name": Name, "Type": "String", "Value": self.answer}}


class FakeTable:
    def __init__(self, name, scans):
        self.name = name
        self.scans = scans

    def scan(self, **kwargs):
        self.scans.append(self.name)
        return {"Items": []}


class FakeResource:
    def __init__(self, scans):
        self.scans = scans

    def Table(self, name):
        return FakeTable(name, self.scans)


class FakeSession:
    def __init__(self, ssm):
        self.ssm = ssm
        self.clients = []
        self.resources = []
        self.scans = []

    def client(self, service, **kwargs):
        self.clients.append(service)
        if service == "ssm":
            return self.ssm
        raise AssertionError(f"unexpected client {service}")

    def resource(self, service, **kwargs):
        self.resources.append(service)
        return FakeResource(self.scans)


def marker(table):
    return json.dumps({"version": 1, "status": "complete", "assetTable": table})


def error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "GetParameter")


SCENARIOS = {
    "missing": error("ParameterNotFound"),
    "throttled": error("ThrottlingException"),
    "otherTable": marker("vams-other-assetStorageTable"),
    "complete": marker(ASSET_TABLE),
}


def main(tool_path, backend_path):
    _install_fakes()
    spec = importlib.util.spec_from_file_location("tag_synced_objects_under_test", tool_path)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    results = {}
    for name, answer in SCENARIOS.items():
        asset_locations = tool._backend_modules(backend_path)[0]
        asset_locations.reset_index_ready_cache()
        ssm = FakeSsm(answer)
        session = FakeSession(ssm)
        outcome = {"exception": None, "result": None}
        try:
            outcome["result"] = tool.tag_synced_objects(session, PREFIX, ASSET_TABLE, BUCKETS_TABLE, backend_path,
                                                        dry_run=True)
        except Exception as e:  # reported, so the test names the failure
            outcome["exception"] = f"{type(e).__name__}: {e}"
        outcome.update({
            "markerReads": ssm.calls,
            "sessionClients": session.clients,
            "sessionResources": session.resources,
            "scans": session.scans,
            # The tool reads the marker with its own session; the backend's default reader (which builds a
            # client from the environment) must not be loaded for it.
            "defaultReaderLoaded": "common.ssmParameters" in sys.modules,
        })
        results[name] = outcome
    print(json.dumps(results, sort_keys=True, default=str))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
