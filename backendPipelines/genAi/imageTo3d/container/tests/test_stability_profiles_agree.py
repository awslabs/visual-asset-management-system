#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The Stability AI profiles the job role is granted are exactly the ones the agent broker invokes.

The broker's OPERATIONS table and the construct's BLENDERAGENT_STABILITY_INFERENCE_PROFILES are two
copies of one list. A profile the broker calls but the role is not granted fails the edit with
AccessDeniedException inside a running agent job; a profile granted but never called is a standing
permission with no use.
"""

import os
import re

CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONSTRUCT = os.path.abspath(os.path.join(
    CONTAINER_DIR, "..", "..", "..", "..", "infra", "lib", "nestedStacks", "pipelines", "genAi",
    "imageTo3d", "constructs", "imageTo3d-construct.ts"))


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _broker_profiles():
    match = re.search(r"^OPERATIONS\s*=\s*\{([^}]*)\}",
                      _read(os.path.join(CONTAINER_DIR, "agent_broker.py")), re.M)
    assert match, "agent_broker.py declares no OPERATIONS"
    return set(re.findall(r':\s*"([^"]+)"', match.group(1)))


def _granted_profiles():
    match = re.search(r"BLENDERAGENT_STABILITY_INFERENCE_PROFILES = \[([^\]]*)\]", _read(CONSTRUCT))
    assert match, "imageTo3d-construct.ts declares no BLENDERAGENT_STABILITY_INFERENCE_PROFILES"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_both_lists_are_found():
    """Guards the comparison below: a regex that matched nothing would compare empty sets."""
    assert len(_broker_profiles()) >= 1
    assert len(_granted_profiles()) >= 1


def test_the_granted_profiles_are_the_invoked_profiles():
    broker, granted = _broker_profiles(), _granted_profiles()
    assert broker == granted, (
        f"invoked but not granted: {sorted(broker - granted)}; "
        f"granted but not invoked: {sorted(granted - broker)}")
