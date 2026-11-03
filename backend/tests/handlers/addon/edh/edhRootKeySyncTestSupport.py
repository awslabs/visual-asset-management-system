# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The root key sync's Run Command document as the stack builds it, for the backend tests.

The stack builds the document's one step from ``backendAddons/edh/rootKeySync/read-root-key.sh``: comment and
blank lines dropped, the key file placeholder replaced with the configured cluster's key file. These helpers
build the same lines for the cluster id the EDH conftest sets, the document content Systems Manager returns
for them, and the SHA-256 the stack passes to the sync function.
"""

import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
DOCUMENT_SCRIPT = REPO_ROOT / "backendAddons" / "edh" / "rootKeySync" / "read-root-key.sh"
KEY_FILE_PLACEHOLDER = "__EDH_ROOT_KEY_FILE__"
# The cluster id the EDH conftest sets.
TEST_CLUSTER_ID = "edh-test"
# The publicKey parameter's allowedPattern, as the construct declares it (EDH_ROOT_KEY_PUBLIC_KEY_PATTERN).
PUBLIC_KEY_ALLOWED_PATTERN = "^[A-Za-z0-9+/]{1,1000}={0,2}$"


def step_lines(cluster_id: str = TEST_CLUSTER_ID) -> list:
    key_file = f"/opt/edh/{cluster_id}/cluster_manager/web_interface/keys/admin_api_key.txt"
    return [line.replace(KEY_FILE_PLACEHOLDER, key_file)
            for line in DOCUMENT_SCRIPT.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def step_sha256(cluster_id: str = TEST_CLUSTER_ID) -> str:
    return hashlib.sha256("\n".join(step_lines(cluster_id)).encode("utf-8")).hexdigest()


def document_content(lines=None, *, parameters=None, extra_steps=()) -> str:
    """The document's JSON content as Systems Manager returns it."""
    return json.dumps({
        "schemaVersion": "2.2",
        "description": "VAMS EDH root key sync",
        "parameters": parameters if parameters is not None else {
            "publicKey": {"type": "String", "description": "Base64 DER public key",
                          "allowedPattern": PUBLIC_KEY_ALLOWED_PATTERN},
        },
        "mainSteps": [{
            "action": "aws:runShellScript",
            "name": "encryptRootKey",
            "precondition": {"StringEquals": ["platformType", "Linux"]},
            "inputs": {"timeoutSeconds": "30", "runCommand": step_lines() if lines is None else lines},
        }, *extra_steps],
    })
