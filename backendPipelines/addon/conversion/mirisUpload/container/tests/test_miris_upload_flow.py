#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The container's upload flow: a failure after the Miris asset exists keeps it and logs its id, and
every exit code has a reason in the pipelineEnd lambda."""
import ast
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import responses

from miris_upload_container_harness import load_entry_point

pytestmark = pytest.mark.unit

_CONTAINER = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _CONTAINER)

UUID = "11111111-2222-3333-4444-555555555555"
BASE = "https://app.miris.test"


@pytest.fixture
def entry():
    return load_entry_point()


def _start_response():
    return {
        "id": UUID,
        "upload_path": "s3://miris-uploads/ws/up",
        "endpoint_url": "https://s3.example.test",
        "region": "us-west-1",
        "access_key_id": "AK",
        "secret_key": "SK",
        "session_token": "ST",
    }


def _client():
    from miris_uploader import MirisClient

    client = MagicMock(spec=MirisClient)
    client.start_upload.return_value = _start_response()
    client.poll_until_terminal.return_value = {"state": "preview"}
    client.trigger_generate_best_effort.return_value = {"state": "processing_streamable"}
    return client


def _upload_args(write_metadata=None, task_timeout=60, **overrides):
    args = dict(
        name="Pump",
        tags=["vams"],
        upload_local="/workdir/pump.usdz",
        upload_filename="pump.usdz",
        content_type="model/vnd.usdz+zip",
        size=10,
        task_timeout=task_timeout,
        write_metadata=write_metadata or MagicMock(),
    )
    args.update(overrides)
    return args


def _logs(output, msg):
    lines = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    return [line for line in lines if line.get("msg") == msg]


def _run(entry, client, write_metadata=None):
    return entry.run_miris_upload(client, **_upload_args(write_metadata))


def _kept_asset_logs(output):
    return _logs(output, "miris_asset_kept")


@pytest.mark.parametrize("generate", [{"state": "processing_streamable"}, None], ids=["generated", "generate-failed"])
def test_a_successful_upload_writes_the_metadata_whatever_generate_returns(entry, capsys, generate):
    client = _client()
    client.trigger_generate_best_effort.return_value = generate
    write_metadata = MagicMock()
    with patch.object(entry, "_put_artifact"):
        assert _run(entry, client, write_metadata) == UUID

    write_metadata.assert_called_once_with(UUID)
    assert _kept_asset_logs(capsys.readouterr().out) == []


@pytest.mark.parametrize("failing_step", [
    "put_artifact", "mark_upload_complete", "poll_until_terminal", "write_metadata"])
def test_a_failure_after_start_upload_keeps_the_asset_and_logs_its_id(entry, capsys, failing_step):
    from miris_uploader import MirisError

    client = _client()
    write_metadata = MagicMock()
    put = MagicMock()
    error = MirisError(f"{failing_step} failed")
    if failing_step == "put_artifact":
        put.side_effect = error
    elif failing_step == "write_metadata":
        write_metadata.side_effect = error
    else:
        getattr(client, failing_step).side_effect = error

    with patch.object(entry, "_put_artifact", put), pytest.raises(MirisError):
        _run(entry, client, write_metadata)

    assert _kept_asset_logs(capsys.readouterr().out) == [
        {"msg": "miris_asset_kept", "asset_uuid": UUID, "error": f"{failing_step} failed"}]


def test_a_start_upload_failure_has_no_asset_to_keep(entry, capsys):
    from miris_uploader import MirisError

    client = _client()
    client.start_upload.side_effect = MirisError("POST /v1/asset failed [500]")

    with pytest.raises(MirisError):
        _run(entry, client)
    assert _kept_asset_logs(capsys.readouterr().out) == []


def test_the_stream_metadata_is_written_when_the_upload_completes_before_the_processing_wait(entry):
    client = _client()
    write_metadata = MagicMock()
    order = MagicMock()
    order.attach_mock(client.mark_upload_complete, "mark_upload_complete")
    order.attach_mock(write_metadata, "write_metadata")
    order.attach_mock(client.poll_until_terminal, "poll_until_terminal")

    with patch.object(entry, "_put_artifact"):
        _run(entry, client, write_metadata)

    names = [name for name, _args, _kwargs in order.mock_calls]
    assert names.index("mark_upload_complete") < names.index("write_metadata")
    assert names.index("write_metadata") < names.index("poll_until_terminal")


NOW = 10_000.0


def _budget(entry):
    return entry.time_budget


def test_a_deadline_reached_during_the_upload_still_checks_the_state_once(entry):
    client = _client()
    clock = iter([NOW, NOW + 10_000])
    deadline = NOW + _budget(entry).END_RESERVE_SECONDS + _budget(entry).upload_seconds_needed(10) + 1

    with patch.object(entry, "_put_artifact"):
        entry.run_miris_upload(
            client, **_upload_args(deadline=deadline, clock=lambda: next(clock)))

    assert client.poll_until_terminal.call_args.kwargs["timeout_seconds"] == 0


@pytest.mark.parametrize("short_by", [1, 0], ids=["one-second-short", "just-enough"])
def test_an_upload_starts_only_with_time_for_it(entry, capsys, short_by):
    client = _client()
    size = 100 * 1024 * 1024
    needed = _budget(entry).upload_seconds_needed(size)
    deadline = NOW + _budget(entry).END_RESERVE_SECONDS + needed - short_by
    put = MagicMock()
    args = _upload_args(size=size, deadline=deadline, clock=lambda: NOW)

    with patch.object(entry, "_put_artifact", put):
        if not short_by:
            assert entry.upload_or_exit(client, **args) == UUID
            return
        with pytest.raises(SystemExit) as exited:
            entry.upload_or_exit(client, **args)

    assert exited.value.code == entry.EXIT_INSUFFICIENT_TIME == 14
    client.start_upload.assert_not_called()
    put.assert_not_called()
    [line] = _logs(capsys.readouterr().out, "insufficient_time")
    assert (line["seconds_left"], line["seconds_needed"]) == (int(needed - 1), int(needed))


def _miris_api(asset_state):
    """Miris over HTTP: the asset is created, and every poll reports `asset_state`. A DELETE of the
    asset is answered too, so a stray one is recorded as a call rather than failing on its own."""
    responses.add(responses.POST, f"{BASE}/v1/asset", json=_start_response(), status=200)
    responses.add(responses.PUT, f"{BASE}/v1/asset/upload/{UUID}", json={"id": UUID}, status=200)
    responses.add(responses.GET, f"{BASE}/v1/asset/{UUID}", json={"id": UUID, "state": asset_state},
                  status=200)
    responses.add(responses.POST, f"{BASE}/v1/asset/{UUID}/generate", json={}, status=200)
    responses.add(responses.DELETE, f"{BASE}/v1/asset/{UUID}", status=204)


@responses.activate
@pytest.mark.parametrize("failure,asset_state,expected", [
    ("processing_unfinished", "processing_preview", None),
    ("terminal_error", "failed", 8),
    ("s3_put", "preview", RuntimeError),
    ("metadata_write", "preview", RuntimeError),
])
def test_no_outcome_sends_a_delete_to_miris(entry, capsys, failure, asset_state, expected):
    from miris_uploader import MirisClient

    _miris_api(asset_state)
    client = MirisClient(BASE, "key", sleep=lambda _seconds: None)
    put = MagicMock(side_effect=RuntimeError("S3 PUT failed") if failure == "s3_put" else None)
    write_metadata = MagicMock(
        side_effect=RuntimeError("metadata write failed") if failure == "metadata_write" else None)
    args = _upload_args(write_metadata, task_timeout=0)

    with patch.object(entry, "_put_artifact", put):
        if expected is None:
            assert entry.upload_or_exit(client, **args) == UUID
        elif isinstance(expected, int):
            with pytest.raises(SystemExit) as exited:
                entry.upload_or_exit(client, **args)
            assert exited.value.code == expected
        else:
            with pytest.raises(expected):
                entry.upload_or_exit(client, **args)

    methods = [call.request.method for call in responses.calls]
    # Control: the run reached Miris, so the absence below is not vacuous.
    assert methods[0] == "POST"
    assert "DELETE" not in methods, methods
    output = capsys.readouterr().out
    kept = _kept_asset_logs(output)
    assert [line["asset_uuid"] for line in kept] == ([] if expected is None else [UUID])
    if failure == "processing_unfinished":
        # The run succeeds linked, without asking for promotion.
        assert [line["asset_uuid"] for line in _logs(output, "processing_unfinished")] == [UUID]
        assert not any(call.request.url.endswith("/generate") for call in responses.calls)
    if failure == "s3_put":
        write_metadata.assert_not_called()
    else:
        write_metadata.assert_called_once_with(UUID)


def _module_int_constants(path, prefix):
    tree = ast.parse(open(path, encoding="utf-8").read())
    return {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id.startswith(prefix)
        and isinstance(node.value, ast.Constant) and isinstance(node.value.value, int)
    }


def _end_lambda_exit_codes():
    path = os.path.join(_CONTAINER, "..", "lambda", "pipelineEnd.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "EXIT_CODE_REASONS":
            return {key.value for key in node.value.keys}
    raise AssertionError("EXIT_CODE_REASONS not found in pipelineEnd.py")


def test_every_exit_code_has_a_user_facing_reason_in_the_end_lambda():
    """The container and the lambda are separate code assets, so the table is kept in step here."""
    codes = _module_int_constants(os.path.join(_CONTAINER, "__main__.py"), "EXIT_")
    # Control: the parse found the constants, or the subset check below is vacuous.
    assert len(codes) >= 8
    assert len(set(codes.values())) == len(codes), "two exit codes share a value"
    reasons = _end_lambda_exit_codes()
    assert set(codes.values()) | {1} <= reasons, sorted(set(codes.values()) - reasons)
    # Nor a reason for a code the container does not exit with.
    assert reasons <= set(codes.values()) | {1}, sorted(reasons - set(codes.values()) - {1})


@pytest.mark.parametrize("field, value", [
    ("id", "../../v1/asset"), ("id", ""), ("id", None),
], ids=["path-traversal", "empty", "missing"])
def test_an_unusable_asset_id_fails_before_anything_is_uploaded(entry, capsys, field, value):
    from miris_uploader import MirisError

    client = _client()
    client.start_upload.return_value = {**_start_response(), field: value}
    put, write_metadata = MagicMock(), MagicMock()
    with patch.object(entry, "_put_artifact", put), pytest.raises(MirisError, match="asset id"):
        _run(entry, client, write_metadata)

    put.assert_not_called()
    write_metadata.assert_not_called()
    client.mark_upload_complete.assert_not_called()
    assert _kept_asset_logs(capsys.readouterr().out) == []


@pytest.mark.parametrize("field, value, problem", [
    ("endpoint_url", "http://s3.example.test", "endpoint"),
    ("endpoint_url", None, "endpoint"),
    ("upload_path", "https://miris-uploads/ws/up", "upload path"),
    ("upload_path", "s3://", "upload path"),
], ids=["plain-http-endpoint", "no-endpoint", "upload-path-not-s3", "upload-path-without-bucket"])
def test_an_unusable_upload_location_uploads_nothing_and_keeps_the_asset(entry, capsys, field, value, problem):
    from miris_uploader import MirisError

    client = _client()
    client.start_upload.return_value = {**_start_response(), field: value}
    write_metadata = MagicMock()
    with patch.object(entry.boto3, "client") as s3_client, pytest.raises(MirisError, match=problem):
        _run(entry, client, write_metadata)

    s3_client.assert_not_called()
    write_metadata.assert_not_called()
    assert [log["asset_uuid"] for log in _kept_asset_logs(capsys.readouterr().out)] == [UUID]


def test_the_start_response_is_logged_by_an_allowlist(entry, capsys):
    client = _client()
    client.start_upload.return_value = {
        **_start_response(), "extra": {"credential": "nested-secret"}, "token": "top-secret"}
    with patch.object(entry, "_put_artifact"):
        _run(entry, client)

    output = capsys.readouterr().out
    assert _logs(output, "start_upload") == [{
        "msg": "start_upload", "asset_uuid": UUID, "region": "us-west-1",
        "endpoint_host": "s3.example.test"}]
    for secret in ("nested-secret", "top-secret", "SK", "ST", "miris-uploads"):
        assert secret not in output


def test_a_kept_asset_logs_only_the_type_of_an_error_that_is_not_a_miris_error(entry, capsys):
    from botocore.exceptions import ClientError

    client = _client()
    error = ClientError({"Error": {"Code": "AccessDenied", "Message": "upload detail s3://secret"}}, "PutObject")
    with patch.object(entry, "_put_artifact", side_effect=error), pytest.raises(ClientError):
        _run(entry, client)

    output = capsys.readouterr().out
    assert _kept_asset_logs(output) == [
        {"msg": "miris_asset_kept", "asset_uuid": UUID, "error": "ClientError"}]
    assert "s3://secret" not in output
