# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-phase wall-clock timings of one run, logged as each phase ends and summarised at the end."""

import time
from contextlib import contextmanager
from typing import Dict

from .logging import get_logger

logger = get_logger()


class PhaseTimer:
    def __init__(self):
        self.seconds: Dict[str, float] = {}

    @contextmanager
    def phase(self, name: str):
        started = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - started
            self.seconds[name] = self.seconds.get(name, 0.0) + elapsed
            logger.info(f"phase={name} seconds={elapsed:.2f}")

    def summary(self) -> str:
        total = sum(self.seconds.values())
        parts = " ".join(f"{name}={value:.2f}s" for name, value in self.seconds.items())
        return f"phase timings: {parts} total={total:.2f}s"
