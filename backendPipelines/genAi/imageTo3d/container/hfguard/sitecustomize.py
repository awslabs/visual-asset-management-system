# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Installs the HuggingFace pin guard in an upstream model process at interpreter start.

``models.base.run_upstream`` puts this directory first on the upstream process's ``PYTHONPATH``, so
Python's ``site`` imports this module before the upstream code imports transformers or diffusers. The
directory holds only the guard, so no pipeline module can shadow an upstream one of the same name.
"""

import hf_pins

hf_pins.install()
