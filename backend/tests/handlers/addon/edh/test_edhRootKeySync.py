# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The root key sync copies the EDH root key from the controller into the root-key secret.

EDH writes a new key to a file on the controller each time its web interface starts and publishes it
nowhere. The sync confirms that the controller EDH names in its ``ControllerInstanceId`` parameter is a
running instance carrying EDH's controller tags for the cluster, generates an RSA key pair for the run, in
memory, and runs the deployment's own Run Command document, at its pinned version, on that instance with
the public half as its only parameter. The document prints the key file encrypted to that key (RSAES-OAEP
with SHA-256), so Run Command keeps only ciphertext; the sync decrypts it with the run's private key, drops
the key, accepts the plaintext only when it is one 32-character alphanumeric key, and writes
``{"rootKey": <key>}`` to the existing secret only when the secret holds another value.

Every other answer -- an unverified controller, a key pair it cannot generate, a command that fails, times out or
cannot be sent, output that is not ciphertext of this key, a plaintext that is not one key, a secret it
cannot read or write -- writes nothing and returns a distinct outcome code without raising, since the
function is invoked asynchronously. A sync runs at most once per interval across the deployment, by a
conditional write on one reserved row of the session table. Each run logs one outcome code and emits one
failure-count metric; neither the root key nor the run's private key appears in a log record, the metric,
the return value or an exception text. An operator may invoke the function directly
(``{"source": "manual"}``); it runs the same path.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import backend.backend.handlers.addon.edh.edhRootKeySync as sync
from backend.backend.handlers.addon.edh.common import edhRootKeySyncTrigger as trigger
from backend.tests.handlers.addon.edh.edhRootKeySyncTestSupport import (
    PUBLIC_KEY_ALLOWED_PATTERN, document_content, step_lines,
)

INSTANCE_ID = "i-0123456789abcdef0"
COMMAND_ID = "6f1f3c0a-1b2c-4d5e-8f90-0a1b2c3d4e5f"
# Sentinel keys: neither may appear in any log record, metric line, return value or exception text.
CURRENT_KEY = "Kq3ZtR8mWv2NpXc7LbYd5HsJf9GaUe4E"
NEW_KEY = "Pz7XwQ2rTn5VbMc8LkJh3GdFs6AyUe9R"
# The reserved row of the session table the rate limit is held on.
LOCK_KEY = "SETTINGS#rootKeySync"
# The clock the fakes start from, in seconds since the epoch.
START = 1_800_000_000.0
# The EDH tags CloudFormation and EDH put on the controller of the configured cluster (conftest: edh-test).
CONTROLLER_TAGS = [
    {"Key": "edh:NodeType", "Value": "controller"},
    {"Key": "aws:cloudformation:stack-name", "Value": "edh-test"},
    {"Key": "edh:ClusterId", "Value": "edh-test"},
    {"Key": "Name", "Value": "edh-test-Controller"},
]
OAEP_SHA_256 = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
REPO_ROOT = Path(__file__).resolve().parents[5]
DOCUMENT_SCRIPT = REPO_ROOT / "backendAddons" / "edh" / "rootKeySync" / "read-root-key.sh"
# The script runs on the EDH controller (Amazon Linux 2023): sh, GNU coreutils and the openssl CLI.
REQUIRES_CONTROLLER_TOOLS = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("openssl") is None or shutil.which("sh") is None,
    reason="the document's script needs Linux sh, GNU base64 and the openssl CLI, as on the EDH controller")
# The handler's own per-run key generation, before any test patches it.
GENERATE_PRIVATE_KEY = sync._generate_private_key


def _client_error(code, operation="Operation"):
    return ClientError({"Error": {"Code": code, "Message": f"{code} said so"}}, operation)


@pytest.fixture(scope="module")
def rsa_key():
    """The key the handler's per-run key generation hands out unless a test asks for real ones; generated once
    for the module, since a 3072-bit key takes up to a second."""
    return rsa.generate_private_key(public_exponent=65537, key_size=3072)


def _public_der(private_key):
    return private_key.public_key().public_bytes(serialization.Encoding.DER,
                                                 serialization.PublicFormat.SubjectPublicKeyInfo)


class FakeClock:
    """The handler's ``time``: advances only when the handler sleeps or a test says so."""

    def __init__(self, start=START):
        self.now = start
        self.slept = []

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class RecordingKey:
    """The run's private key as the handler uses it: its public half, its size and its decrypt, recorded."""

    def __init__(self, key, recorder):
        self._key = key
        self._recorder = recorder

    @property
    def key_size(self):
        return self._key.key_size

    def public_key(self):
        return self._key.public_key()

    def decrypt(self, ciphertext, pad):
        self._recorder.decrypts.append(pad)
        if self._recorder.decrypt_error is not None:
            raise self._recorder.decrypt_error
        return self._key.decrypt(ciphertext, pad)


class KeyRecorder:
    """The handler's per-run key generation: the module's fixed key (``fixed``), or a real key per run when
    ``fixed`` is None. Every key handed out is kept, so a test can assert it never leaks."""

    def __init__(self, fixed=None, *, error=None, decrypt_error=None):
        self.fixed = fixed
        self.error = error
        self.decrypt_error = decrypt_error
        self.handed_out = []
        self.decrypts = []

    def __call__(self):
        if self.error is not None:
            raise self.error
        key = self.fixed if self.fixed is not None else GENERATE_PRIVATE_KEY()
        self.handed_out.append(key)
        return RecordingKey(key, self)


class FakeEc2:
    def __init__(self, *, tags=None, state="running", error=None, instances=None):
        self.tags = CONTROLLER_TAGS if tags is None else tags
        self.state = state
        self.error = error
        self.instances = instances
        self.reads = []

    def describe_instances(self, **kwargs):
        self.reads.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.instances is not None:
            return {"Reservations": [{"Instances": self.instances}]}
        return {"Reservations": [{"Instances": [{
            "InstanceId": kwargs["InstanceIds"][0], "State": {"Name": self.state}, "Tags": self.tags}]}]}


class FakeSsm:
    """Parameter Store and Run Command. ``statuses`` are the successive invocation statuses (a status, or an
    exception to raise); the last one repeats. A successful invocation's output is the controller's key file
    (``key_file``) encrypted to the public key the command was sent, as the document produces it, unless
    ``output`` replaces it; with ``run_script`` the document's own script runs, under the local openssl."""

    def __init__(self, *, key_file=NEW_KEY, instance_id=INSTANCE_ID, parameter_error=None, send_error=None,
                 statuses=("Success",), output=None, run_script=False, tmp_path=None):
        self.key_file = key_file
        self.instance_id = instance_id
        self.parameter_error = parameter_error
        self.send_error = send_error
        self.statuses = list(statuses)
        self.output = output
        self.run_script = run_script
        self.tmp_path = tmp_path
        self.parameter_reads = []
        self.sent = []
        self.invocation_reads = []
        self.script_output = None
        # The deployed document as DescribeDocument and GetDocument report it; a test replaces any part.
        self.document_hash = "a" * 64
        self.described = {"Name": sync.EDH_ROOT_KEY_DOCUMENT_NAME, "DocumentVersion": "1", "Hash": self.document_hash,
                          "HashType": "Sha256", "Status": "Active", "CreatedDate": "2026-11-02T00:00:00Z"}
        self.fetched = {"Name": sync.EDH_ROOT_KEY_DOCUMENT_NAME, "DocumentVersion": "1", "Status": "Active",
                        "CreatedDate": "2026-11-02T00:00:00Z", "Content": document_content(),
                        "DocumentFormat": "JSON"}
        self.document_error = None
        self.document_reads = []

    def describe_document(self, **kwargs):
        self.document_reads.append(("describe", kwargs))
        if self.document_error is not None:
            raise self.document_error
        return {"Document": dict(self.described)}

    def get_document(self, **kwargs):
        self.document_reads.append(("get", kwargs))
        if self.document_error is not None:
            raise self.document_error
        return dict(self.fetched)

    def get_parameter(self, **kwargs):
        self.parameter_reads.append(kwargs)
        if self.parameter_error is not None:
            raise self.parameter_error
        return {"Parameter": {"Name": kwargs["Name"], "Type": "String", "Value": self.instance_id}}

    def send_command(self, **kwargs):
        self.sent.append(kwargs)
        if self.send_error is not None:
            raise self.send_error
        public_key = base64.b64decode(kwargs["Parameters"]["publicKey"][0], validate=True)
        if self.run_script:
            self.script_output = run_document_script(public_key, self.key_file, self.tmp_path)
        else:
            loaded = serialization.load_der_public_key(public_key)
            self.script_output = base64.b64encode(loaded.encrypt(self.key_file.encode(), OAEP_SHA_256)).decode()
        return {"Command": {"CommandId": COMMAND_ID, "Status": "Pending"}}

    def get_command_invocation(self, **kwargs):
        self.invocation_reads.append(kwargs)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if isinstance(status, Exception):
            raise status
        output = self.output if self.output is not None else self.script_output
        return {"CommandId": kwargs["CommandId"], "InstanceId": kwargs["InstanceId"], "Status": status,
                "StandardOutputContent": output if status == "Success" else "", "ResponseCode": 0}


class FakeSecretsManager:
    def __init__(self, *, current=CURRENT_KEY, read_error=None, write_error=None, raw=None):
        self.secret_string = raw if raw is not None else json.dumps({"rootKey": current})
        self.read_error = read_error
        self.write_error = write_error
        self.reads = []
        self.writes = []

    def get_secret_value(self, **kwargs):
        self.reads.append(kwargs)
        if self.read_error is not None:
            raise self.read_error
        return {"SecretString": self.secret_string}

    def put_secret_value(self, **kwargs):
        self.writes.append(kwargs)
        if self.write_error is not None:
            raise self.write_error
        self.secret_string = kwargs["SecretString"]
        return {"VersionId": "v2"}


class FakeLockTable:
    """The session table as the rate limit sees it: one conditional UpdateItem on the reserved row."""

    def __init__(self, *, held_at=None, error=None):
        self.held_at = held_at
        self.error = error
        self.updates = []

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        if self.error is not None:
            raise self.error
        values = kwargs.get("ExpressionAttributeValues", {})
        if ":cutoff" not in values:
            # The bookkeeping write after a run.
            return {}
        if self.held_at is not None and values[":cutoff"] <= self.held_at <= values[":ahead"]:
            raise _client_error("ConditionalCheckFailedException", "UpdateItem")
        self.held_at = values[":now"]
        return {"Attributes": {"bootstrapId": LOCK_KEY, "syncStartedAt": Decimal(values[":now"])}}


class RecordingLogger:
    def __init__(self):
        self.records = []

    def _record(self, message, *args, **kwargs):
        self.records.append([str(message), [str(a) for a in args], kwargs.get("extra")])

    debug = info = warning = error = exception = critical = _record


class Fakes:
    def __init__(self, **parts):
        self.__dict__.update(parts)


class FakeContext:
    """The Lambda context the sync reads: the time left before its timeout, on the fake clock, and the
    request id."""

    def __init__(self, clock, deadline_at, request_id="11111111-2222-3333-4444-555555555555"):
        self.clock = clock
        self.deadline_at = deadline_at
        self.aws_request_id = request_id

    def get_remaining_time_in_millis(self):
        return int((self.deadline_at - self.clock.now) * 1000)


def install(monkeypatch, rsa_key, *, ssm=None, secrets=None, table=None, keys=None, ec2=None, clock=None):
    fakes = Fakes(ssm=ssm or FakeSsm(), secrets=secrets or FakeSecretsManager(), table=table or FakeLockTable(),
                  keys=keys or KeyRecorder(rsa_key), ec2=ec2 or FakeEc2(), clock=clock or FakeClock(),
                  log=RecordingLogger(), metrics=[])
    monkeypatch.setattr(sync, "_ssm_client", lambda: fakes.ssm)
    monkeypatch.setattr(sync, "_secretsmanager_client", lambda: fakes.secrets)
    monkeypatch.setattr(sync, "_lock_table", lambda: fakes.table)
    monkeypatch.setattr(sync, "_generate_private_key", fakes.keys)
    monkeypatch.setattr(sync, "_ec2_client", lambda: fakes.ec2)
    monkeypatch.setattr(sync, "time", fakes.clock)
    monkeypatch.setattr(sync, "logger", fakes.log)
    monkeypatch.setattr(sync, "_write_metric_line", fakes.metrics.append)
    return fakes


def run(fakes, event=None, context=None):
    return sync.lambda_handler({"trigger": trigger.ROOT_KEY_SYNC_TRIGGER} if event is None else event, context)


def run_document_script(public_key_der, key_file_content, tmp_path):
    """The document's own step, as Run Command runs it: its lines with the key file path and the publicKey
    parameter substituted, under ``sh``. Returns its standard output; the private directory it uses must be
    gone afterwards."""
    key_file = tmp_path / "admin_api_key.txt"
    key_file.write_text(key_file_content)
    workdir = tmp_path / "tmp"
    workdir.mkdir(exist_ok=True)
    script = DOCUMENT_SCRIPT.read_text().replace("__EDH_ROOT_KEY_FILE__", str(key_file)).replace(
        "{{ publicKey }}", base64.b64encode(public_key_der).decode())
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=60,
                            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TMPDIR": str(workdir)})
    assert result.returncode == 0, result.stderr
    assert list(workdir.iterdir()) == [], "the script left its private directory behind"
    return result.stdout


def update_effect(kwargs):
    """The attribute names one UpdateItem call sets and the ones it removes, whatever the expression's spelling."""
    names = kwargs.get("ExpressionAttributeNames", {})
    sets, _, removes = kwargs["UpdateExpression"].partition(" REMOVE ")
    set_names = {names.get(term.split(" = ")[0], term.split(" = ")[0])
                 for term in filter(None, sets.removeprefix("SET ").split(", "))}
    removed = {names.get(term, term) for term in filter(None, removes.split(", "))}
    return set_names, removed


def assert_no_key_leaked(fakes, result):
    """Neither sentinel key reaches a log record, a metric line or the return value."""
    logged = json.dumps(fakes.log.records) + "".join(fakes.metrics)
    returned = json.dumps(result)
    for value in (CURRENT_KEY, NEW_KEY):
        assert value not in logged
        assert value not in returned


@pytest.mark.unit
class TestTheKeyIsCopied:
    def test_a1_a_new_key_is_written_once_as_the_documented_json(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key)

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_UPDATED}
        assert fakes.secrets.writes == [{
            "SecretId": sync.EDH_ADMIN_KEY_SECRET_ARN,
            "SecretString": json.dumps({"rootKey": NEW_KEY}),
        }]
        assert fakes.secrets.reads == [{"SecretId": sync.EDH_ADMIN_KEY_SECRET_ARN}]
        assert_no_key_leaked(fakes, result)

    def test_the_command_is_the_pinned_document_on_the_named_controller_with_only_the_public_key(
            self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key)

        run(fakes)

        assert fakes.ssm.parameter_reads == [
            {"Name": f"/edh/{sync.EDH_CLUSTER_ID}/configuration/ControllerInstanceId"}]
        assert len(fakes.ssm.sent) == 1
        sent = fakes.ssm.sent[0]
        assert sent["DocumentName"] == sync.EDH_ROOT_KEY_DOCUMENT_NAME
        # The deployed version, never $DEFAULT or $LATEST, so a version added to the document later is not run.
        assert sent["DocumentVersion"] == sync.EDH_ROOT_KEY_DOCUMENT_VERSION == "1"
        assert sent["InstanceIds"] == [INSTANCE_ID]
        assert sent["Parameters"] == {"publicKey": [base64.b64encode(_public_der(rsa_key)).decode()]}
        # The output stays with the invocation: no S3 bucket and no CloudWatch Logs group receives it.
        assert not {"OutputS3BucketName", "OutputS3KeyPrefix", "CloudWatchOutputConfig",
                    "NotificationConfig", "Targets"} & set(sent)
        assert all(read == {"CommandId": COMMAND_ID, "InstanceId": INSTANCE_ID}
                   for read in fakes.ssm.invocation_reads)

    def test_the_ciphertext_is_decrypted_once_by_the_runs_own_key_with_oaep_sha_256(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key)

        run(fakes)

        assert fakes.keys.handed_out == [rsa_key]
        assert len(fakes.keys.decrypts) == 1
        pad = fakes.keys.decrypts[0]
        assert isinstance(pad, padding.OAEP)
        assert pad is sync.OAEP_SHA_256
        # Run Command held only ciphertext of the key.
        assert NEW_KEY not in fakes.ssm.script_output

    def test_an_invocation_not_yet_registered_is_read_again(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key, ssm=FakeSsm(statuses=[
            _client_error("InvocationDoesNotExist", "GetCommandInvocation"), "InProgress", "Success"]))

        assert run(fakes) == {"outcome": sync.OUTCOME_UPDATED}
        assert len(fakes.ssm.invocation_reads) == 3
        assert len(fakes.secrets.writes) == 1

    def test_a_secret_whose_value_is_not_the_documented_json_is_repaired(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key, secrets=FakeSecretsManager(raw="not json"))

        assert run(fakes) == {"outcome": sync.OUTCOME_UPDATED}
        assert json.loads(fakes.secrets.writes[0]["SecretString"]) == {"rootKey": NEW_KEY}

    def test_an_operator_may_run_the_same_sync_directly(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key)

        result = run(fakes, {"source": "manual"})

        assert result == {"outcome": sync.OUTCOME_UPDATED}
        assert len(fakes.ssm.sent) == 1 and len(fakes.secrets.writes) == 1
        assert any("manual" in json.dumps(record) for record in fakes.log.records)

    def test_any_other_event_runs_the_same_path_and_logs_none_of_it(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key)

        result = run(fakes, {"source": "<script>", "detail": NEW_KEY})

        assert result == {"outcome": sync.OUTCOME_UPDATED}
        assert "<script>" not in json.dumps(fakes.log.records)
        assert_no_key_leaked(fakes, result)


@pytest.mark.unit
class TestNothingIsWritten:
    def test_a2_the_key_the_secret_already_holds_is_not_written_again(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key, secrets=FakeSecretsManager(current=NEW_KEY))

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_UNCHANGED}
        assert fakes.secrets.writes == []
        assert_no_key_leaked(fakes, result)

    @pytest.mark.parametrize("key_file", [
        "",
        "   \n",
        NEW_KEY[:31],
        NEW_KEY + "A",
        NEW_KEY[:31] + "-",
        f"warning: something\n{NEW_KEY}",
        f"{NEW_KEY}\n{NEW_KEY}",
        "x" * 100,
        "\u00e9" * 16,
    ], ids=["empty", "blank", "short", "long", "symbol", "extra line first", "two keys", "large", "not ascii"])
    def test_a3_a_key_file_that_is_not_one_key_writes_nothing(self, monkeypatch, rsa_key, key_file):
        fakes = install(monkeypatch, rsa_key, ssm=FakeSsm(key_file=key_file))

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_INVALID}
        assert fakes.secrets.writes == []
        assert fakes.secrets.reads == []
        assert_no_key_leaked(fakes, result)
        if key_file.strip():
            assert key_file.strip() not in json.dumps(fakes.log.records)

    @pytest.mark.parametrize("output", [
        "",
        "not base64 at all!",
        base64.b64encode(b"short").decode(),
        NEW_KEY,
    ], ids=["empty", "not base64", "not ciphertext", "plaintext key"])
    def test_a3_output_that_is_not_ciphertext_of_the_key_writes_nothing(self, monkeypatch, rsa_key, output):
        fakes = install(monkeypatch, rsa_key, ssm=FakeSsm(output=output))

        result = run(fakes)

        assert result["outcome"] in (sync.OUTCOME_INVALID, sync.OUTCOME_DECRYPT_FAILED)
        assert fakes.secrets.writes == []
        assert_no_key_leaked(fakes, result)

    def test_a4_ciphertext_the_key_cannot_decrypt_writes_nothing(self, monkeypatch, rsa_key):
        other = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        forged = base64.b64encode(other.public_key().encrypt(NEW_KEY.encode(), OAEP_SHA_256)).decode()
        fakes = install(monkeypatch, rsa_key, ssm=FakeSsm(output=forged))

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_DECRYPT_FAILED}
        assert fakes.secrets.writes == []
        assert_no_key_leaked(fakes, result)

    @pytest.mark.parametrize("status", ["Failed", "Cancelled", "TimedOut"])
    def test_a4_a_command_that_does_not_succeed_writes_nothing(self, monkeypatch, rsa_key, status):
        fakes = install(monkeypatch, rsa_key, ssm=FakeSsm(statuses=[status]))

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_COMMAND_FAILED}
        assert fakes.secrets.writes == [] and fakes.keys.decrypts == []
        assert_no_key_leaked(fakes, result)

    def test_a4_a_command_that_cannot_be_sent_writes_nothing(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key, ssm=FakeSsm(send_error=_client_error("InvalidInstanceId", "SendCommand")))

        assert run(fakes) == {"outcome": sync.OUTCOME_COMMAND_FAILED}
        assert fakes.ssm.invocation_reads == []
        assert fakes.secrets.writes == []

    def test_a4_an_invocation_that_cannot_be_read_writes_nothing(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key, ssm=FakeSsm(statuses=[
            _client_error("AccessDeniedException", "GetCommandInvocation")]))

        assert run(fakes) == {"outcome": sync.OUTCOME_COMMAND_FAILED}
        assert fakes.secrets.writes == []

    def test_a4_a_command_still_running_at_the_wait_bound_times_out_and_writes_nothing(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key, ssm=FakeSsm(statuses=["InProgress"]))

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_COMMAND_TIMED_OUT}
        assert fakes.secrets.writes == []
        # The wait is bounded by its budget, in pauses of the poll interval.
        assert sum(fakes.clock.slept) <= sync.COMMAND_WAIT_SECONDS + sync.COMMAND_POLL_SECONDS
        assert len(fakes.ssm.invocation_reads) <= sync.COMMAND_WAIT_SECONDS // sync.COMMAND_POLL_SECONDS + 2

    @pytest.mark.parametrize("ssm", [
        FakeSsm(parameter_error=_client_error("ParameterNotFound", "GetParameter")),
        FakeSsm(instance_id="not-an-instance"),
        FakeSsm(instance_id=""),
    ], ids=["no parameter", "malformed id", "empty id"])
    def test_a4_a_controller_the_cluster_does_not_name_is_never_sent_a_command(self, monkeypatch, rsa_key, ssm):
        fakes = install(monkeypatch, rsa_key, ssm=ssm)

        assert run(fakes) == {"outcome": sync.OUTCOME_CONTROLLER_NOT_FOUND}
        assert fakes.ssm.sent == []
        assert fakes.secrets.writes == []

    def test_a4_a_secret_that_cannot_be_read_is_not_written(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key, secrets=FakeSecretsManager(
            read_error=_client_error("ResourceNotFoundException", "GetSecretValue")))

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_SECRET_READ_FAILED}
        assert fakes.secrets.writes == []
        assert_no_key_leaked(fakes, result)

    def test_a4_a_write_the_secret_refuses_is_reported(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key, secrets=FakeSecretsManager(
            write_error=_client_error("AccessDeniedException", "PutSecretValue")))

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_SECRET_WRITE_FAILED}
        assert len(fakes.secrets.writes) == 1
        assert_no_key_leaked(fakes, result)

    def test_a4_a5_an_unexpected_failure_is_an_outcome_and_its_text_never_carries_the_key(
            self, monkeypatch, rsa_key):
        class ExplodingSecrets(FakeSecretsManager):
            def put_secret_value(self, **kwargs):
                raise RuntimeError(f"boom {kwargs['SecretString']}")

        fakes = install(monkeypatch, rsa_key, secrets=ExplodingSecrets())

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_ERROR}
        assert_no_key_leaked(fakes, result)

    def test_a4_the_outcome_codes_are_distinct(self):
        codes = [value for name, value in vars(sync).items() if name.startswith("OUTCOME_")]
        assert len(codes) == len(set(codes)) >= 13


@pytest.mark.unit
class TestTheControllerIsVerified:
    @pytest.mark.parametrize("ec2", [
        FakeEc2(tags=[t for t in CONTROLLER_TAGS if t["Key"] != "edh:NodeType"]),
        FakeEc2(tags=[{**t, "Value": "dcv_node"} if t["Key"] == "edh:NodeType" else t for t in CONTROLLER_TAGS]),
        FakeEc2(tags=[{**t, "Value": "edh-other"} if t["Key"] == "aws:cloudformation:stack-name" else t
                      for t in CONTROLLER_TAGS]),
        FakeEc2(tags=[t for t in CONTROLLER_TAGS if t["Key"] != "aws:cloudformation:stack-name"]),
        FakeEc2(state="stopped"),
        FakeEc2(instances=[]),
        FakeEc2(instances=[{"InstanceId": "i-0fffffffffffffff0", "State": {"Name": "running"},
                            "Tags": CONTROLLER_TAGS}]),
        FakeEc2(error=_client_error("InvalidInstanceID.NotFound", "DescribeInstances")),
    ], ids=["no node type tag", "desktop node type", "another stack", "no stack tag", "not running",
            "not found", "another instance answered", "read refused"])
    def test_an_instance_that_is_not_the_running_tagged_controller_is_never_sent_a_command(
            self, monkeypatch, rsa_key, ec2):
        fakes = install(monkeypatch, rsa_key, ec2=ec2)

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_CONTROLLER_NOT_VERIFIED}
        assert fakes.ssm.sent == []
        assert fakes.keys.handed_out == []
        assert fakes.secrets.writes == []

    def test_the_instance_read_is_the_one_edh_publishes(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key)

        run(fakes)

        assert fakes.ec2.reads == [{"InstanceIds": [INSTANCE_ID]}]


@pytest.mark.unit
class TestTheRunFinishesInsideItsTimeout:
    """Every run logs an outcome before the function's timeout: the clients are bounded, and no call starts
    with less than RUN_RESERVE_SECONDS left. A run killed anyway shows in the function's Lambda Errors metric."""

    def test_s2_the_clients_are_bounded(self):
        config = sync.sync_client_config
        attempts = config.retries.get("total_max_attempts")
        assert config.connect_timeout <= 3 and config.read_timeout <= 10
        assert attempts is not None and attempts <= 3
        assert config.retries.get("mode") == "standard"
        assert (config.connect_timeout + config.read_timeout) * attempts <= sync.RUN_RESERVE_SECONDS

    def test_s2_the_wait_and_the_reserve_fit_the_function_timeout(self):
        builder = (REPO_ROOT / "infra" / "lib" / "nestedStacks" / "addon" / "edhWorkstations" / "lambdaBuilder"
                   / "edhWorkstationsFunctions.ts").read_text()
        match = re.search(r"EDH_ROOT_KEY_SYNC_TIMEOUT = Duration\.minutes\((\d+)\)", builder)
        assert match, "the builder declares the sync's timeout in minutes"
        timeout = int(match.group(1)) * 60
        assert sync.DEFAULT_RUN_SECONDS == timeout
        assert sync.COMMAND_WAIT_SECONDS + sync.RUN_RESERVE_SECONDS < timeout

    def test_s2_a_run_with_too_little_time_left_calls_nothing_and_logs_a_failure(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key)
        context = FakeContext(fakes.clock, START + sync.RUN_RESERVE_SECONDS - 1)

        result = run(fakes, context=context)

        assert result == {"outcome": sync.OUTCOME_OUT_OF_TIME}
        assert fakes.table.updates == [] and fakes.ssm.sent == [] and fakes.secrets.writes == []
        assert json.loads(fakes.metrics[0])[sync.METRIC_FAILURES] == 1
        assert any(r[0] == "EDH root key sync finished" for r in fakes.log.records)

    def test_s2_a_slow_send_ends_the_wait_early_enough_to_log_the_outcome(self, monkeypatch, rsa_key):
        clock = FakeClock()

        class SlowSsm(FakeSsm):
            def send_command(self, **kwargs):
                clock.now += 70
                return super().send_command(**kwargs)

        fakes = install(monkeypatch, rsa_key, ssm=SlowSsm(statuses=["InProgress"]), clock=clock)
        deadline_at = START + sync.DEFAULT_RUN_SECONDS
        context = FakeContext(clock, deadline_at)

        result = run(fakes, context=context)

        assert result["outcome"] in (sync.OUTCOME_COMMAND_TIMED_OUT, sync.OUTCOME_OUT_OF_TIME)
        assert deadline_at - clock.now >= sync.RUN_RESERVE_SECONDS - sync.COMMAND_POLL_SECONDS
        assert fakes.secrets.writes == []
        assert len(fakes.metrics) == 1

    def test_s2_a_late_step_is_not_started_without_the_reserve(self, monkeypatch, rsa_key):
        clock = FakeClock()

        class SlowSsm(FakeSsm):
            def get_command_invocation(self, **kwargs):
                clock.now += 95
                return super().get_command_invocation(**kwargs)

        fakes = install(monkeypatch, rsa_key, ssm=SlowSsm(), clock=clock)

        result = run(fakes, context=FakeContext(clock, START + sync.DEFAULT_RUN_SECONDS))

        assert result == {"outcome": sync.OUTCOME_OUT_OF_TIME}
        assert fakes.keys.decrypts == [] and fakes.secrets.writes == []


def test_n2_a_run_out_of_time_between_the_document_reads_ends_out_of_time(monkeypatch, rsa_key):
    clock = FakeClock()

    class SlowSsm(FakeSsm):
        def describe_document(self, **kwargs):
            clock.now += 60
            return super().describe_document(**kwargs)

    fakes = install(monkeypatch, rsa_key, ssm=SlowSsm(), clock=clock)
    deadline_at = START + sync.RUN_RESERVE_SECONDS + 50

    result = run(fakes, context=FakeContext(clock, deadline_at))

    assert result == {"outcome": sync.OUTCOME_OUT_OF_TIME}
    assert [kind for kind, _ in fakes.ssm.document_reads] == ["describe"]
    assert fakes.ssm.sent == [] and fakes.secrets.writes == []
    finished = [r for r in fakes.log.records if r[0] == "EDH root key sync finished"]
    assert len(finished) == 1 and finished[0][2]["failure"] is True


def _tampered(change):
    ssm = FakeSsm()
    change(ssm)
    return ssm


@pytest.mark.unit
class TestTheDocumentIsVerified:
    """The sync runs only the document the deployment created: its version's content is the expected step, and
    SendCommand names that version's hash, so Systems Manager refuses any other content."""

    def test_s4_the_command_names_the_deployed_version_and_its_hash(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key)

        assert run(fakes) == {"outcome": sync.OUTCOME_UPDATED}

        sent = fakes.ssm.sent[0]
        assert (sent["DocumentVersion"], sent["DocumentHash"], sent["DocumentHashType"]) == (
            "1", fakes.ssm.document_hash, "Sha256")
        assert fakes.ssm.document_reads == [
            ("describe", {"Name": sync.EDH_ROOT_KEY_DOCUMENT_NAME, "DocumentVersion": "1"}),
            ("get", {"Name": sync.EDH_ROOT_KEY_DOCUMENT_NAME, "DocumentVersion": "1", "DocumentFormat": "JSON"}),
        ]

    def test_s4_the_expected_step_is_the_repository_script_for_the_cluster(self):
        assert sync.PUBLIC_KEY_ALLOWED_PATTERN == PUBLIC_KEY_ALLOWED_PATTERN
        lines = step_lines()
        assert any("openssl pkeyutl -encrypt -pubin" in line for line in lines)
        assert sync.EDH_ROOT_KEY_DOCUMENT_STEP_SHA256 == __import__("hashlib").sha256(
            "\n".join(lines).encode()).hexdigest()

    @pytest.mark.parametrize("ssm", [
        _tampered(lambda s: s.fetched.update(Content=document_content(step_lines() + ["cat /etc/shadow"]))),
        _tampered(lambda s: s.fetched.update(Content=document_content(["cat /opt/edh/x/keys/admin_api_key.txt"]))),
        _tampered(lambda s: s.fetched.update(Content=document_content(extra_steps=[
            {"action": "aws:runShellScript", "name": "more", "inputs": {"runCommand": ["id"]}}]))),
        _tampered(lambda s: s.fetched.update(Content=document_content(parameters={
            "publicKey": {"type": "String", "allowedPattern": PUBLIC_KEY_ALLOWED_PATTERN},
            "commands": {"type": "StringList"}}))),
        _tampered(lambda s: s.fetched.update(Content=document_content(parameters={
            "publicKey": {"type": "String", "allowedPattern": ".*"}}))),
        _tampered(lambda s: s.fetched.update(Content=document_content(parameters={
            "publicKey": {"type": "String", "allowedPattern": PUBLIC_KEY_ALLOWED_PATTERN, "default": "AAAA"}}))),
        _tampered(lambda s: s.fetched.update(Content="not json")),
        _tampered(lambda s: s.described.update(HashType="Sha1")),
        _tampered(lambda s: s.described.update(Hash="")),
        _tampered(lambda s: s.described.update(Status="Updating")),
        _tampered(lambda s: s.fetched.update(CreatedDate="2026-11-03T00:00:00Z")),
        _tampered(lambda s: s.fetched.update(DocumentVersion="2")),
        _tampered(lambda s: setattr(s, "document_error", _client_error("InvalidDocument", "DescribeDocument"))),
    ], ids=["extra line", "other step", "second step", "extra parameter", "open pattern", "default key",
            "not json", "sha1", "no hash", "not active", "recreated", "other version", "read refused"])
    def test_s4_a_document_that_is_not_the_deployed_one_is_never_sent(self, monkeypatch, rsa_key, ssm):
        fakes = install(monkeypatch, rsa_key, ssm=ssm)

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_DOCUMENT_NOT_VERIFIED}
        assert fakes.ssm.sent == [] and fakes.secrets.writes == []
        assert json.loads(fakes.metrics[0])[sync.METRIC_FAILURES] == 1


class OversizedKey:
    """A key whose public half is longer than the document's parameter admits."""

    key_size = 3072

    def public_key(self):
        return self

    def public_bytes(self, *_args):
        return b"\x00" * 900


def _secret_forms(key):
    """Every text form of a private key a leak could take: PEM, base64 DER and the private exponent."""
    der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    d = key.private_numbers().d
    return [pem, pem.splitlines()[1], base64.b64encode(der).decode(), str(d), format(d, "x")]


def assert_no_private_key_leaked(fakes, result):
    logged = json.dumps(fakes.log.records) + "".join(fakes.metrics) + json.dumps(result)
    assert fakes.keys.handed_out
    for key in fakes.keys.handed_out:
        for form in _secret_forms(key):
            assert form not in logged


@pytest.mark.unit
class TestTheRunsOwnKey:
    """Each run generates its own RSA key pair, in memory, sends the public half and decrypts with the private half;
    nothing outside the run ever holds the private key (review S-10)."""

    def test_s10_each_run_generates_a_fresh_3072_bit_key_and_sends_its_public_half(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key, keys=KeyRecorder())

        assert run(fakes) == {"outcome": sync.OUTCOME_UPDATED}
        fakes.clock.now += sync.ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS + 1
        assert run(fakes, {"source": "manual"})["outcome"] == sync.OUTCOME_UNCHANGED

        first, second = fakes.keys.handed_out
        for key in (first, second):
            assert key.key_size == 3072 and key.public_key().public_numbers().e == 65537
        assert first.private_numbers().d != second.private_numbers().d
        sent = [s["Parameters"]["publicKey"][0] for s in fakes.ssm.sent]
        assert sent == [base64.b64encode(_public_der(k)).decode() for k in (first, second)]
        assert [len(value) for value in sent] == [564, 564]
        assert all(re.fullmatch(PUBLIC_KEY_ALLOWED_PATTERN, value) for value in sent)

    def test_s10_the_private_key_never_reaches_a_log_the_metric_or_the_answer(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key, keys=KeyRecorder())

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_UPDATED}
        assert_no_private_key_leaked(fakes, result)
        assert_no_key_leaked(fakes, result)

    def test_s10_no_module_global_keeps_the_private_key(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key, keys=KeyRecorder())

        run(fakes)

        held = [name for name, value in vars(sync).items()
                if isinstance(value, (rsa.RSAPrivateKey, RecordingKey))]
        assert held == []

    def test_s10_an_unexpected_error_carrying_the_key_is_logged_by_type_only(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key, keys=KeyRecorder())

        def leaking(_key):
            pem = fakes.keys.handed_out[0].private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            raise RuntimeError(pem.decode())

        monkeypatch.setattr(sync, "_write_key", leaking)

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_ERROR}
        assert any((r[2] or {}).get("error") == "RuntimeError" for r in fakes.log.records)
        assert_no_private_key_leaked(fakes, result)

    @pytest.mark.parametrize("keys", [
        KeyRecorder(error=ValueError("generation failed")),
        KeyRecorder(error=MemoryError()),
        KeyRecorder(OversizedKey()),
    ], ids=["generation refused", "out of memory", "public half too long"])
    def test_s10_a_key_pair_that_cannot_be_made_sends_nothing(self, monkeypatch, rsa_key, keys):
        fakes = install(monkeypatch, rsa_key, keys=keys)

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_KEY_UNAVAILABLE}
        assert fakes.ssm.sent == [] and fakes.secrets.reads == [] and fakes.secrets.writes == []
        assert json.loads(fakes.metrics[0])[sync.METRIC_FAILURES] == 1
        assert "generation failed" not in json.dumps(fakes.log.records)

    def test_s10_a_decrypt_that_fails_writes_nothing_and_names_the_error_by_type(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key, keys=KeyRecorder(rsa_key, decrypt_error=ValueError("bad " + NEW_KEY)))

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_DECRYPT_FAILED}
        assert fakes.secrets.writes == []
        assert any((r[2] or {}).get("error") == "ValueError" for r in fakes.log.records)
        assert_no_key_leaked(fakes, result)

    def test_s10_ciphertext_that_is_not_one_rsa_block_is_invalid(self, monkeypatch, rsa_key):
        block = rsa_key.public_key().encrypt(NEW_KEY.encode(), OAEP_SHA_256)
        fakes = install(monkeypatch, rsa_key, ssm=FakeSsm(output=base64.b64encode(block + b"\x00").decode()))

        assert run(fakes) == {"outcome": sync.OUTCOME_INVALID}
        assert fakes.keys.decrypts == [] and fakes.secrets.writes == []

    def test_s10_no_key_is_made_before_the_controller_and_the_document_are_verified(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key, ssm=_tampered(lambda s: s.described.update(HashType="Sha1")))

        assert run(fakes) == {"outcome": sync.OUTCOME_DOCUMENT_NOT_VERIFIED}
        assert fakes.keys.handed_out == []


@pytest.mark.unit
class TestRateLimit:
    def test_a6_a_sync_inside_the_interval_of_the_last_one_sends_no_command(self, monkeypatch, rsa_key):
        recent = Decimal(int(START) - sync.ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS + 1)
        fakes = install(monkeypatch, rsa_key, table=FakeLockTable(held_at=recent))

        assert run(fakes) == {"outcome": sync.OUTCOME_RATE_LIMITED}
        assert fakes.ssm.parameter_reads == []
        assert fakes.ssm.sent == []
        assert fakes.secrets.reads == [] and fakes.secrets.writes == []

    def test_a6_a_second_trigger_inside_the_window_is_refused_and_one_after_it_runs(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key)

        assert run(fakes) == {"outcome": sync.OUTCOME_UPDATED}
        fakes.clock.now += sync.ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS - 1
        assert run(fakes) == {"outcome": sync.OUTCOME_RATE_LIMITED}
        assert len(fakes.ssm.sent) == 1
        fakes.clock.now += 2
        assert run(fakes) == {"outcome": sync.OUTCOME_UNCHANGED}
        assert len(fakes.ssm.sent) == 2

    def test_a6_the_rate_limit_is_one_conditional_write_on_the_reserved_row(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key)

        run(fakes)

        # The admission, then (N-1) the written key's stamp on the same row, unconditional.
        assert len(fakes.table.updates) == 2
        stamp = fakes.table.updates[1]
        assert stamp["Key"] == {"bootstrapId": LOCK_KEY} and "ConditionExpression" not in stamp
        stamp_sets, stamp_removes = update_effect(stamp)
        assert stamp_sets == {"updatedAt"}
        assert stamp_removes == {"unchangedStreak", "retryAfter"}
        update = fakes.table.updates[0]
        assert update["Key"] == {"bootstrapId": LOCK_KEY}
        assert "ConditionExpression" in update
        values = update["ExpressionAttributeValues"]
        assert values[":now"] == int(START)
        assert values[":cutoff"] == int(START) - sync.ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS
        # The row carries no state, userId or sessionId, so no session read or index ever yields it.
        admission_sets, admission_removes = update_effect(update)
        assert admission_sets == {"syncStartedAt"}
        assert not admission_removes
        assert sync.ROOT_KEY_SYNC_SETTINGS_KEY == LOCK_KEY

    def test_a6_a_rate_limit_that_cannot_be_written_runs_nothing(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key,
                        table=FakeLockTable(error=_client_error("ProvisionedThroughputExceededException")))

        assert run(fakes) == {"outcome": sync.OUTCOME_ERROR}
        assert fakes.ssm.sent == []

    def test_a6_the_interval_is_the_one_the_trigger_debounces_with(self):
        assert sync.ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS == trigger.ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS


def _lock_row(table):
    return table.get_item(Key={"bootstrapId": LOCK_KEY}, ConsistentRead=True).get("Item") or {}


@pytest.mark.unit
class TestFirstTimeSetupFromAPlaceholder:
    """With the sync on, the secret is created as {"rootKey": "placeholder"} and the first sync writes the key, so
    no plaintext read of the key file lands in the Run Command history (review S-3)."""

    @pytest.mark.parametrize("event", [None, {"source": "manual"}], ids=["first rejected call", "manual run"])
    def test_s3_a_placeholder_secret_is_replaced_by_the_first_sync(self, monkeypatch, rsa_key, event):
        fakes = install(monkeypatch, rsa_key, secrets=FakeSecretsManager(current="placeholder"))

        result = run(fakes, event)

        assert result == {"outcome": sync.OUTCOME_UPDATED}
        assert fakes.secrets.writes == [{"SecretId": sync.EDH_ADMIN_KEY_SECRET_ARN,
                                         "SecretString": json.dumps({"rootKey": NEW_KEY})}]
        assert json.loads(fakes.metrics[-1])[sync.METRIC_FAILURES] == 0
        assert_no_key_leaked(fakes, result)


@pytest.mark.unit
class TestBackoffAndClockSkew:
    """Against a DynamoDB table (moto), so the condition expressions are evaluated as DynamoDB evaluates them.

    EDH writes the key file before its web interface starts, so a key EDH rejected that equals the file is a
    misconfiguration, not a web interface still starting: such a run counts as a failure and the automatic
    trigger backs off, doubling from twice the interval up to MAX_BACKOFF_SECONDS. An administrator's manual run
    is a success when unchanged and is held only by the interval. A stamp further ahead than one interval, or a
    backoff further ahead than its ceiling, can only be forged or skewed and is ignored."""

    def test_s6_unchanged_after_a_rejection_is_a_failure_and_backs_off(self, monkeypatch, rsa_key, edh_session_table):
        fakes = install(monkeypatch, rsa_key, secrets=FakeSecretsManager(current=NEW_KEY), table=edh_session_table)
        interval = sync.ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS

        assert run(fakes) == {"outcome": sync.OUTCOME_UNCHANGED}
        assert json.loads(fakes.metrics[-1])[sync.METRIC_FAILURES] == 1
        row = _lock_row(edh_session_table)
        assert (int(row["unchangedStreak"]), int(row["retryAfter"])) == (1, int(START) + 2 * interval)

        fakes.clock.now += interval + 1
        assert run(fakes) == {"outcome": sync.OUTCOME_BACKED_OFF}
        assert json.loads(fakes.metrics[-1])[sync.METRIC_FAILURES] == 1
        assert len(fakes.ssm.sent) == 1

        fakes.clock.now = START + 2 * interval
        assert run(fakes) == {"outcome": sync.OUTCOME_UNCHANGED}
        row = _lock_row(edh_session_table)
        assert (int(row["unchangedStreak"]), int(row["retryAfter"])) == (2, int(fakes.clock.now) + 4 * interval)
        assert_no_key_leaked(fakes, {})

    def test_s6_the_backoff_is_capped(self, monkeypatch, rsa_key, edh_session_table):
        edh_session_table.put_item(Item={"bootstrapId": LOCK_KEY, "syncStartedAt": Decimal(int(START) - 99999),
                                         "unchangedStreak": Decimal(40), "retryAfter": Decimal(int(START) - 1)})
        fakes = install(monkeypatch, rsa_key, secrets=FakeSecretsManager(current=NEW_KEY), table=edh_session_table)

        assert run(fakes) == {"outcome": sync.OUTCOME_UNCHANGED}
        row = _lock_row(edh_session_table)
        assert int(row["retryAfter"]) == int(START) + sync.MAX_BACKOFF_SECONDS

    def test_s6_a_manual_unchanged_run_is_a_success_and_is_not_held_by_the_backoff(
            self, monkeypatch, rsa_key, edh_session_table):
        edh_session_table.put_item(Item={"bootstrapId": LOCK_KEY, "syncStartedAt": Decimal(int(START) - 400),
                                         "unchangedStreak": Decimal(3), "retryAfter": Decimal(int(START) + 3000)})
        fakes = install(monkeypatch, rsa_key, secrets=FakeSecretsManager(current=NEW_KEY), table=edh_session_table)

        assert run(fakes, {"source": "manual"}) == {"outcome": sync.OUTCOME_UNCHANGED}
        assert json.loads(fakes.metrics[-1])[sync.METRIC_FAILURES] == 0
        row = _lock_row(edh_session_table)
        assert (int(row["unchangedStreak"]), int(row["retryAfter"])) == (3, int(START) + 3000)
        # The automatic trigger is still held by the backoff.
        fakes.clock.now += sync.ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS + 1
        assert run(fakes) == {"outcome": sync.OUTCOME_BACKED_OFF}

    def test_s6_a_written_key_clears_the_backoff(self, monkeypatch, rsa_key, edh_session_table):
        edh_session_table.put_item(Item={"bootstrapId": LOCK_KEY, "syncStartedAt": Decimal(int(START) - 4000),
                                         "unchangedStreak": Decimal(3), "retryAfter": Decimal(int(START) - 1)})
        fakes = install(monkeypatch, rsa_key, table=edh_session_table)

        assert run(fakes) == {"outcome": sync.OUTCOME_UPDATED}
        row = _lock_row(edh_session_table)
        assert "unchangedStreak" not in row and "retryAfter" not in row
        assert int(row["syncStartedAt"]) == int(START)
        assert int(START) <= int(row["updatedAt"]) <= fakes.clock.now

    def test_s6_the_rate_limit_still_holds_a_second_trigger_inside_the_interval(
            self, monkeypatch, rsa_key, edh_session_table):
        fakes = install(monkeypatch, rsa_key, table=edh_session_table)

        assert run(fakes) == {"outcome": sync.OUTCOME_UPDATED}
        fakes.clock.now += sync.ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS - 1
        assert run(fakes) == {"outcome": sync.OUTCOME_RATE_LIMITED}
        assert run(fakes, {"source": "manual"}) == {"outcome": sync.OUTCOME_RATE_LIMITED}
        assert len(fakes.ssm.sent) == 1
        assert [json.loads(line)[sync.METRIC_FAILURES] for line in fakes.metrics] == [0, 0, 0]

    def test_s6_a_manual_run_inside_the_interval_is_rate_limited_not_backed_off(
            self, monkeypatch, rsa_key, edh_session_table):
        edh_session_table.put_item(Item={"bootstrapId": LOCK_KEY, "syncStartedAt": Decimal(int(START) - 10),
                                         "unchangedStreak": Decimal(1), "retryAfter": Decimal(int(START) + 500)})
        fakes = install(monkeypatch, rsa_key, table=edh_session_table)

        assert run(fakes, {"source": "manual"}) == {"outcome": sync.OUTCOME_RATE_LIMITED}
        assert json.loads(fakes.metrics[-1])[sync.METRIC_FAILURES] == 0

    def test_s6_a_lasting_misconfiguration_counts_failures_every_hour_and_runs_the_document_rarely(
            self, monkeypatch, rsa_key, edh_session_table):
        """The reconciler's sweep meets the rejection every 15 minutes and asks for a sync each time. Over six
        hours the document runs 6 times instead of 24, and every clock hour still sums 4 failures, at or above
        the threshold of 3 in one hour the docs suggest for an operator's alarm on the metric."""
        fakes = install(monkeypatch, rsa_key, secrets=FakeSecretsManager(current=NEW_KEY), table=edh_session_table)
        outcomes = []
        for sweep in range(24):
            fakes.clock.now = START + sweep * 900
            outcomes.append(run(fakes)["outcome"])

        assert len(fakes.ssm.sent) == 6
        assert outcomes.count(sync.OUTCOME_UNCHANGED) == 6
        assert outcomes.count(sync.OUTCOME_BACKED_OFF) == 18
        failures = [json.loads(line)[sync.METRIC_FAILURES] for line in fakes.metrics]
        assert [sum(failures[hour * 4:hour * 4 + 4]) for hour in range(6)] == [4] * 6
        assert fakes.secrets.writes == []

    @pytest.mark.parametrize("row", [
        {"syncStartedAt": Decimal(int(START) + 10_000)},
        {"syncStartedAt": Decimal(int(START) - 4000), "retryAfter": Decimal(int(START) + 10 ** 9),
         "unchangedStreak": Decimal(1)},
    ], ids=["start stamp far ahead", "backoff past its ceiling"])
    def test_s12_a_stamp_from_the_future_does_not_stop_the_sync(self, monkeypatch, rsa_key, edh_session_table, row):
        edh_session_table.put_item(Item={"bootstrapId": LOCK_KEY, **row})
        fakes = install(monkeypatch, rsa_key, table=edh_session_table)

        assert run(fakes) == {"outcome": sync.OUTCOME_UPDATED}
        assert int(_lock_row(edh_session_table)["syncStartedAt"]) == int(START)

    def test_s12_a_stamp_a_little_ahead_still_holds_the_interval(self, monkeypatch, rsa_key, edh_session_table):
        edh_session_table.put_item(Item={"bootstrapId": LOCK_KEY, "syncStartedAt": Decimal(int(START) + 60)})
        fakes = install(monkeypatch, rsa_key, table=edh_session_table)

        assert run(fakes) == {"outcome": sync.OUTCOME_RATE_LIMITED}


@pytest.mark.unit
class TestATriggerQueuedBeforeTheLastWrite:
    """Lambda keeps a throttled trigger up to the function's maximum event age (15 minutes) and may deliver one
    twice, so a trigger queued before a sync wrote the key can run after the 5-minute interval and find the key it
    just repaired. Within MAX_EVENT_AGE_SECONDS of the row's updatedAt such an automatic "unchanged" is not a
    failure, sets no backoff and grows no streak; an updatedAt further ahead than CLOCK_SKEW_SECONDS is ignored, so
    a forged or skewed one cannot keep failures from counting (review N-1)."""

    @staticmethod
    def _finished(fakes):
        return [r[2] for r in fakes.log.records if r[0] == "EDH root key sync finished"][-1]

    def test_n1_the_max_event_age_is_the_functions(self):
        builder = (REPO_ROOT / "infra" / "lib" / "nestedStacks" / "addon" / "edhWorkstations" / "lambdaBuilder"
                   / "edhWorkstationsFunctions.ts").read_text()
        match = re.search(r"EDH_ROOT_KEY_SYNC_MAX_EVENT_AGE = Duration\.minutes\((\d+)\)", builder)
        assert match, "the builder declares the sync's maximum event age in minutes"
        assert sync.MAX_EVENT_AGE_SECONDS == int(match.group(1)) * 60
        assert 0 < sync.CLOCK_SKEW_SECONDS < sync.ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS

    def test_n1_a_delayed_trigger_after_an_update_is_unchanged_and_not_a_failure(
            self, monkeypatch, rsa_key, edh_session_table):
        fakes = install(monkeypatch, rsa_key, table=edh_session_table)
        assert run(fakes) == {"outcome": sync.OUTCOME_UPDATED}

        fakes.clock.now = START + 14 * 60
        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_UNCHANGED}
        assert len(fakes.ssm.sent) == 2
        assert self._finished(fakes)["failure"] is False
        assert json.loads(fakes.metrics[-1])[sync.METRIC_FAILURES] == 0
        row = _lock_row(edh_session_table)
        assert "retryAfter" not in row and "unchangedStreak" not in row
        # The next automatic trigger is held only by the interval.
        fakes.clock.now += sync.ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS + 1
        assert run(fakes) == {"outcome": sync.OUTCOME_UNCHANGED}
        assert_no_key_leaked(fakes, result)

    def test_n1_the_same_trigger_16_minutes_after_the_update_is_a_failure_and_backs_off(
            self, monkeypatch, rsa_key, edh_session_table):
        fakes = install(monkeypatch, rsa_key, table=edh_session_table)
        assert run(fakes) == {"outcome": sync.OUTCOME_UPDATED}

        fakes.clock.now = START + 16 * 60
        assert run(fakes) == {"outcome": sync.OUTCOME_UNCHANGED}

        assert self._finished(fakes)["failure"] is True
        assert json.loads(fakes.metrics[-1])[sync.METRIC_FAILURES] == 1
        row = _lock_row(edh_session_table)
        interval = sync.ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS
        assert (int(row["unchangedStreak"]), int(row["retryAfter"])) == (1, int(fakes.clock.now) + 2 * interval)

    def test_n1_a_delayed_trigger_does_not_grow_the_streak(self, monkeypatch, rsa_key, edh_session_table):
        edh_session_table.put_item(Item={"bootstrapId": LOCK_KEY, "syncStartedAt": Decimal(int(START) - 400),
                                         "updatedAt": Decimal(int(START) - 400), "unchangedStreak": Decimal(2),
                                         "retryAfter": Decimal(int(START) - 1)})
        fakes = install(monkeypatch, rsa_key, secrets=FakeSecretsManager(current=NEW_KEY), table=edh_session_table)

        assert run(fakes) == {"outcome": sync.OUTCOME_UNCHANGED}
        assert self._finished(fakes)["failure"] is False
        row = _lock_row(edh_session_table)
        assert (int(row["unchangedStreak"]), int(row["retryAfter"])) == (2, int(START) - 1)

    @pytest.mark.parametrize("updated_at,failure", [
        (int(START) + 10_000, True),
        (int(START) + 10 ** 9, True),
        (int(START) - 16 * 60, True),
        (int(START) + 30, False),
    ], ids=["far in the future", "beyond any ceiling", "older than the max event age", "inside the clock skew"])
    def test_n1_an_updated_at_in_the_future_beyond_the_skew_is_ignored(
            self, monkeypatch, rsa_key, edh_session_table, updated_at, failure):
        edh_session_table.put_item(Item={"bootstrapId": LOCK_KEY, "syncStartedAt": Decimal(int(START) - 4000),
                                         "updatedAt": Decimal(updated_at)})
        fakes = install(monkeypatch, rsa_key, secrets=FakeSecretsManager(current=NEW_KEY), table=edh_session_table)

        assert run(fakes) == {"outcome": sync.OUTCOME_UNCHANGED}

        assert self._finished(fakes)["failure"] is failure
        row = _lock_row(edh_session_table)
        assert ("retryAfter" in row) is failure

    @pytest.mark.parametrize("updated_at", ["soon", True], ids=["a string", "a boolean"])
    def test_n1_an_updated_at_that_is_not_a_time_is_ignored(
            self, monkeypatch, rsa_key, edh_session_table, updated_at):
        edh_session_table.put_item(Item={"bootstrapId": LOCK_KEY, "syncStartedAt": Decimal(int(START) - 4000),
                                         "updatedAt": updated_at})
        fakes = install(monkeypatch, rsa_key, secrets=FakeSecretsManager(current=NEW_KEY), table=edh_session_table)

        assert run(fakes) == {"outcome": sync.OUTCOME_UNCHANGED}
        assert self._finished(fakes)["failure"] is True

    def test_n1_a_manual_run_stamps_updated_at_too(self, monkeypatch, rsa_key, edh_session_table):
        fakes = install(monkeypatch, rsa_key, table=edh_session_table)

        assert run(fakes, {"source": "manual"}) == {"outcome": sync.OUTCOME_UPDATED}
        assert int(START) <= int(_lock_row(edh_session_table)["updatedAt"]) <= fakes.clock.now


@pytest.mark.unit
class TestTheOutcomeIsLoggedBeforeTheBookkeeping:
    """The backoff or updatedAt write runs after the outcome line and the metric, so a slow or failing write
    cannot cost the run its outcome (review N-2)."""

    @pytest.mark.parametrize("current,outcome", [(CURRENT_KEY, "updated"), (NEW_KEY, "unchanged")],
                             ids=["updated", "unchanged"])
    def test_n2_the_bookkeeping_write_comes_after_the_outcome_line(self, monkeypatch, rsa_key, current, outcome):
        fakes = install(monkeypatch, rsa_key, secrets=FakeSecretsManager(current=current))
        admit = fakes.table.update_item
        seen = []

        def recording(**kwargs):
            if ":cutoff" not in kwargs.get("ExpressionAttributeValues", {}):
                seen.append(([r[0] for r in fakes.log.records], len(fakes.metrics)))
            return admit(**kwargs)

        fakes.table.update_item = recording

        assert run(fakes) == {"outcome": outcome}
        assert len(seen) == 1
        logged, metrics = seen[0]
        assert "EDH root key sync finished" in logged and metrics == 1

    def test_n2_a_bookkeeping_write_that_fails_unexpectedly_keeps_the_outcome(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key)
        admit = fakes.table.update_item

        def failing(**kwargs):
            if ":cutoff" not in kwargs.get("ExpressionAttributeValues", {}):
                raise RuntimeError("boom " + NEW_KEY)
            return admit(**kwargs)

        fakes.table.update_item = failing

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_UPDATED}
        assert json.loads(fakes.metrics[-1])[sync.METRIC_FAILURES] == 0
        assert_no_key_leaked(fakes, result)


@pytest.mark.unit
class TestLoggingAndMetric:
    SCENARIOS = [
        ({}, "updated", 0),
        ({"secrets": "same"}, "unchanged", 1),
        ({"ssm": "bad key file"}, "invalid", 1),
        ({"ssm": "failed"}, "commandFailed", 1),
        ({"secrets": "write refused"}, "secretWriteFailed", 1),
        ({"table": "held"}, "rateLimited", 0),
    ]

    @staticmethod
    def _parts(rsa_key, spec):
        parts = {}
        if spec.get("secrets") == "same":
            parts["secrets"] = FakeSecretsManager(current=NEW_KEY)
        if spec.get("secrets") == "write refused":
            parts["secrets"] = FakeSecretsManager(write_error=_client_error("AccessDeniedException", "PutSecretValue"))
        if spec.get("ssm") == "bad key file":
            parts["ssm"] = FakeSsm(key_file=NEW_KEY + "!")
        if spec.get("ssm") == "failed":
            parts["ssm"] = FakeSsm(statuses=["Failed"])
        if spec.get("table") == "held":
            parts["table"] = FakeLockTable(held_at=Decimal(int(START)))
        return parts

    @pytest.mark.parametrize("spec,outcome,failures", SCENARIOS, ids=[s[1] for s in SCENARIOS])
    def test_a5_each_run_logs_one_outcome_and_one_failure_count_and_never_the_key(
            self, monkeypatch, rsa_key, spec, outcome, failures):
        fakes = install(monkeypatch, rsa_key, **self._parts(rsa_key, spec))

        result = run(fakes)

        assert result == {"outcome": outcome}
        assert_no_key_leaked(fakes, result)
        finished = [r for r in fakes.log.records if r[0] == "EDH root key sync finished"]
        assert len(finished) == 1 and finished[0][2]["outcome"] == outcome
        assert len(fakes.metrics) == 1
        line = json.loads(fakes.metrics[0])
        directive = line["_aws"]["CloudWatchMetrics"][0]
        assert directive["Namespace"] == sync.METRIC_NAMESPACE
        assert directive["Metrics"] == [{"Name": sync.METRIC_FAILURES, "Unit": "Count"}]
        assert directive["Dimensions"] == [["FunctionName"]]
        assert line[sync.METRIC_FAILURES] == failures
        assert line["outcome"] == outcome

    def test_s13_the_outcome_line_carries_the_request_id_of_a_manual_run(self, monkeypatch, rsa_key):
        fakes = install(monkeypatch, rsa_key)
        context = FakeContext(fakes.clock, START + 120, request_id="0f0e0d0c-aaaa-4bbb-8ccc-000000000001")

        run(fakes, {"source": "manual"}, context)

        finished = [r for r in fakes.log.records if r[0] == "EDH root key sync finished"]
        assert finished[0][2]["requestId"] == "0f0e0d0c-aaaa-4bbb-8ccc-000000000001"
        assert finished[0][2]["trigger"] == "manual"

    @pytest.mark.parametrize("request_id", ["not-a-request-id\nforged line", 12345, None])
    def test_s13_a_request_id_that_is_not_a_uuid_is_not_logged(self, monkeypatch, rsa_key, request_id):
        fakes = install(monkeypatch, rsa_key)

        run(fakes, None, FakeContext(fakes.clock, START + 120, request_id=request_id))

        finished = [r for r in fakes.log.records if r[0] == "EDH root key sync finished"]
        assert "requestId" not in finished[0][2]

    def test_the_metric_line_is_written_to_standard_output_as_embedded_metric_format(self, capsys):
        sync._write_metric_line('{"probe": 1}')
        assert capsys.readouterr().out == '{"probe": 1}\n'


@pytest.mark.unit
@REQUIRES_CONTROLLER_TOOLS
class TestTheDocumentScriptMatchesTheRunsKey:
    """The document's own script, run under the local openssl, against the padding the sync decrypts with."""

    def test_the_script_prints_only_oaep_sha_256_ciphertext_of_the_key_file(self, rsa_key, tmp_path):
        output = run_document_script(_public_der(rsa_key), NEW_KEY, tmp_path)

        assert NEW_KEY not in output
        ciphertext = base64.b64decode(output, validate=True)
        assert len(ciphertext) == rsa_key.key_size // 8
        assert rsa_key.decrypt(ciphertext, OAEP_SHA_256).decode() == NEW_KEY

    def test_the_mgf1_digest_is_sha_256_too(self, rsa_key, tmp_path):
        ciphertext = base64.b64decode(run_document_script(_public_der(rsa_key), NEW_KEY, tmp_path))
        with pytest.raises(ValueError):
            rsa_key.decrypt(ciphertext, padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA256(),
                                                     label=None))
        with pytest.raises(ValueError):
            rsa_key.decrypt(ciphertext, padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(),
                                                     label=None))

    def test_s10_the_sync_decrypts_what_the_script_encrypts_to_its_generated_key(self, monkeypatch, rsa_key, tmp_path):
        fakes = install(monkeypatch, rsa_key, ssm=FakeSsm(run_script=True, tmp_path=tmp_path), keys=KeyRecorder())

        result = run(fakes)

        assert result == {"outcome": sync.OUTCOME_UPDATED}
        assert json.loads(fakes.secrets.writes[0]["SecretString"]) == {"rootKey": NEW_KEY}
        assert NEW_KEY not in fakes.ssm.script_output
        # The generated key, not the module's fixed one, decrypted the script's output.
        assert len(fakes.keys.handed_out) == 1 and fakes.keys.handed_out[0] is not rsa_key
        assert len(base64.b64decode(fakes.ssm.script_output)) == 384
        assert_no_private_key_leaked(fakes, result)

    def test_s10_the_generated_key_cannot_decrypt_another_runs_ciphertext(self, rsa_key, tmp_path):
        """Ciphertext Run Command keeps from one run is useless to every later run: each holds another key."""
        ciphertext = base64.b64decode(run_document_script(_public_der(rsa_key), NEW_KEY, tmp_path))
        later = GENERATE_PRIVATE_KEY()
        with pytest.raises(ValueError):
            later.decrypt(ciphertext, sync.OAEP_SHA_256)

    def test_the_script_writes_its_files_private_and_removes_them(self, rsa_key, tmp_path):
        traced = DOCUMENT_SCRIPT.read_text().replace(
            'base64 -w 0 "$workdir/root-key.enc"',
            'stat -c "%a %n" "$workdir" "$workdir"/* >&2\nbase64 -w 0 "$workdir/root-key.enc"')
        key_file = tmp_path / "admin_api_key.txt"
        key_file.write_text(NEW_KEY)
        workdir = tmp_path / "tmp"
        workdir.mkdir()
        script = traced.replace("__EDH_ROOT_KEY_FILE__", str(key_file)).replace(
            "{{ publicKey }}", base64.b64encode(_public_der(rsa_key)).decode())
        result = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=60,
                                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TMPDIR": str(workdir)})

        assert result.returncode == 0, result.stderr
        modes = [line.split(" ", 1)[0] for line in result.stderr.strip().splitlines()]
        assert modes == ["700", "600", "600"]
        assert list(workdir.iterdir()) == []

    def test_a_failing_encryption_prints_nothing_and_removes_its_files(self, tmp_path):
        key_file = tmp_path / "admin_api_key.txt"
        key_file.write_text(NEW_KEY)
        workdir = tmp_path / "tmp"
        workdir.mkdir()
        script = DOCUMENT_SCRIPT.read_text().replace("__EDH_ROOT_KEY_FILE__", str(key_file)).replace(
            "{{ publicKey }}", base64.b64encode(b"not a public key").decode())
        result = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=60,
                                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TMPDIR": str(workdir)})

        assert result.returncode != 0
        assert result.stdout == ""
        assert NEW_KEY not in result.stderr
        assert list(workdir.iterdir()) == []
