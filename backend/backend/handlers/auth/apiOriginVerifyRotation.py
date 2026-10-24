# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""AWS Secrets Manager rotation function of the API origin verification secret.

On a CloudFront deployment CloudFront adds the secret's ``current`` value to every request it
forwards to the REST API (``common.auth.originVerify``). Secrets Manager invokes this function with
the four rotation steps:

- ``createSecret``: once the distribution is ``Deployed`` (no earlier header change still
  propagating), write ``{"current": <new>, "previous": <old current>}`` as the ``AWSPENDING``
  version.
- ``setSecret``: set the regional web ACL's origin verification matches to the new and the old value
  (when ``ORIGIN_VERIFY_WEB_ACL_ARN`` names one), wait for the web ACL change to settle, then point
  every distribution origin header named ``ORIGIN_VERIFY_HEADER_NAME`` at the new value. CloudFront
  edges send the old or the new value while the change propagates; the web ACL and the authorizer
  accept both.
- ``testSecret``: no service-side check. Secrets Manager also runs this step on its own, against a
  test version it creates, when the rotation schedule is created or changed without rotating.
- ``finishSecret``: apply the ``setSecret`` changes again (a stack update that landed during the
  rotation resolves the web ACL and the distribution to the old value; with no such update nothing
  changes), then move ``AWSCURRENT`` to the new version.

A later stack update resolves the CloudFront header and the web ACL matches to ``AWSCURRENT``, which
is the state this function leaves them in. Each step is idempotent, so a retried step does not repeat
a change already made. No value is logged.
"""
import json
import os
import time
from typing import Iterable, List, Sequence, Tuple

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from common.auth.originVerify import (
    CURRENT_KEY,
    ORIGIN_VERIFY_HEADER_NAME,
    PREVIOUS_KEY,
    values_from_secret_string,
)
from customLogging.logger import safeLogger

logger = safeLogger(service_name="ApiOriginVerifyRotation")

DISTRIBUTION_ID = os.environ.get("ORIGIN_VERIFY_DISTRIBUTION_ID", "")
WEB_ACL_ARN = os.environ.get("ORIGIN_VERIFY_WEB_ACL_ARN", "")

VALUE_LENGTH = 64
WAF_SETTLE_SECONDS = 60
WAF_LOCK_ATTEMPTS = 3

retry_config = BotoConfig(retries={"max_attempts": 5, "mode": "adaptive"})
secrets_client = boto3.client("secretsmanager", config=retry_config)
cloudfront_client = boto3.client("cloudfront", config=retry_config)
waf_client = boto3.client("wafv2", config=retry_config)


class RotationError(Exception):
    pass


def _error_code(error: ClientError) -> str:
    return (getattr(error, "response", None) or {}).get("Error", {}).get("Code", "")


# ---------------------------------------------------------------------------------------------
# Pure helpers: the web ACL rules and the distribution configuration
# ---------------------------------------------------------------------------------------------


def _origin_verify_matches(rule: dict) -> List[dict]:
    """The origin verification byte-match statements of one rate-based rule's scope-down, or []."""
    scope_down = ((rule.get("Statement") or {}).get("RateBasedStatement") or {}).get(
        "ScopeDownStatement"
    ) or {}
    statements = (
        ((scope_down.get("NotStatement") or {}).get("Statement") or {}).get("OrStatement") or {}
    ).get("Statements") or []
    matches = []
    for statement in statements:
        byte_match = statement.get("ByteMatchStatement") or {}
        header = (byte_match.get("FieldToMatch") or {}).get("SingleHeader") or {}
        if str(header.get("Name", "")).lower() == ORIGIN_VERIFY_HEADER_NAME:
            matches.append(byte_match)
    return matches


def set_web_acl_origin_verify_values(rules: Iterable[dict], values: Sequence[str]) -> Tuple[int, bool]:
    """Set each rate-based rule's origin verification matches to ``values``, in order.

    Returns (number of rules carrying the matches, whether anything changed). ``rules`` is the
    ``Rules`` list of a GetWebACL response and is modified in place.
    """
    found = 0
    changed = False
    for rule in rules:
        matches = _origin_verify_matches(rule)
        if len(matches) != len(values):
            continue
        found += 1
        for byte_match, value in zip(matches, values):
            wanted = value.encode("utf-8")
            current = byte_match.get("SearchString")
            if isinstance(current, str):
                current = current.encode("utf-8")
            if current != wanted:
                byte_match["SearchString"] = wanted
                changed = True
    return found, changed


def set_distribution_origin_verify_value(distribution_config: dict, value: str) -> Tuple[int, bool]:
    """Set every origin custom header named ``ORIGIN_VERIFY_HEADER_NAME`` to ``value``.

    Returns (number of headers found, whether anything changed). ``distribution_config`` is the
    ``DistributionConfig`` of a GetDistributionConfig response and is modified in place.
    """
    found = 0
    changed = False
    for origin in (distribution_config.get("Origins") or {}).get("Items") or []:
        for header in (origin.get("CustomHeaders") or {}).get("Items") or []:
            if str(header.get("HeaderName", "")).lower() != ORIGIN_VERIFY_HEADER_NAME:
                continue
            found += 1
            if header.get("HeaderValue") != value:
                header["HeaderValue"] = value
                changed = True
    return found, changed


def parse_web_acl_arn(arn: str) -> Tuple[str, str, str]:
    """(Name, Id, Scope) of a WAFv2 web ACL ARN.

    The ARN has the form ``arn:<partition>:wafv2:<region>:<account>:<scope>/webacl/<name>/<id>``.
    """
    parts = arn.split(":", 5)
    resource = parts[5].split("/") if len(parts) == 6 else []
    if len(resource) != 4 or resource[1] != "webacl":
        raise RotationError("ORIGIN_VERIFY_WEB_ACL_ARN is not a web ACL ARN")
    scope = "REGIONAL" if resource[0] == "regional" else "CLOUDFRONT"
    return resource[2], resource[3], scope


def update_web_acl_arguments(web_acl: dict, lock_token: str, scope: str, members: Iterable[str]) -> dict:
    """UpdateWebACL arguments that keep every setting of ``web_acl``.

    UpdateWebACL replaces the whole web ACL, and a setting it is not given reverts to its default, so
    every member of the request shape that GetWebACL returned is passed back.
    """
    arguments = {
        key: web_acl[key] for key in members if key in web_acl and key not in ("Scope", "LockToken")
    }
    arguments.update(Name=web_acl["Name"], Id=web_acl["Id"], Scope=scope, LockToken=lock_token)
    return arguments


# ---------------------------------------------------------------------------------------------
# Service updates
# ---------------------------------------------------------------------------------------------


def _require_distribution_deployed() -> None:
    status = cloudfront_client.get_distribution(Id=DISTRIBUTION_ID)["Distribution"]["Status"]
    if status != "Deployed":
        raise RotationError(
            f"Distribution {DISTRIBUTION_ID} is {status}, and rotation waits until an earlier change "
            "has reached every edge location"
        )


def _update_web_acl(values: Sequence[str]) -> bool:
    """Set the regional web ACL's origin verification matches. Returns whether it changed."""
    name, acl_id, scope = parse_web_acl_arn(WEB_ACL_ARN)
    waf = waf_client
    members = waf.meta.service_model.operation_model("UpdateWebACL").input_shape.members.keys()
    for attempt in range(1, WAF_LOCK_ATTEMPTS + 1):
        response = waf.get_web_acl(Name=name, Scope=scope, Id=acl_id)
        web_acl = response["WebACL"]
        found, changed = set_web_acl_origin_verify_values(web_acl.get("Rules") or [], values)
        if found == 0:
            # A WAF policy without rate-based rules has no origin verification match to update.
            logger.info(f"Web ACL {name} has no rate-based rule with an origin verification match")
            return False
        if not changed:
            return False
        try:
            waf.update_web_acl(
                **update_web_acl_arguments(web_acl, response["LockToken"], scope, members)
            )
            logger.info(f"Updated the origin verification matches of web ACL {name}")
            return True
        except ClientError as e:
            if _error_code(e) == "WAFOptimisticLockException" and attempt < WAF_LOCK_ATTEMPTS:
                continue
            raise
    return False


def _update_distribution(value: str) -> bool:
    """Point the distribution's origin verification headers at ``value``. Returns whether it changed."""
    cloudfront = cloudfront_client
    response = cloudfront.get_distribution_config(Id=DISTRIBUTION_ID)
    distribution_config = response["DistributionConfig"]
    found, changed = set_distribution_origin_verify_value(distribution_config, value)
    if found == 0:
        raise RotationError(
            f"No origin of distribution {DISTRIBUTION_ID} carries the {ORIGIN_VERIFY_HEADER_NAME} header"
        )
    if not changed:
        return False
    cloudfront.update_distribution(
        Id=DISTRIBUTION_ID, IfMatch=response["ETag"], DistributionConfig=distribution_config
    )
    logger.info(f"Updated {found} origin verification header(s) of distribution {DISTRIBUTION_ID}")
    return True


# ---------------------------------------------------------------------------------------------
# Rotation steps
# ---------------------------------------------------------------------------------------------


def _pending_values(secrets, secret_arn: str, token: str) -> Tuple[str, str]:
    pending = secrets.get_secret_value(SecretId=secret_arn, VersionId=token, VersionStage="AWSPENDING")
    values = values_from_secret_string(pending.get("SecretString"), (CURRENT_KEY, PREVIOUS_KEY))
    if len(values) != 2:
        raise RotationError("The pending version does not carry both a current and a previous value")
    return values[0], values[1]


def create_secret_step(secrets, secret_arn: str, token: str) -> None:
    current = secrets.get_secret_value(SecretId=secret_arn, VersionStage="AWSCURRENT")
    current_values = values_from_secret_string(current.get("SecretString"), (CURRENT_KEY,))
    if not current_values:
        raise RotationError("The current version carries no current value")
    try:
        secrets.get_secret_value(SecretId=secret_arn, VersionId=token, VersionStage="AWSPENDING")
        logger.info("createSecret: the pending version already exists")
        return
    except ClientError as e:
        if _error_code(e) != "ResourceNotFoundException":
            raise

    _require_distribution_deployed()
    new_value = secrets.get_random_password(PasswordLength=VALUE_LENGTH, ExcludePunctuation=True)[
        "RandomPassword"
    ]
    secrets.put_secret_value(
        SecretId=secret_arn,
        ClientRequestToken=token,
        SecretString=json.dumps({CURRENT_KEY: new_value, PREVIOUS_KEY: current_values[0]}),
        VersionStages=["AWSPENDING"],
    )
    logger.info("createSecret: wrote the pending version")


def set_secret_step(secrets, secret_arn: str, token: str) -> None:
    new_value, old_value = _pending_values(secrets, secret_arn, token)
    _apply_values(new_value, old_value)


def _apply_values(new_value: str, old_value: str) -> None:
    """Web ACL matches to [new, old], then (after the web ACL settles) the origin headers to new.

    Both updates are idempotent: each reads the current state first and changes nothing when it
    already holds the wanted values.
    """
    if WEB_ACL_ARN and _update_web_acl((new_value, old_value)):
        time.sleep(WAF_SETTLE_SECONDS)
    _update_distribution(new_value)


def verify_secret_step(secrets, secret_arn: str, token: str) -> None:
    logger.info("testSecret: no service-side check")


def finish_secret_step(secrets, secret_arn: str, token: str) -> None:
    metadata = secrets.describe_secret(SecretId=secret_arn)
    current_version = None
    for version, stages in (metadata.get("VersionIdsToStages") or {}).items():
        if "AWSCURRENT" in stages:
            if version == token:
                logger.info("finishSecret: the version is already current")
                return
            current_version = version
            break
    # A stack update that lands between setSecret and this step resolves the web ACL matches and the
    # origin headers to the version that is still AWSCURRENT, which puts them back on the old value.
    # Apply the pending values again before promoting; this changes nothing when no update landed.
    new_value, old_value = _pending_values(secrets, secret_arn, token)
    _apply_values(new_value, old_value)
    secrets.update_secret_version_stage(
        SecretId=secret_arn,
        VersionStage="AWSCURRENT",
        MoveToVersionId=token,
        RemoveFromVersionId=current_version,
    )
    logger.info("finishSecret: moved AWSCURRENT to the new version")


_STEPS = {
    "createSecret": create_secret_step,
    "setSecret": set_secret_step,
    "testSecret": verify_secret_step,
    "finishSecret": finish_secret_step,
}


def lambda_handler(event, context):
    secret_arn = event["SecretId"]
    token = event["ClientRequestToken"]
    step = event["Step"]
    if step not in _STEPS:
        raise RotationError(f"Unknown rotation step {step}")

    secrets = secrets_client
    metadata = secrets.describe_secret(SecretId=secret_arn)
    if not metadata.get("RotationEnabled"):
        raise RotationError("Rotation is not enabled for the secret")
    stages = (metadata.get("VersionIdsToStages") or {}).get(token)
    if stages is None:
        raise RotationError("The rotation version has no stage")
    if "AWSCURRENT" in stages:
        logger.info(f"{step}: the version is already current")
        return
    if "AWSPENDING" not in stages:
        raise RotationError("The rotation version is not pending")

    logger.info(f"{step}: start")
    _STEPS[step](secrets, secret_arn, token)
