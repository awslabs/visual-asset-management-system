# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here do
not conflict on upgrade.

The handler modules a fork adds to this package are fork-owned too. Each is a module
``handlers/custom/<name>.py`` with a ``lambda_handler`` entry point, referenced from CDK as
``handlers.custom.<name>.lambda_handler``. Follow the handler pattern in
``backend/HANDLER_TEMPLATES.md`` (both Casbin tiers, fail-closed).
"""
