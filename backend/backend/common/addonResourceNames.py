# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Resource-name keys for the resources the optional add-ons create (``infra/lib/nestedStacks/addon/``).

Each attribute of ``AddonResourceKeys`` is a ``ResourceParamKey`` whose key suffix matches an entry in
``infra/common/addonResourceParamKeys.ts``; the add-on CDK stack publishes the value under the
deployment's resource-name prefix with ``publishAddonResourceName``, and an add-on handler resolves it
with ``get_resource_name`` like any core key. ``infra/test/storage/resourceNamesMirror.test.ts`` keeps
the two files in step. Upstream-owned.

Key suffixes under ``addon/`` are recommended so a later core key does not collide with one declared
here; an exact duplicate of a core or custom key fails at synth.
"""

from common.resourceNames import ResourceParamKey  # noqa: F401


class AddonResourceKeys:
    pass
