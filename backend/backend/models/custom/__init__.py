# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here do
not conflict on upgrade.

The model modules a fork adds to this package are fork-owned too: request and response models for
the handlers in ``handlers/custom/``, written in Pydantic v1 like every VAMS model
(``backend/CLAUDE.md``).
"""
