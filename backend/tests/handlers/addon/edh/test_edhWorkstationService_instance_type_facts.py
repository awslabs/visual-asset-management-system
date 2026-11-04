# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""What the launch catalog says about an instance type and which one a recipe image starts with.

``GET /addon/edh/catalog/instance-types`` adds, per type, EC2's ``vcpus``, ``memoryGiB`` and ``gpus`` (each kind of
GPU: ``count``, ``manufacturer``, ``name``, ``memoryGiB`` per GPU; empty for a type without GPUs), read with
DescribeInstanceTypes in the Lambda's Region on an ``instance-type`` filter of at most 100 types per call, following
NextToken, and kept per container for an hour. A type EC2 does not offer, and every type of a read that fails, is
listed without the fields; a failed read is a warning, caches nothing and never fails the catalog.

``GET /addon/edh/catalog/images`` names, on a recipe image whose recipe lists launch instance types,
``defaultInstanceType``: the first of those types the deployment allows, the image's own EDH profile lists and that
runs the image's architecture; absent when none does, and on every other image.
"""

import json

import pytest
from botocore.exceptions import ClientError

from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    IMAGES_PATH,
    INSTANCE_TYPES_PATH,
    FakeEdhClient,
    FakeInstanceTypesEc2,
    install_service_fakes,
    make_event,
    response_body,
)
import backend.backend.handlers.addon.edh.edhWorkstationService as service

PROFILE_ID = 1
GPU_TYPE = "g5.2xlarge"
CPU_TYPE = "m7i.xlarge"
RECIPE_STACK_ID = 36


def _record(instance_type, vcpus, memory_mib, gpus=None):
    """A DescribeInstanceTypes record as EC2 answers it; ``gpus`` is a list of (count, manufacturer, name, MiB)."""
    record = {"InstanceType": instance_type, "VCpuInfo": {"DefaultVCpus": vcpus},
              "MemoryInfo": {"SizeInMiB": memory_mib}}
    if gpus:
        record["GpuInfo"] = {"Gpus": [{"Count": count, "Manufacturer": maker, "Name": name,
                                       "MemoryInfo": {"SizeInMiB": mib}} for count, maker, name, mib in gpus],
                             "TotalGpuMemoryInMiB": sum(count * mib for count, _, _, mib in gpus)}
    return record


RECORDS = {
    GPU_TYPE: _record(GPU_TYPE, 8, 32768, [(1, "NVIDIA", "A10G", 24576)]),
    CPU_TYPE: _record(CPU_TYPE, 4, 16384),
    "c5.large": _record("c5.large", 2, 4096),
    "g6e.xlarge": _record("g6e.xlarge", 4, 32768, [(1, "NVIDIA", "L40S", 46068)]),
}


def _profiles(types):
    return [{service.EDH_FIELD_PROFILE_ID: PROFILE_ID,
             service.EDH_FIELD_PROFILE_INSTANCE_TYPES: json.dumps({"x86_64": list(types)})}]


def _catalog(monkeypatch, ec2, types=(GPU_TYPE, CPU_TYPE)):
    """The instance-type catalog served for a profile of ``types``, every one of them allowed."""
    fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=_profiles(types)), ec2=ec2)
    monkeypatch.setattr(service, "allowed_instance_types", ())
    response = fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {})
    assert response["statusCode"] == 200
    return {item["instanceType"]: item for item in response_body(response)["Items"]}


def _client_error(code="UnauthorizedOperation"):
    return ClientError({"Error": {"Code": code, "Message": "no"}}, "DescribeInstanceTypes")


@pytest.fixture
def clock(monkeypatch):
    """The service's monotonic clock, set by the test."""
    now = [1000.0]
    monkeypatch.setattr(service, "_monotonic", lambda: now[0])
    return now


@pytest.fixture
def warnings(monkeypatch):
    logged = []
    monkeypatch.setattr(service.logger, "warning", lambda message, *a, **k: logged.append(str(message)))
    return logged


@pytest.mark.unit
class TestTheFactsOfEachType:
    def test_a_gpu_type_carries_its_vcpus_memory_and_gpus(self, monkeypatch):
        items = _catalog(monkeypatch, FakeInstanceTypesEc2(RECORDS))

        assert items[GPU_TYPE] == {
            "instanceType": GPU_TYPE, "architectures": ["x86_64"], "vcpus": 8, "memoryGiB": 32.0,
            "gpus": [{"count": 1, "manufacturer": "NVIDIA", "name": "A10G", "memoryGiB": 24.0}]}

    def test_a_type_without_gpus_carries_an_empty_gpu_list(self, monkeypatch):
        items = _catalog(monkeypatch, FakeInstanceTypesEc2(RECORDS))

        assert items[CPU_TYPE] == {"instanceType": CPU_TYPE, "architectures": ["x86_64"], "vcpus": 4,
                                   "memoryGiB": 16.0, "gpus": []}

    def test_the_memory_of_a_gpu_that_is_not_a_whole_gib_keeps_two_decimals(self, monkeypatch):
        items = _catalog(monkeypatch, FakeInstanceTypesEc2(RECORDS), types=("g6e.xlarge",))

        assert items["g6e.xlarge"]["gpus"] == [
            {"count": 1, "manufacturer": "NVIDIA", "name": "L40S", "memoryGiB": 44.99}]

    @pytest.mark.parametrize("partition,logical,share", [(0.125, 1, 0.125), (0.5, None, 0.5)],
                             ids=["an-eighth", "half-without-a-logical-count"])
    def test_a_type_given_part_of_a_gpu_carries_that_gpu_with_its_share(self, monkeypatch, partition, logical,
                                                                      share):
        """EC2 states a GPU partition (g6f, gr6f) as a Count of 0 with a GpuPartitionSize below 1."""
        gpu = {"Name": "L4", "Manufacturer": "NVIDIA", "Count": 0, "GpuPartitionSize": partition,
               "Workloads": ["graphics", "ml-ai"], "MemoryInfo": {"SizeInMiB": 2861}}
        if logical is not None:
            gpu["LogicalGpuCount"] = logical
        record = {"InstanceType": "g6f.large", "VCpuInfo": {"DefaultVCpus": 2}, "MemoryInfo": {"SizeInMiB": 8192},
                  "GpuInfo": {"Gpus": [gpu], "TotalGpuMemoryInMiB": 2861}}

        items = _catalog(monkeypatch, FakeInstanceTypesEc2({"g6f.large": record}), types=("g6f.large",))

        assert items["g6f.large"]["gpus"] == [
            {"count": 1, "manufacturer": "NVIDIA", "name": "L4", "memoryGiB": 2.79, "share": share}]

    def test_a_whole_gpu_carries_no_share(self, monkeypatch):
        """EC2 states a whole GPU with a GpuPartitionSize of 1.0."""
        record = _record("g6.xlarge", 4, 16384, [(1, "NVIDIA", "L4", 22888)])
        record["GpuInfo"]["Gpus"][0]["GpuPartitionSize"] = 1.0

        items = _catalog(monkeypatch, FakeInstanceTypesEc2({"g6.xlarge": record}), types=("g6.xlarge",))

        assert items["g6.xlarge"]["gpus"] == [
            {"count": 1, "manufacturer": "NVIDIA", "name": "L4", "memoryGiB": 22.35}]

    def test_a_type_ec2_does_not_offer_is_listed_without_facts(self, monkeypatch):
        items = _catalog(monkeypatch, FakeInstanceTypesEc2(RECORDS), types=(GPU_TYPE, "gr6.4xlarge"))

        assert items["gr6.4xlarge"] == {"instanceType": "gr6.4xlarge", "architectures": ["x86_64"]}
        # Control: the type EC2 does offer kept its facts on the same answer.
        assert items[GPU_TYPE]["vcpus"] == 8

    def test_the_read_filters_on_the_types_rather_than_naming_them(self, monkeypatch):
        ec2 = FakeInstanceTypesEc2(RECORDS)

        _catalog(monkeypatch, ec2)

        assert ec2.requests, "the catalog read EC2"
        assert all("InstanceTypes" not in request for request in ec2.requests)
        assert sorted(ec2.types_asked()) == sorted([GPU_TYPE, CPU_TYPE])

    def test_fields_ec2_leaves_out_are_left_out(self, monkeypatch):
        sparse = {GPU_TYPE: {"InstanceType": GPU_TYPE, "GpuInfo": {"Gpus": [{"Count": 1}, {"Count": 0}]}}}

        items = _catalog(monkeypatch, FakeInstanceTypesEc2(sparse), types=(GPU_TYPE,))

        assert items[GPU_TYPE] == {"instanceType": GPU_TYPE, "architectures": ["x86_64"], "gpus": [{"count": 1}]}


@pytest.mark.unit
class TestAFailedRead:
    @pytest.mark.parametrize("failure", [_client_error(), _client_error("RequestLimitExceeded"), RuntimeError("x")],
                             ids=["denied", "throttled", "unexpected"])
    def test_the_catalog_answers_without_the_facts_and_warns(self, monkeypatch, warnings, failure):
        items = _catalog(monkeypatch, FakeInstanceTypesEc2(RECORDS, failure=failure))

        assert items == {GPU_TYPE: {"instanceType": GPU_TYPE, "architectures": ["x86_64"]},
                         CPU_TYPE: {"instanceType": CPU_TYPE, "architectures": ["x86_64"]}}
        assert any("Instance type facts not read" in message for message in warnings)

    def test_a_failed_read_is_not_kept_so_the_next_request_reads_again(self, monkeypatch, clock):
        _catalog(monkeypatch, FakeInstanceTypesEc2(RECORDS, failure=_client_error()))

        ec2 = FakeInstanceTypesEc2(RECORDS)
        items = _catalog(monkeypatch, ec2)

        assert ec2.requests, "the second request read EC2"
        assert items[GPU_TYPE]["vcpus"] == 8


@pytest.mark.unit
class TestEachCallAndPage:
    def test_a_profile_of_more_types_than_one_call_names_is_read_in_calls_of_at_most_the_limit(self, monkeypatch):
        types = [f"c5.{n}xlarge" for n in range(1, 251)]
        records = {name: _record(name, 4, 8192) for name in types}
        ec2 = FakeInstanceTypesEc2(records)

        items = _catalog(monkeypatch, ec2, types=types)

        assert len(ec2.requests) > 1
        for request in ec2.requests:
            [type_filter] = request["Filters"]
            assert 0 < len(type_filter["Values"]) <= service.DESCRIBE_INSTANCE_TYPES_PER_CALL
        assert sorted(set(ec2.types_asked())) == sorted(types)
        assert sorted(name for name, item in items.items() if item.get("vcpus") == 4) == sorted(types)

    def test_every_page_of_an_answer_is_read(self, monkeypatch):
        types = [f"c5.{n}xlarge" for n in range(1, 8)]
        ec2 = FakeInstanceTypesEc2({name: _record(name, 2, 4096) for name in types}, page_size=3)

        items = _catalog(monkeypatch, ec2, types=types)

        assert {request.get("NextToken") for request in ec2.requests} >= {None, "3", "6"}
        assert sorted(name for name, item in items.items() if "vcpus" in item) == sorted(types)

    def test_a_repeated_token_ends_the_read(self, monkeypatch):
        class Repeating(FakeInstanceTypesEc2):
            def describe_instance_types(self, **kwargs):
                self.requests.append(kwargs)
                if len(self.requests) > 5:
                    raise AssertionError("the read kept following one token")
                return {"InstanceTypes": [RECORDS[GPU_TYPE]], "NextToken": "same"}

        items = _catalog(monkeypatch, Repeating(), types=(GPU_TYPE,))

        assert items[GPU_TYPE]["vcpus"] == 8


@pytest.mark.unit
class TestTheCache:
    def test_a_second_request_within_the_hour_reads_nothing(self, monkeypatch, clock):
        ec2 = FakeInstanceTypesEc2(RECORDS)
        _catalog(monkeypatch, ec2)
        read_before = len(ec2.requests)
        clock[0] += service.INSTANCE_TYPE_FACTS_CACHE_SECONDS - 1

        items = _catalog(monkeypatch, ec2)

        assert len(ec2.requests) == read_before
        assert items[GPU_TYPE]["vcpus"] == 8

    def test_a_type_ec2_does_not_offer_is_kept_as_well(self, monkeypatch, clock):
        ec2 = FakeInstanceTypesEc2(RECORDS)
        _catalog(monkeypatch, ec2, types=(GPU_TYPE, "gr6.4xlarge"))
        read_before = len(ec2.requests)

        items = _catalog(monkeypatch, ec2, types=(GPU_TYPE, "gr6.4xlarge"))

        assert len(ec2.requests) == read_before
        assert "vcpus" not in items["gr6.4xlarge"]

    def test_after_the_hour_the_types_are_read_again(self, monkeypatch, clock):
        ec2 = FakeInstanceTypesEc2(RECORDS)
        _catalog(monkeypatch, ec2)
        read_before = len(ec2.requests)
        clock[0] += service.INSTANCE_TYPE_FACTS_CACHE_SECONDS

        _catalog(monkeypatch, ec2)

        assert len(ec2.requests) > read_before

    def test_only_a_type_not_yet_read_is_read(self, monkeypatch, clock):
        ec2 = FakeInstanceTypesEc2(RECORDS)
        _catalog(monkeypatch, ec2, types=(GPU_TYPE,))
        ec2.requests.clear()

        items = _catalog(monkeypatch, ec2, types=(GPU_TYPE, CPU_TYPE))

        assert ec2.types_asked() == [CPU_TYPE]
        assert items[GPU_TYPE]["vcpus"] == 8 and items[CPU_TYPE]["vcpus"] == 4

    def test_an_empty_catalog_reads_nothing(self, monkeypatch):
        ec2 = FakeInstanceTypesEc2(RECORDS)

        assert _catalog(monkeypatch, ec2, types=()) == {}
        assert ec2.requests == []


# ---------------------------------------------------------------------------
# The recipe image's default instance type
# ---------------------------------------------------------------------------


def _recipe_stack(profile_types=("g5.xlarge", "g5.2xlarge", "g6e.xlarge"), arm_types=("g5g.xlarge",),
                  architecture="x86_64", profile=True):
    stack = {
        service.EDH_FIELD_STACK_ID: RECIPE_STACK_ID,
        service.EDH_FIELD_STACK_NAME: "VAMS Isaac Sim v4",
        service.EDH_FIELD_STACK_BASE_OS: "ubuntu2404",
        service.EDH_FIELD_STACK_OS_FAMILY: "linux",
        service.EDH_FIELD_STACK_ROOT_SIZE: 200,
        service.EDH_FIELD_STACK_DESCRIPTION: "Isaac Sim",
        service.EDH_FIELD_STACK_ARCHITECTURE: architecture,
    }
    if profile:
        stack[service.EDH_FIELD_STACK_PROFILE] = {service.EDH_FIELD_PROFILE_INSTANCE_TYPES: json.dumps(
            {"x86_64": list(profile_types), "arm64": list(arm_types)})}
    return stack


def _baseline_stack():
    return {service.EDH_FIELD_STACK_ID: 12, service.EDH_FIELD_STACK_NAME: "Ubuntu 22",
            service.EDH_FIELD_STACK_BASE_OS: "ubuntu2204", service.EDH_FIELD_STACK_OS_FAMILY: "linux",
            service.EDH_FIELD_STACK_ARCHITECTURE: "x86_64"}


def _images(monkeypatch, launch_types, stack=None, allow_list=()):
    """The image catalog with one recipe image whose recipe lists ``launch_types`` and one baseline image."""
    stack = stack if stack is not None else _recipe_stack()
    fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_software_stacks=[stack, _baseline_stack()]))
    monkeypatch.setattr(service, "allowed_instance_types", tuple(allow_list))
    monkeypatch.setattr(service, "ami_builds_enabled", True)
    recipe = {"recipeId": "isaacsim", "displayName": "NVIDIA Isaac Sim desktop"}
    if launch_types is not None:
        recipe["launchInstanceTypes"] = list(launch_types)
    fakes.recipe_store.available_builds_by_stack_id.return_value = {
        str(RECIPE_STACK_ID): (recipe, {"version": 4})}
    response = fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {})
    assert response["statusCode"] == 200
    return {item["imageId"]: item for item in response_body(response)["Items"]}


@pytest.mark.unit
class TestTheRecipeImagesDefaultInstanceType:
    def test_the_first_of_the_recipes_types_is_the_default(self, monkeypatch):
        items = _images(monkeypatch, ["g5.2xlarge", "g6e.xlarge"])

        assert items[str(RECIPE_STACK_ID)]["defaultInstanceType"] == "g5.2xlarge"

    def test_a_type_the_deployment_does_not_allow_is_passed_over(self, monkeypatch):
        items = _images(monkeypatch, ["g5.2xlarge", "g6e.xlarge"], allow_list=("g6e.xlarge", "g5.xlarge"))

        assert items[str(RECIPE_STACK_ID)]["defaultInstanceType"] == "g6e.xlarge"

    def test_a_type_the_images_profile_does_not_list_is_passed_over(self, monkeypatch):
        items = _images(monkeypatch, ["g6.2xlarge", "g6e.xlarge"])

        assert items[str(RECIPE_STACK_ID)]["defaultInstanceType"] == "g6e.xlarge"

    def test_a_type_of_another_architecture_is_passed_over(self, monkeypatch):
        items = _images(monkeypatch, ["g5g.xlarge", "g5.2xlarge"])

        assert items[str(RECIPE_STACK_ID)]["defaultInstanceType"] == "g5.2xlarge"

    def test_an_image_without_a_readable_profile_takes_the_first_allowed_type(self, monkeypatch):
        items = _images(monkeypatch, ["g6.2xlarge", "g6e.xlarge"], stack=_recipe_stack(profile=False))

        assert items[str(RECIPE_STACK_ID)]["defaultInstanceType"] == "g6.2xlarge"

    def test_no_type_accepted_leaves_the_default_out(self, monkeypatch):
        items = _images(monkeypatch, ["g5.2xlarge"], allow_list=("g5.xlarge",))

        recipe_image = items[str(RECIPE_STACK_ID)]
        assert recipe_image["launchInstanceTypes"] == ["g5.2xlarge"]
        assert "defaultInstanceType" not in recipe_image

    def test_a_recipe_that_lists_no_types_names_no_default(self, monkeypatch):
        items = _images(monkeypatch, None)

        assert items[str(RECIPE_STACK_ID)]["category"] == "amiRecipe"
        assert "defaultInstanceType" not in items[str(RECIPE_STACK_ID)]

    def test_a_baseline_image_names_no_default(self, monkeypatch):
        items = _images(monkeypatch, ["g5.2xlarge"])

        assert items["12"]["category"] == "baseline"
        assert "defaultInstanceType" not in items["12"]
