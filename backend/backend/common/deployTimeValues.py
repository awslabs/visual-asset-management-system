# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The deploy-time values VAMS creates in stacks built after the resource-name stack: the OpenSearch
endpoint and index names, the deployed web URL and the Amazon Location Service map API key ARN.

The deployment publishes each one under the resource-name prefix and in the resource-name chunk
parameters, only when its feature is deployed (infra ``lateCoreResourceNames.ts``), so
``get_resource_name()`` answers it from the names a cold start has already loaded, with no call of
its own. The parameters that held these values before (``aos/*``, ``web/deployedUrl`` and
``location/apiKeyArn`` beside the prefix) stay published. They are read only when the resource names
do not carry a key: during the deployment that adds the keys, while its stacks update, and on a
deployment whose chunk parameters predate them.

The keys mirror the ``openSearch``, ``web`` and ``location`` categories of
``infra/common/resourceParamKeys.ts`` (``tests/common/test_deployTimeValues.py`` compares them). None
takes an environment override: the ``*_SSM_PARAM`` variables a function carries hold parameter
names, never values.

Only a ``KeyError`` (the key is not published under the prefix) reads the legacy parameter. Any other
failure propagates: ``ResourceNameUnavailableError`` when Parameter Store could not be read, so a
throttled lookup adds no second read. The values are identifiers, not secrets; nothing here logs one.
"""

from common.resourceNames import ResourceParamKey, get_resource_name
from common.ssmParameters import CACHE_TTL_SECONDS as PARAMETER_CACHE_TTL_SECONDS
from common.ssmParameters import get_parameter_value
from customLogging.logger import safeLogger

logger = safeLogger()


class DeployTimeKeys:
    OPENSEARCH_ENDPOINT = ResourceParamKey("openSearch/endpoint", ())
    OPENSEARCH_ASSET_INDEX_NAME = ResourceParamKey("openSearch/assetIndexName", ())
    OPENSEARCH_FILE_INDEX_NAME = ResourceParamKey("openSearch/fileIndexName", ())
    WEB_DEPLOYED_URL = ResourceParamKey("web/deployedUrl", ())
    LOCATION_API_KEY_ARN = ResourceParamKey("location/apiKeyArn", ())


# The keys whose legacy read has been logged in this container: one INFO line per key, not per call.
_fallback_logged = set()


def _log_fallback_once(keys, legacy_parameter_names):
    label = ", ".join(key.param_key for key in keys)
    if label in _fallback_logged:
        return
    _fallback_logged.add(label)
    logger.info(f"{label} not among the resource names yet; reading {', '.join(legacy_parameter_names)}")


def resolve(key: ResourceParamKey, legacy_parameter_name: str = "", *,
            legacy_ttl_seconds: float = PARAMETER_CACHE_TTL_SECONDS) -> str:
    """The value of ``key`` from the resource names. When they do not carry it (``KeyError``) and a
    legacy parameter name is given, the value of that parameter, read through
    ``common.ssmParameters`` and cached for ``legacy_ttl_seconds``. Without a legacy name the
    ``KeyError`` is raised; a missing legacy parameter raises ``ParameterNotFoundError`` (a
    ``KeyError``). Every other error propagates as raised."""
    try:
        return get_resource_name(key)
    except KeyError:
        if not legacy_parameter_name:
            raise
    _log_fallback_once((key,), (legacy_parameter_name,))
    return get_parameter_value(legacy_parameter_name, ttl_seconds=legacy_ttl_seconds)


def resolve_together(keys, legacy_parameter_names, *,
                     legacy_ttl_seconds: float = PARAMETER_CACHE_TTL_SECONDS) -> list:
    """The values of ``keys``, which one stack publishes together (the OpenSearch endpoint and index
    names), all from the resource names or all from their legacy parameters
    (``legacy_parameter_names``, in the same order), so a caller never pairs a value of one
    deployment state with another's. The first key decides: when the resource names do not carry
    it, the legacy parameters are read and no other key is looked up, so a deployment still adding
    the keys costs one lookup that misses rather than one per key. A later key they do not carry also
    falls back to the legacy set. Errors propagate as in ``resolve()``; without a legacy name for
    every key the ``KeyError`` is raised."""
    keys = tuple(keys)
    legacy_parameter_names = tuple(legacy_parameter_names)
    if len(keys) != len(legacy_parameter_names):
        raise ValueError("resolve_together() takes one legacy parameter name per key")
    try:
        return [get_resource_name(key) for key in keys]
    except KeyError:
        if not all(legacy_parameter_names):
            raise
    _log_fallback_once(keys, legacy_parameter_names)
    return [get_parameter_value(name, ttl_seconds=legacy_ttl_seconds) for name in legacy_parameter_names]
