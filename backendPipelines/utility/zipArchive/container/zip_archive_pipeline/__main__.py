# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Entry point: ``python3 -m zip_archive_pipeline '<definition JSON>'`` (the Batch job command)."""

import sys

from .runner import main

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 -m zip_archive_pipeline '<definition JSON>'")
        sys.exit(2)
    sys.exit(main(sys.argv))
