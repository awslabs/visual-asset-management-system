# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here do
not conflict on upgrade.

Fork-owned backend definitions that core VAMS splices in: API routes (``customApiRoutes.py``),
resource-name keys (``customResourceNames.py``) and constants (``customConstants.py``: permission
object types, constraint fields and upload deny-list entries). Fork handlers live in
``handlers/custom/`` and fork models in ``models/custom/``. See the "Extending VAMS in a fork"
developer guide.
"""
