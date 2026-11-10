# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""AWS session factory and partition-generic ARN helpers.

Never hardcodes the ``aws`` partition: the partition comes from the env ledger
or from the STS caller ARN. Tests replace :class:`AwsFactory` with a stubbed one.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

# Base-credential variables the `credential-process` helper restores from VRA_BASE_<name>.
BASE_ENV_KEYS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_CONFIG_FILE",
                 "AWS_SHARED_CREDENTIALS_FILE",
                 # the helper's own base session may come from these (CodeBuild container role, EC2, OIDC)
                 "AWS_EC2_METADATA_DISABLED", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
                 "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN")

# Credential sources an SDK in a child process falls back to after the configured profile: disabled for children
# that get a vra credential_process profile.
NO_FALLBACK_CREDENTIALS_ENV = {"AWS_EC2_METADATA_DISABLED": "true", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "",
                               "AWS_CONTAINER_CREDENTIALS_FULL_URI": "", "AWS_WEB_IDENTITY_TOKEN_FILE": "",
                               "AWS_ROLE_ARN": ""}

PARTITION_RE = re.compile(r"^aws(-[a-z]+)*$")
ARN_RE = re.compile(r"^arn:(?P<partition>[a-z0-9-]+):(?P<service>[a-z0-9-]*):(?P<region>[a-z0-9-]*):"
                    r"(?P<account>\d{12}|aws|):(?P<resource>.+)$")



def _partitions() -> list[dict]:
    """The SDK's partition data (every partition it knows, with its region regex and DNS suffix)."""
    global _PARTITIONS
    if _PARTITIONS is None:
        import botocore.loaders
        _PARTITIONS = botocore.loaders.create_loader().load_data("partitions")["partitions"]
    return _PARTITIONS


_PARTITIONS: list[dict] | None = None

# DNS suffix per partition (aws, aws-cn, aws-us-gov, aws-eusc, aws-iso, aws-iso-b, aws-iso-e, aws-iso-f, ...).
KNOWN_PARTITIONS = {p["id"]: p["outputs"]["dnsSuffix"] for p in _partitions()}
# Partitions where vra's services have FIPS endpoints. The SDK data marks every partition FIPS-capable, but in
# aws-eusc (and the ISO partitions) AWS_USE_FIPS_ENDPOINT points the CLI at hosts that do not exist; set
# `fips: true` in env.yaml to force it where you know the endpoints exist.
FIPS_PARTITIONS = {"aws", "aws-us-gov"}


def partition_for_region(region: str | None) -> str | None:
    if not region:
        return None
    for p in _partitions():
        if region in (p.get("regions") or {}) or re.match(p["regionRegex"], region):
            return p["id"]
    return None


def dns_suffix(partition: str | None) -> str:
    return KNOWN_PARTITIONS.get(partition or "aws", "amazonaws.com")


def fips_on(env: dict, vams_config: dict | None) -> bool:
    """FIPS endpoints for the engine's AWS calls and deploy/test/hook children: env.yaml ``fips`` (true/false), else
    auto = the overlay's ``app.useFips`` in a partition that offers FIPS endpoints. Never applied to synth."""
    v = env.get("fips", "auto")
    if v is True or v is False:
        return v
    part = env.get("partition") or partition_for_region(env.get("region"))
    return bool(((vams_config or {}).get("app") or {}).get("useFips")) and part in FIPS_PARTITIONS


def parse_arn(arn: str) -> dict[str, str]:
    m = ARN_RE.match(arn or "")
    if not m:
        raise ValueError(f"not an ARN: {arn!r}")
    return m.groupdict()


def partition_from_arn(arn: str) -> str:
    return parse_arn(arn)["partition"]


def make_arn(partition: str, service: str, region: str, account: str, resource: str) -> str:
    if not PARTITION_RE.match(partition):
        raise ValueError(f"invalid partition {partition!r}")
    return f"arn:{partition}:{service}:{region}:{account}:{resource}"


def principal_arn_for_simulation(caller_arn: str) -> str:
    """Convert an STS assumed-role ARN into the IAM role ARN that SimulatePrincipalPolicy accepts.

    arn:<p>:sts::<acct>:assumed-role/<RoleName>/<session> -> arn:<p>:iam::<acct>:role/<RoleName>
    (role paths are not visible in the STS ARN; callers can pass ``deployRoleArn`` from the ledger
    instead, which keeps the path).
    """
    a = parse_arn(caller_arn)
    if a["service"] == "sts" and a["resource"].startswith("assumed-role/"):
        role = a["resource"].split("/")[1]
        return f"arn:{a['partition']}:iam::{a['account']}:role/{role}"
    return caller_arn


def substitute(template: str, values: dict[str, str]) -> str:
    """Replace ``${Name}`` placeholders. Unknown placeholders are left in place (and reported)."""
    def rep(m: re.Match) -> str:
        return str(values.get(m.group(1), m.group(0)))
    return re.sub(r"\$\{([A-Za-z0-9_]+)\}", rep, template)


class AwsFactory:
    """Creates boto3 sessions/clients per ledger env.

    Env resolution: ``profile`` -> named profile; ``deployRoleArn`` -> AssumeRole from the base
    session (role session name ``vra-<runId>``). ``dataCopyRoleArn`` etc. can be selected via ``role``.
    """

    def __init__(self, ledger: Any = None, run_id: str | None = None, base_profile: str | None = None):
        self.ledger = ledger
        self.run_id = run_id or "vra"
        self.base_profile = base_profile
        self._sessions: dict[tuple[str, str], Any] = {}
        self._identity: dict[tuple[str, str], dict] = {}
        self._cfg_dir: Path | None = None
        self._via: dict[tuple[str, str], tuple[str, str]] = {}

    def set_via(self, env_name: str, role: str, via: tuple[str, str]) -> None:
        """Assume (env_name, role) FROM the session of ``via`` (role chaining), e.g. the source export role
        from the target loader role when the orchestrator may only reach the target side."""
        self._via[(env_name, role)] = via
        self._sessions.pop((env_name, role), None)

    # -- sessions -----------------------------------------------------------------
    def _boto3(self):
        import boto3  # local import keeps CLI startup fast
        return boto3

    def base_session(self, region: str | None = None):
        b = self._boto3()
        return b.Session(profile_name=self.base_profile, region_name=region) if self.base_profile else \
            b.Session(region_name=region)

    def role_arn(self, env_name: str, role: str = "deploy") -> str | None:
        env = self.ledger.env(env_name)
        key = {"deploy": "deployRoleArn", "dataCopy": "dataCopyRoleArn", "backup": "deployRoleArn",
               "readonly": "readOnlyRoleArn", "synth": "synthRoleArn", "test": "testRoleArn"}.get(role, f"{role}RoleArn")
        if role == "synth":
            return env.get(key)  # a build identity never falls back to the deploy role
        return env.get(key) or env.get("deployRoleArn")

    def assume_fetcher(self, env_name: str, role: str = "deploy"):
        """Return a zero-arg function that calls sts:AssumeRole for the env role and returns the raw
        STS ``Credentials`` dict. Each call is a fresh AssumeRole, so callers can refresh past the
        1-hour role-chaining cap."""
        env = self.ledger.env(env_name)
        region = env["region"]
        role_arn = self.role_arn(env_name, role)
        if not role_arn:
            return None
        duration = int(env.get("sessionDurationSeconds", 3600))
        via = self._via.get((env_name, role))
        if via:
            duration = min(duration, 3600)  # chained role sessions are capped at one hour
        source_identity = self._source_identity(env)

        def fetch() -> dict:
            parent = self.session(*via) if via else self.base_session(region)
            sts = parent.client("sts", region_name=region)
            kw: dict[str, Any] = {"RoleArn": role_arn, "RoleSessionName": f"vra-{self.run_id}"[:64],
                                  "DurationSeconds": duration}
            if source_identity:
                kw["SourceIdentity"] = source_identity
            resp = sts.assume_role(**kw)
            return resp["Credentials"]

        return fetch

    source_identity_value: str | None = None  # set by the CLI to the resolved initiator

    def _source_identity(self, env: dict) -> str | None:
        """sts:SourceIdentity for env roles whose trust requires it (env.yaml ``setSourceIdentity: true``):
        CloudTrail in the env account then attributes every call to the initiator, not just to the role."""
        from vra.ledger import source_identity_on
        if not source_identity_on(env) or not self.source_identity_value:
            return None
        return re.sub(r"[^\w+=,.@-]", "-", self.source_identity_value)[:64] or None

    def session(self, env_name: str, role: str = "deploy"):
        cache_key = (env_name, role)
        if cache_key in self._sessions:
            return self._sessions[cache_key]
        env = self.ledger.env(env_name)
        region = env["region"]
        if env.get("profile"):
            sess = self._boto3().Session(profile_name=env["profile"], region_name=region)
        else:
            fetch = self.assume_fetcher(env_name, role)
            if fetch:
                sess = refreshable_session(self._boto3(), fetch, region)
            else:
                sess = self.base_session(region)
        self._sessions[cache_key] = sess
        return sess

    def _fips(self, env_name: str) -> bool:
        try:
            return fips_on(self.ledger.env(env_name), self.ledger.vams_config(env_name))
        except Exception:  # noqa: BLE001 - an unreadable overlay is reported by validate
            return False

    def client(self, env_name: str, service: str, role: str = "deploy", region: str | None = None):
        sess = self.session(env_name, role)
        kw: dict[str, Any] = {"region_name": region or self.ledger.env(env_name)["region"]}
        if self._fips(env_name):
            from botocore.config import Config
            kw["config"] = Config(use_fips_endpoint=True)
        return sess.client(service, **kw)

    def tools_client(self, service: str, region: str | None = None):
        """Client in the tools/state account (the caller's own credentials)."""
        return self.base_session(region).client(service, region_name=region)

    def identity(self, env_name: str, role: str = "deploy") -> dict:
        key = (env_name, role)
        if key not in self._identity:
            self._identity[key] = self.client(env_name, "sts", role).get_caller_identity()
        return self._identity[key]

    def partition(self, env_name: str) -> str:
        env = self.ledger.env(env_name)
        if env.get("partition"):
            return env["partition"]
        return partition_from_arn(self.identity(env_name)["Arn"])

    def subprocess_env(self, env_name: str, role: str = "deploy") -> dict[str, str]:
        """Environment variables for child processes (cdk, migration scripts). Never logged."""
        env = self.ledger.env(env_name)
        out = {"AWS_REGION": env["region"], "AWS_DEFAULT_REGION": env["region"], "CDK_DEFAULT_REGION": env["region"],
               "CDK_DEFAULT_ACCOUNT": str(env["account"])}
        if self._fips(env_name):
            out["AWS_USE_FIPS_ENDPOINT"] = "true"  # deploy / test / hook children; build.synth removes it
        if env.get("profile"):
            out["AWS_PROFILE"] = env["profile"]
            return out
        if self.role_arn(env_name, role) and env.get("refreshCredentials", True):
            # Long deploys outlive the 1-hour role-chaining cap. Instead of exporting static keys, hand
            # the child (cdk, migration scripts) a profile whose credential_process re-runs
            # `vra credential-process`, which does a fresh AssumeRole whenever the SDK sees expiry.
            out.update(self.credential_process_env(env_name, role))
            return out
        creds = self.session(env_name, role).get_credentials()
        if creds is not None:
            fc = creds.get_frozen_credentials()
            out["AWS_ACCESS_KEY_ID"] = fc.access_key
            out["AWS_SECRET_ACCESS_KEY"] = fc.secret_key
            if fc.token:
                out["AWS_SESSION_TOKEN"] = fc.token
        return out

    # -- credential_process for child processes ------------------------------------------
    def _config_dir(self) -> Path:
        if self._cfg_dir is None:
            self._cfg_dir = Path(tempfile.mkdtemp(prefix="vra-aws-"))
            # 0o700: owner-only, the most restrictive mode for a directory the owner uses
            # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
            os.chmod(self._cfg_dir, 0o700)
        return self._cfg_dir

    def credential_process_env(self, env_name: str, role: str = "deploy") -> dict[str, str]:
        """Env vars that point a child process at a run-scoped AWS config file with one profile
        ``vra-<env>-<role>`` whose ``credential_process`` is ``vra credential-process``.

        The file holds no secrets. The caller's own base credentials are passed to the helper under
        ``VRA_BASE_*`` names (they are already in the child's environment) and the plain ``AWS_*``
        key variables are blanked, so the SDK in the child uses the profile and nothing else.
        """
        profile = re.sub(r"[^A-Za-z0-9_-]", "-", f"vra-{env_name}-{role}")
        nonce = self._nonce()
        argv = [sys.executable, "-m", "vra", "credential-process", "--ledger", str(Path(self.ledger.root).resolve()),
                "--env", env_name, "--role", role, "--session-run-id", self.run_id,
                "--nonce-file", str(self._config_dir() / "nonce")]
        if self.base_profile:
            argv += ["--base-profile", self.base_profile]
        if self._source_identity(self.ledger.env(env_name)):
            argv += ["--source-identity", self.source_identity_value]
        cfg = self._config_dir() / f"{profile}.config"
        cfg.write_text(f"[profile {profile}]\ncredential_process = {shlex.join(argv)}\n"
                       f"region = {self.ledger.env(env_name)['region']}\n", encoding="utf-8")
        out = {"AWS_CONFIG_FILE": str(cfg), "AWS_PROFILE": profile, "AWS_SDK_LOAD_CONFIG": "1",
               "AWS_SHARED_CREDENTIALS_FILE": str(self._config_dir() / "empty-credentials")}
        (self._config_dir() / "empty-credentials").touch()
        for k in BASE_ENV_KEYS:
            if os.environ.get(k) is not None:
                out[f"VRA_BASE_{k}"] = os.environ[k]
        for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
            out[k] = ""
        # the profile is the ONLY credential source: if it fails, the child must fail, never fall back to the
        # host's instance profile or container role (live test: cdk synth ran fork code as the build host's role)
        out.update(NO_FALLBACK_CREDENTIALS_ENV)
        out[CREDPROC_NONCE_ENV] = nonce
        return out

    def _nonce(self) -> str:
        """Per-process secret shared with the engine's own child processes only: `vra credential-process` refuses
        to print anything without it, so an agent (or anyone) running the helper by hand gets nothing."""
        p = self._config_dir() / "nonce"
        if not p.exists():
            import secrets as _s
            fd = os.open(str(p), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(_s.token_hex(24))
        return p.read_text(encoding="utf-8").strip()


def _iso(v: Any) -> str:
    if isinstance(v, dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=dt.timezone.utc)
        return v.isoformat()
    return str(v)


def refreshable_session(boto3_mod, fetch: Callable[[], dict], region: str):
    """boto3 Session whose credentials re-run ``fetch`` (an AssumeRole) shortly before they expire."""
    from botocore.credentials import RefreshableCredentials
    from botocore.session import get_session

    def metadata() -> dict:
        c = fetch()
        return {"access_key": c["AccessKeyId"], "secret_key": c["SecretAccessKey"], "token": c["SessionToken"],
                "expiry_time": _iso(c["Expiration"])}

    creds = RefreshableCredentials.create_from_metadata(metadata=metadata(), refresh_using=metadata,
                                                        method="sts-assume-role")
    bs = get_session()
    bs._credentials = creds  # documented pattern for injecting refreshable credentials
    return boto3_mod.Session(botocore_session=bs, region_name=region)


CREDPROC_NONCE_ENV = "VRA_CREDPROC_NONCE"


def check_credproc_nonce(nonce_file: str | None, environ: dict | None = None) -> bool:
    environ = os.environ if environ is None else environ
    given = environ.get(CREDPROC_NONCE_ENV) or ""
    if not nonce_file or not given:
        return False
    try:
        expected = Path(nonce_file).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    import hmac
    return bool(expected) and hmac.compare_digest(expected, given)


def restore_base_env(environ: dict | None = None) -> None:
    """Undo :meth:`AwsFactory.credential_process_env` inside the helper process."""
    environ = os.environ if environ is None else environ
    for k in BASE_ENV_KEYS:
        base = environ.pop(f"VRA_BASE_{k}", None)
        if base is not None:
            environ[k] = base
        else:
            environ.pop(k, None)


def credential_process_output(creds: dict) -> str:
    """JSON document for the AWS SDK ``credential_process`` protocol (Version 1)."""
    return json.dumps({"Version": 1, "AccessKeyId": creds["AccessKeyId"], "SecretAccessKey": creds["SecretAccessKey"],
                       "SessionToken": creds["SessionToken"], "Expiration": _iso(creds["Expiration"])})


def client_error_parts(exc: BaseException) -> tuple[str, str, str]:
    """(error code, message, operation name) for a botocore ClientError; empty strings otherwise."""
    resp = getattr(exc, "response", None) or {}
    err = resp.get("Error", {}) if isinstance(resp, dict) else {}
    return (str(err.get("Code", "")), str(err.get("Message", "")), str(getattr(exc, "operation_name", "") or ""))
