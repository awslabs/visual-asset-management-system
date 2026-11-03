# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Request and response models for the optional add-ons (``handlers/addon/``), written in Pydantic v1
like every VAMS model (``backend/CLAUDE.md``). Upstream-owned: one module per add-on, kept out of the
core ``models/`` package so an add-on model is never on the path of a core change.
"""
