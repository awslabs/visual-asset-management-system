# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stand-in module for `common.validators`.

The real module is pure (`re`, `json`, and the pure-constant `common.s3PathPatterns`), so this
mock loads it by path and re-exports it verbatim rather than re-implementing the rules. The
`validate()` dispatcher and the regex pattern constants must be the real ones: models resolve
`validate` through `sys.modules['common.validators']` at call time, so a re-implementation here
decides what every model accepts in the suite while the deployed handler uses the real rules.
Tests that need validation bypassed patch `validate` locally.
"""

import importlib.util
import os
import sys

_real_common_dir = os.path.join(
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ),
    'backend',
    'common',
)

# The autouse mock fixture re-executes this module before every test; cache the loaded real
# module under a private key so its regexes are compiled once per session.
_REAL_MODULE_CACHE_KEY = '_vams_real_common_validators'


def _load_real_module(module_name, file_name):
    spec = importlib.util.spec_from_file_location(
        module_name, os.path.join(_real_common_dir, file_name)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _real_validators():
    module = sys.modules.get(_REAL_MODULE_CACHE_KEY)
    if module is None:
        # validators.py imports these prefixes at module level.
        if 'common.s3PathPatterns' not in sys.modules:
            sys.modules['common.s3PathPatterns'] = _load_real_module(
                'common.s3PathPatterns', 's3PathPatterns.py'
            )
        module = _load_real_module('common.validators', 'validators.py')
        sys.modules[_REAL_MODULE_CACHE_KEY] = module
    return module


_real = _real_validators()

for _name in dir(_real):
    if not _name.startswith('_'):
        globals().setdefault(_name, getattr(_real, _name))

del _name
