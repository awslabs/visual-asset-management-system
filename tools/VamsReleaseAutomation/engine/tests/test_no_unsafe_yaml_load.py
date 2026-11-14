# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""No Python file of the tool calls the PyYAML `load` family.

Static analysis (bandit B506, the AppSec scanner) flags every `load` call that passes a custom loader class, even
when the class is a `SafeLoader` subclass that only adds the CloudFormation tags. The tool therefore builds the
loader instance itself (`Loader(text).get_single_data()`, then `dispose()`) and uses `safe_load` elsewhere, so no
call needs a suppression.
"""

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[2]
# built from parts so this file does not match its own scan
UNSAFE = re.compile(r"\byaml\s*\.\s*(" + "|".join(("load", "load_all", "unsafe_" + "load", "full_" + "load")) + r")\s*\(")
SKIP_DIRS = {"node_modules", "__pycache__", ".pytest_cache", ".vra", "cdk.out", ".venv"}


def test_no_call_of_the_pyyaml_load_family():
    hits = []
    for path in sorted(PKG.rglob("*.py")):
        if SKIP_DIRS & set(path.relative_to(PKG).parts):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if UNSAFE.search(line):
                hits.append(f"{path.relative_to(PKG)}:{n}: {line.strip()}")
    assert not hits, "use yaml.safe_load, or a SafeLoader instance with get_single_data():\n" + "\n".join(hits)
