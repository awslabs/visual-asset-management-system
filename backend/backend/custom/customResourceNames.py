# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here do
not conflict on upgrade.

Resource-name keys for the resources a fork adds. Each attribute of ``CustomResourceKeys`` is a
``ResourceParamKey`` whose key suffix matches an entry in ``infra/common/customResourceParamKeys.ts``;
the custom CDK stack publishes the value under the deployment's resource-name prefix with
``publishCustomResourceName``, and a fork handler resolves it with ``get_resource_name`` like any
core key. ``infra/test/storage/resourceNamesMirror.test.ts`` keeps the two files in step.

Key suffixes under ``custom/`` are recommended so a later core key does not collide with one
declared here; an exact duplicate of a core key fails at synth.
"""

from common.resourceNames import ResourceParamKey  # noqa: F401


class CustomResourceKeys:
    pass
