# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The instance-type catalog: the deployment's EDH profile filtered by the configured allow-list,
sorted by name with each run of digits compared as a number (``g5.2xlarge`` before ``g5.12xlarge``).

Each item names the architecture groups of the profile value that list the type, in the value's key
order; a value not keyed by architecture names none. An empty allow-list means unfiltered. EDH's field
names are read from the handler's constants so a spike correction there does not break these tests.
"""

import json
import re

import pytest

from backend.backend.handlers.addon.edh.common.edhClient import EdhUnavailableError
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    INSTANCE_TYPES_PATH,
    USER,
    FakeEdhClient,
    edh_proxy_records,
    install_service_fakes,
    make_event,
    response_body,
)
from backend.tests.handlers.addon.edh.edhTestSupport import load_fixture
import backend.backend.handlers.addon.edh.edhWorkstationService as service

PROFILE_TYPES = ["g5.xlarge", "g5.2xlarge", "g6.xlarge"]
# An EC2 instance type name -- family, dot, size (``g4dn.xlarge``, ``u-6tb1.metal``, ``c7i.metal-24xl``)
# -- and never a fragment of the encoding it arrived in.
INSTANCE_TYPE_NAME = re.compile(r"[a-z0-9-]+\.[a-z0-9-]+")


def _profile(profile_id, types):
    return {service.EDH_FIELD_PROFILE_ID: profile_id, service.EDH_FIELD_PROFILE_INSTANCE_TYPES: types}


def _profiles():
    return [_profile(7, ["c6i.large"]), _profile(1, PROFILE_TYPES)]


def _items(response):
    return [item["instanceType"] for item in response_body(response)["Items"]]


def _catalog(monkeypatch, value, allow_list=()):
    """The catalog served from one deployment profile whose allowed-types value is ``value``."""
    fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=[_profile(1, value)]))
    monkeypatch.setattr(service, "allowed_instance_types", allow_list)
    response = fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {})
    assert response["statusCode"] == 200
    return response_body(response)["Items"]


@pytest.mark.unit
class TestInstanceTypeCatalog:
    def test_returns_the_profile_types_filtered_by_the_allow_list_sorted(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=_profiles()))
        monkeypatch.setattr(service, "allowed_instance_types", ("g5.xlarge", "g5.2xlarge"))

        response = fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {})

        assert response["statusCode"] == 200
        # Sorted, neither profile nor allow-list order; g6.xlarge is dropped by the allow-list.
        assert _items(response) == ["g5.2xlarge", "g5.xlarge"]
        assert fakes.edh.calls_to("list_profiles") == [("dcv",)]

    def test_the_types_are_sorted_by_name_with_numbers_in_numeric_order(self, monkeypatch):
        listed = ["m7i.xlarge", "g5.12xlarge", "c6i.large", "m7i.large", "g5.2xlarge"]
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=[_profile(1, listed)]))
        monkeypatch.setattr(service, "allowed_instance_types",
                            ("m7i.xlarge", "g5.12xlarge", "m7i.large", "g5.2xlarge"))

        response = fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {})

        # c6i.large is not allowed; the rest are in order, g5.2xlarge before g5.12xlarge.
        assert _items(response) == ["g5.2xlarge", "g5.12xlarge", "m7i.large", "m7i.xlarge"]

    def test_the_sort_key_ignores_case_and_compares_digit_runs_as_numbers(self):
        names = ["Image 10", "image 2", "IMAGE 1b", "image 1a", "g5.12xlarge", "G5.2xlarge", ""]
        assert sorted(names, key=service._natural_key) == [
            "", "G5.2xlarge", "g5.12xlarge", "image 1a", "IMAGE 1b", "image 2", "Image 10"]

    def test_an_empty_allow_list_returns_every_profile_type(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=_profiles()))
        monkeypatch.setattr(service, "allowed_instance_types", ())

        response = fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {})

        assert _items(response) == ["g5.2xlarge", "g5.xlarge", "g6.xlarge"]

    def test_the_profile_is_selected_by_the_configured_id_whatever_its_type(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch,
            edh=FakeEdhClient(list_profiles=[_profile("1", ["g5.xlarge"]), _profile(2, ["p4d.24xlarge"])]),
        )
        monkeypatch.setattr(service, "allowed_instance_types", ())

        response = fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {})

        assert _items(response) == ["g5.xlarge"]

    def test_a_comma_separated_profile_value_is_accepted(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(list_profiles=[_profile(1, " g5.xlarge, g5.2xlarge ,")]))
        monkeypatch.setattr(service, "allowed_instance_types", ())

        response = fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {})

        assert _items(response) == ["g5.2xlarge", "g5.xlarge"]

    def test_the_captured_profile_value_is_read_as_the_json_string_keyed_by_architecture(self, monkeypatch):
        """EDH serialises a profile's allowed types as a JSON string keyed by architecture; the
        catalog lists the names inside it, never fragments of the encoding."""
        capture = load_fixture("profiles_dcv")
        profiles = list(capture["response"]["json"]["message"].values())
        assert len(profiles) == capture["expect"]["count"]
        profile = profiles[0]
        by_architecture = json.loads(profile[service.EDH_FIELD_PROFILE_INSTANCE_TYPES])
        expected = [item for arch_types in by_architecture.values() for item in arch_types]
        assert expected, "the captured profile allows no instance type"
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=profiles))
        monkeypatch.setattr(service, "vdi_profile_id", int(profile[service.EDH_FIELD_PROFILE_ID]))
        monkeypatch.setattr(service, "allowed_instance_types", ())

        response = fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {})

        assert response["statusCode"] == 200
        catalog = _items(response)
        assert catalog == sorted(expected, key=service._natural_key)
        assert all(INSTANCE_TYPE_NAME.fullmatch(item) for item in catalog)
        assert {item["instanceType"]: item["architectures"] for item in response_body(response)["Items"]} == {
            item: [arch for arch, arch_types in by_architecture.items() if item in arch_types] for item in expected}
        assert all(item["architectures"] == ["x86_64"] for item in response_body(response)["Items"])

    def test_a_type_listed_under_two_architectures_appears_once(self, monkeypatch):
        by_architecture = json.dumps({"x86_64": ["g5.xlarge", "g6.xlarge"], "arm64": ["g5.xlarge", "g5g.xlarge"]})
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=[_profile(1, by_architecture)]))
        monkeypatch.setattr(service, "allowed_instance_types", ())

        response = fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {})

        assert _items(response) == ["g5.xlarge", "g5g.xlarge", "g6.xlarge"]
        assert response_body(response)["Items"][0]["architectures"] == ["x86_64", "arm64"]

    def test_each_item_names_the_groups_that_list_it_in_the_values_key_order(self, monkeypatch):
        """``arm64`` leads this value and ``x86_64`` precedes ``i386``; the groups keep that order, which
        neither a sort nor naming ``x86_64`` first would give."""
        items = _catalog(monkeypatch, json.dumps({
            "arm64": ["m7g.large", "g5g.xlarge"], "x86_64": ["m7i.large", "m7g.large", "t3.large"],
            "i386": ["t3.large"]}))

        assert items == [
            {"instanceType": "g5g.xlarge", "architectures": ["arm64"]},
            {"instanceType": "m7g.large", "architectures": ["arm64", "x86_64"]},
            {"instanceType": "m7i.large", "architectures": ["x86_64"]},
            {"instanceType": "t3.large", "architectures": ["x86_64", "i386"]},
        ]

    def test_a_type_listed_twice_in_one_group_names_it_once(self, monkeypatch):
        items = _catalog(monkeypatch, json.dumps({"x86_64": ["m7i.large", " m7i.large "]}))

        assert items == [{"instanceType": "m7i.large", "architectures": ["x86_64"]}]

    def test_an_i386_group_is_named_as_it_is(self, monkeypatch):
        items = _catalog(monkeypatch, json.dumps({"x86_64": ["m7i.large"], "arm64": [], "i386": ["t2.medium"]}))

        assert items == [
            {"instanceType": "m7i.large", "architectures": ["x86_64"]},
            {"instanceType": "t2.medium", "architectures": ["i386"]},
        ]

    @pytest.mark.parametrize("value", [
        ["g5.xlarge", "g5.2xlarge"],
        " g5.xlarge, g5.2xlarge ,",
        json.dumps(["g5.xlarge", "g5.2xlarge"]),
        json.dumps("g5.xlarge,g5.2xlarge"),
    ], ids=["list", "comma-string", "json-list", "json-string"])
    def test_a_value_not_keyed_by_architecture_names_no_group(self, monkeypatch, value):
        items = _catalog(monkeypatch, value)

        assert items == [
            {"instanceType": "g5.2xlarge", "architectures": []},
            {"instanceType": "g5.xlarge", "architectures": []},
        ]

    def test_the_allow_list_keeps_the_groups_of_the_types_it_keeps(self, monkeypatch):
        value = json.dumps({"x86_64": ["g5.xlarge", "m7i.large"], "arm64": ["g5.xlarge", "m7g.large"]})

        items = _catalog(monkeypatch, value, allow_list=("g5.xlarge", "m7g.large"))

        assert items == [
            {"instanceType": "g5.xlarge", "architectures": ["x86_64", "arm64"]},
            {"instanceType": "m7g.large", "architectures": ["arm64"]},
        ]

    def test_a_json_encoded_string_value_is_read_as_names(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(list_profiles=[_profile(1, json.dumps("g5.xlarge,g5.2xlarge"))]))
        monkeypatch.setattr(service, "allowed_instance_types", ())

        response = fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {})

        assert _items(response) == ["g5.2xlarge", "g5.xlarge"]

    def test_a_missing_profile_is_a_502_without_echoing_edh(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=[_profile(9, PROFILE_TYPES)]))

        response = fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {})

        assert response["statusCode"] == 502
        assert response_body(response)["message"] == "EDH virtual desktop profile is unavailable"

    def test_edh_unavailable_is_a_502(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(list_profiles=EdhUnavailableError("connect timeout")))

        response = fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {})

        assert response["statusCode"] == 502

    def test_the_call_is_audited_as_an_edh_proxy_record(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=_profiles()))

        fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {})

        records = edh_proxy_records(fakes)
        assert len(records) == 1
        assert records[0]["operation"] == "catalogInstanceTypes"
        assert records[0]["edhUid"] == edh_uid_for(USER)
        assert records[0]["edhStatus"] == "ok"
        assert (records[0]["edhMethod"], records[0]["edhRoute"]) == service.EDH_AUDIT_ROUTES["list_profiles"]

    def test_a_failed_call_is_audited_with_the_error_type(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(list_profiles=EdhUnavailableError("connect timeout")))

        fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {})

        assert [record["edhStatus"] for record in edh_proxy_records(fakes)] == ["EdhUnavailableError"]

    def test_an_unexpected_failure_is_audited_and_is_a_500(self, monkeypatch):
        """A wrapper failure that is not a typed EDH error still leaves an edhProxy record before the 500."""
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(list_profiles=RuntimeError("unexpected")))

        response = fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {})

        assert response["statusCode"] == 500
        assert [record["edhStatus"] for record in edh_proxy_records(fakes)] == ["RuntimeError"]

    def test_a_rest_shaped_event_is_served(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=_profiles()))
        monkeypatch.setattr(service, "allowed_instance_types", ())

        response = fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH, rest=True), {})

        assert response["statusCode"] == 200
        assert _items(response) == ["g5.2xlarge", "g5.xlarge", "g6.xlarge"]
