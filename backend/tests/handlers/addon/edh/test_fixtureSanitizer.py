# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""``fixtureSanitizer`` must strip every credential and deployment identifier from a captured
EDH exchange, and must be a fixed point on its own output so a committed fixture can be
re-verified by running it through ``sanitize_document`` again.

Controls: a document with nothing sensitive is returned unchanged; non-string scalars and key
order are never touched; ``main`` refuses to write when the root key survives under a key name
the redaction list does not know.
"""

import json
import re

import pytest

from backend.tests.handlers.addon.edh import fixtureSanitizer as fs


@pytest.mark.unit
class TestSecretKeysAreRedacted:
    def test_token_keys_are_redacted_at_any_depth(self):
        doc = {
            "X-EDH-TOKEN": "rk-secret-1",
            "session": {"authToken": "at-secret-2", "items": [{"password": "pw-secret-3", "name": "keep"}]},
        }

        out = fs.sanitize_document(doc)

        assert out["X-EDH-TOKEN"] == fs.REDACTED
        assert out["session"]["authToken"] == fs.REDACTED
        assert out["session"]["items"][0]["password"] == fs.REDACTED
        assert out["session"]["items"][0]["name"] == "keep"
        serialized = json.dumps(out)
        assert "rk-secret-1" not in serialized
        assert "at-secret-2" not in serialized
        assert "pw-secret-3" not in serialized

    def test_key_match_is_case_insensitive(self):
        assert fs.sanitize_document({"AuthToken": "x", "Set-Cookie": "y"}) == {
            "AuthToken": fs.REDACTED,
            "Set-Cookie": fs.REDACTED,
        }

    def test_auth_token_query_parameter_is_redacted_and_the_url_shape_survives(self):
        out = fs.sanitize_string("https://dcv.corp.example/dcv/?authToken=abcDEF123#sess-42")

        assert out == "https://dcv.corp.example/dcv/?authToken=<redacted>#sess-42"

    def test_a_connection_string_keeps_its_url_shape_and_only_its_auth_token_is_redacted(self):
        """``connection_string`` is not a redacted key name: the query rule removes the credential
        while the browser URL a consumer has to reproduce stays readable."""
        url = "https://edh-api.example.internal/other.example.internal/?authToken=abcDEF123#3f2c9d4e"

        assert fs.sanitize_document({"connection_string": url}) == {
            "connection_string": "https://edh-api.example.internal/other.example.internal/"
            "?authToken=<redacted>#3f2c9d4e"
        }


@pytest.mark.unit
class TestApiKeyAndAwsCredentialKeysAreRedacted:
    """One case per credential key name that is not a spelling of ``token``, ``password`` or
    ``secret`` the base list already covers."""

    def test_apikey_is_redacted(self):
        assert fs.sanitize_document({"apikey": "ak-1"}) == {"apikey": fs.REDACTED}

    def test_api_key_is_redacted(self):
        assert fs.sanitize_document({"api_key": "ak-2"}) == {"api_key": fs.REDACTED}

    def test_x_api_key_is_redacted(self):
        assert fs.sanitize_document({"x-api-key": "ak-3"}) == {"x-api-key": fs.REDACTED}

    def test_sessiontoken_is_redacted(self):
        assert fs.sanitize_document({"sessiontoken": "st-1"}) == {"sessiontoken": fs.REDACTED}

    def test_session_token_is_redacted(self):
        assert fs.sanitize_document({"session_token": "st-2"}) == {"session_token": fs.REDACTED}

    def test_authentication_token_is_redacted(self):
        assert fs.sanitize_document({"authentication_token": "at-1"}) == {
            "authentication_token": fs.REDACTED
        }

    def test_session_local_admin_password_is_redacted(self):
        assert fs.sanitize_document({"session_local_admin_password": "pw-1"}) == {
            "session_local_admin_password": fs.REDACTED
        }

    def test_broker_session_id_is_redacted(self):
        # A desktop row returns its authentication token a second time under this key.
        assert fs.sanitize_document({"broker_session_id": "bs-1"}) == {"broker_session_id": fs.REDACTED}

    def test_accesskeyid_is_redacted(self):
        assert fs.sanitize_document({"accesskeyid": "AKIAEXAMPLEKEYID0000"}) == {
            "accesskeyid": fs.REDACTED
        }

    def test_secretaccesskey_is_redacted(self):
        assert fs.sanitize_document({"secretaccesskey": "sk-1"}) == {"secretaccesskey": fs.REDACTED}

    def test_bearer_is_redacted(self):
        assert fs.sanitize_document({"bearer": "br-1"}) == {"bearer": fs.REDACTED}

    def test_privatekey_is_redacted(self):
        assert fs.sanitize_document({"privatekey": "-----BEGIN PRIVATE KEY-----"}) == {
            "privatekey": fs.REDACTED
        }

    def test_credentials_is_redacted_together_with_everything_below_it(self):
        assert fs.sanitize_document({"credentials": {"accesskeyid": "a", "nested": ["b"]}}) == {
            "credentials": fs.REDACTED
        }


@pytest.mark.unit
class TestDeploymentIdentifiersAreReplaced:
    def test_api_host_from_the_host_map_becomes_the_api_placeholder(self):
        host_map = {"internal-edh-1234.us-east-1.elb.amazonaws.com": fs.PLACEHOLDER_API_HOST}

        out = fs.sanitize_string(
            "https://internal-edh-1234.us-east-1.elb.amazonaws.com/api/v1/dcv/profiles", host_map
        )

        assert out == f"https://{fs.PLACEHOLDER_API_HOST}/api/v1/dcv/profiles"

    def test_other_aws_hostnames_become_the_other_placeholder(self):
        out = fs.sanitize_string("ip-10-20-30-40.ec2.internal and edh-dcv-99.us-gov-west-1.elb.amazonaws.com")

        assert out == f"{fs.PLACEHOLDER_OTHER_HOST} and {fs.PLACEHOLDER_OTHER_HOST}"

    def test_account_ids_inside_arns_and_standalone_are_replaced(self):
        out = fs.sanitize_string("arn:aws-us-gov:iam::210987654321:role/edh-dcv 210987654321")

        assert out == f"arn:aws-us-gov:iam::{fs.PLACEHOLDER_ACCOUNT}:role/edh-dcv {fs.PLACEHOLDER_ACCOUNT}"

    def test_an_ecr_registry_keeps_its_region_and_the_placeholder_form_is_a_fixed_point(self):
        # The registry host names the repository a container job pulls, so the region has to stay
        # readable; only the account id in front of it is a deployment identifier.
        once = fs.sanitize_string(
            "210987654321.dkr.ecr.us-gov-west-1.amazonaws.com/vams-spike-edh-probe:latest"
        )

        assert once == (
            f"{fs.PLACEHOLDER_ACCOUNT}.dkr.ecr.us-gov-west-1.amazonaws.com"
            "/vams-spike-edh-probe:latest"
        )
        assert fs.sanitize_string(once) == once

    def test_thirteen_digit_timestamps_are_not_touched(self):
        assert fs.sanitize_string("1757340000123") == "1757340000123"

    def test_ipv4_and_email_are_replaced(self):
        out = fs.sanitize_string("10.42.7.9 owner@corp.example.com")

        assert out == f"{fs.PLACEHOLDER_IPV4} {fs.PLACEHOLDER_EMAIL}"

    def test_aws_resource_ids_are_replaced_deterministically_and_distinctly(self):
        first = fs.sanitize_string("i-0abc123def4567890")
        again = fs.sanitize_string("i-0abc123def4567890")
        other = fs.sanitize_string("i-0abc123def4567891")

        assert first == again
        assert first != other
        assert first.startswith("i-" + fs.SANITIZED_ID_MARK)
        assert "0abc123def4567890" not in first

    def test_ami_and_security_group_ids_follow_the_same_rule(self):
        out = fs.sanitize_string("ami-0123456789abcdef0 sg-0fedcba9876543210")

        ami, sg = out.split(" ")
        assert ami.startswith("ami-" + fs.SANITIZED_ID_MARK) and ami != "ami-0123456789abcdef0"
        assert sg.startswith("sg-" + fs.SANITIZED_ID_MARK) and sg != "sg-0fedcba9876543210"

    def test_sanitized_ids_are_a_fixed_point_and_never_look_like_an_account_id(self):
        # A replacement whose tail happened to form a 12-digit run would be rewritten by the
        # account-id rule on the next pass; the tail therefore carries no digits at all. Ids with
        # many leading zeros are real ids too and must still be hashed.
        for n in range(2000):
            once = fs.sanitize_string(f"i-0{n:016x} subnet-{n:08x} i-00000{n:012x}")

            assert fs.sanitize_string(once) == once, once
            assert not re.search(r"\d{12}", once), once
            assert re.fullmatch(r"i-00000[a-f]{12} subnet-00000[a-f]{12} i-00000[a-f]{12}", once), once


@pytest.mark.unit
class TestSanitizationIsIdempotentAndBounded:
    def test_a_clean_document_is_returned_unchanged(self):
        # Control: uuids, states, numbers, booleans and None carry nothing to strip.
        doc = {
            "session_uuid": "4f3c0d3e-9a1b-4c2d-8e7f-0a1b2c3d4e5f",
            "state": "running",
            "count": 2,
            "active": True,
            "none": None,
            "tags": ["a", "b"],
        }

        assert fs.sanitize_document(doc) == doc

    def test_sanitizing_twice_equals_sanitizing_once(self):
        doc = {
            "authToken": "t",
            "url": "https://x.elb.amazonaws.com/?authToken=q#1",
            "arn": "arn:aws:iam::210987654321:role/r",
            "id": "i-0abc123def4567890",
            "ip": "10.1.2.3",
            "mail": "a@b.example",
        }

        once = fs.sanitize_document(doc)

        assert fs.sanitize_document(once) == once

    def test_placeholder_hosts_are_left_alone(self):
        text = f"https://{fs.PLACEHOLDER_API_HOST}/x https://{fs.PLACEHOLDER_OTHER_HOST}/y"

        assert fs.sanitize_string(text) == text

    def test_key_order_is_preserved(self):
        doc = {"b": 1, "a": 2, "c": {"z": 1, "y": 2}}

        assert list(fs.sanitize_document(doc)) == ["b", "a", "c"]
        assert list(fs.sanitize_document(doc)["c"]) == ["z", "y"]


@pytest.mark.unit
class TestEnvelope:
    def test_envelope_carries_request_response_and_expect_and_never_a_token_value(self):
        env = fs.build_envelope(
            name="virtual_desktops_list_active",
            method="get",
            path="/api/v1/dcv/virtual_desktops/list",
            base_path="/api/v1",
            status=200,
            elapsed_seconds=0.4567,
            response_body={"message": [{"session_uuid": "a"}, {"session_uuid": "b"}]},
            edh_uid="vspikeaaaaaaaaaa",
            query={"is_active": "true"},
            expect={"count": 2},
            content_type="application/json",
            edh_version="26.4.0",
            captured_at="2026-09-10T00:00:00Z",
        )

        assert env["name"] == "virtual_desktops_list_active"
        assert env["edhVersion"] == "26.4.0"
        assert env["capturedAt"] == "2026-09-10T00:00:00Z"
        assert env["request"] == {
            "method": "GET",
            "path": "/dcv/virtual_desktops/list",
            "absolutePath": "/api/v1/dcv/virtual_desktops/list",
            "query": {"is_active": "true"},
            "headers": {"X-EDH-USER": "vspikeaaaaaaaaaa"},
            "tokenHeaderName": "X-EDH-TOKEN",
            "body": None,
        }
        assert env["response"] == {
            "status": 200,
            "contentType": "application/json",
            "elapsedSeconds": 0.457,
            "json": {"message": [{"session_uuid": "a"}, {"session_uuid": "b"}]},
        }
        assert env["expect"] == {"count": 2}
        assert list(env) == ["name", "capturedAt", "edhVersion", "request", "response", "expect"]

    def test_envelope_without_a_uid_has_no_user_header_and_an_empty_expect(self):
        env = fs.build_envelope(
            name="n", method="GET", path="/p", status=401, elapsed_seconds=0.1, response_body=None
        )

        assert env["request"]["headers"] == {}
        assert env["expect"] == {}
        assert env["capturedAt"].endswith("Z")

    def test_path_outside_the_base_is_kept_whole_and_the_absolute_path_is_always_recorded(self):
        assert fs.relative_path("/api/v1/dcv/profiles", "/api/v1") == "/dcv/profiles"
        assert fs.relative_path("/api/ldap/users", "/api/v1") == "/api/ldap/users"
        assert fs.relative_path("ec2:DescribeInstances", "/api/v1") == "ec2:DescribeInstances"
        assert fs.relative_path("/api/v1", "/api/v1") == "/"

        env = fs.build_envelope(
            name="n", method="GET", path="/api/ldap/users", base_path="/api/v1", status=200,
            elapsed_seconds=0.1, response_body=[],
        )

        assert env["request"]["path"] == "/api/ldap/users"
        assert env["request"]["absolutePath"] == "/api/ldap/users"

    def test_header_names_are_recorded_as_sent(self):
        env = fs.build_envelope(
            name="n", method="GET", path="/p", status=200, elapsed_seconds=0.1, response_body=None,
            edh_uid="vspikeaaaaaaaaaa", user_header_name="x-edh-user", token_header_name="x-edh-token",
        )

        assert env["request"]["headers"] == {"x-edh-user": "vspikeaaaaaaaaaa"}
        assert env["request"]["tokenHeaderName"] == "x-edh-token"

    def test_expect_values_parse_as_json_and_fall_back_to_strings(self):
        parsed = fs.parse_expect(["count=2", "imageId=\"7\"", "sessionUuid=4f3c0d3e-9a1b-4c2d-8e7f-0a1b2c3d4e5f", "displayName=VAMS Spike A", "flag=true"])

        assert parsed == {
            "count": 2,
            "imageId": "7",
            "sessionUuid": "4f3c0d3e-9a1b-4c2d-8e7f-0a1b2c3d4e5f",
            "displayName": "VAMS Spike A",
            "flag": True,
        }

    def test_load_body_returns_json_or_wraps_non_json_text(self, tmp_path):
        good = tmp_path / "good.json"
        good.write_text('{"a": 1}', encoding="utf-8")
        html = tmp_path / "bad.json"
        html.write_text("<html>502 Bad Gateway</html>", encoding="utf-8")
        empty = tmp_path / "empty.json"
        empty.write_text("", encoding="utf-8")

        assert fs.load_body(good) == {"a": 1}
        assert fs.load_body(html) == {"_raw": "<html>502 Bad Gateway</html>"}
        assert fs.load_body(empty) is None


@pytest.mark.unit
class TestMainRefusesToLeak:
    def _run(self, tmp_path, monkeypatch, body, extra_args=()):
        monkeypatch.setenv("EDH_ROOT_KEY", "rootkey-value-0001")
        monkeypatch.setenv("EDH_API_ENDPOINT", "https://internal-edh-1.us-east-1.elb.amazonaws.com/api/v1")
        raw = tmp_path / "raw.json"
        raw.write_text(json.dumps(body), encoding="utf-8")
        out = tmp_path / "fixtures" / "probe.json"
        code = fs.main(
            [
                "--name", "probe",
                "--method", "GET",
                "--path", "/api/v1/dcv/profiles",
                "--base-path", "/api/v1",
                "--status", "200",
                "--elapsed", "0.25",
                "--edh-uid", "vspikeaaaaaaaaaa",
                "--query", "is_active=true",
                "--expect", "count=1",
                "--response-body", str(raw),
                "--out", str(out),
                *extra_args,
            ]
        )
        return code, out

    def test_writes_a_sanitized_envelope_when_nothing_leaks(self, tmp_path, monkeypatch):
        code, out = self._run(
            tmp_path,
            monkeypatch,
            {"password": "rootkey-value-0001", "host": "internal-edh-1.us-east-1.elb.amazonaws.com"},
        )

        assert code == 0
        written = json.loads(out.read_text(encoding="utf-8"))
        assert written["response"]["json"] == {"password": fs.REDACTED, "host": fs.PLACEHOLDER_API_HOST}
        assert written["request"]["path"] == "/dcv/profiles"
        assert written["request"]["query"] == {"is_active": "true"}
        assert written["request"]["headers"] == {"X-EDH-USER": "vspikeaaaaaaaaaa"}
        assert written["expect"] == {"count": 1}

    def test_the_api_host_mapping_wins_over_a_replace_host_for_the_same_host(self, tmp_path, monkeypatch):
        # A DCV host that equals the management host must still read as the API placeholder, so the
        # fixture can tell "same host" from "other host".
        code, out = self._run(
            tmp_path,
            monkeypatch,
            {"dcv": "https://internal-edh-1.us-east-1.elb.amazonaws.com/dcv/", "other": "https://dcv.corp.example/"},
            extra_args=["--replace-host", "internal-edh-1.us-east-1.elb.amazonaws.com", "--replace-host", "dcv.corp.example"],
        )

        assert code == 0
        written = json.loads(out.read_text(encoding="utf-8"))
        assert written["response"]["json"] == {
            "dcv": f"https://{fs.PLACEHOLDER_API_HOST}/dcv/",
            "other": f"https://{fs.PLACEHOLDER_OTHER_HOST}/",
        }

    def test_refuses_when_the_root_key_survives_under_an_unlisted_key(self, tmp_path, monkeypatch, capsys):
        code, out = self._run(tmp_path, monkeypatch, {"tkn": "rootkey-value-0001"})

        assert code == 2
        assert not out.exists()
        assert "EDH_ROOT_KEY" in capsys.readouterr().err

    def test_refuses_when_the_real_api_host_survives_in_mixed_case(self, tmp_path, monkeypatch):
        # The host map is case-insensitive, so this can only leak through a code path that skips
        # it; the guard is the last line of defence and must still fire on the serialized text.
        monkeypatch.setattr(fs, "sanitize_document", lambda doc, host_map=None: doc)
        code, out = self._run(tmp_path, monkeypatch, {"h": "INTERNAL-EDH-1.us-east-1.elb.amazonaws.com"})

        assert code == 2
        assert not out.exists()
