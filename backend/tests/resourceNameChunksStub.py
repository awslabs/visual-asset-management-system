# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A fake Parameter Store for counting the calls a handler's cold start makes, with the resource-name
chunk parameters published (infra resourceNameChunks.ts) or not.

``ChunkedParameterStore`` answers both shared lookups at once: ``GetParameters`` for the chunk
parameters ``<prefix>Chunks/<i>`` (one chunk carrying ``names``) that ``common/resourceNames.py``
reads, and ``GetParameter`` by full name for a per-key parameter under the prefix or any other
parameter (the ``aos/*``, web URL and map key parameters ``common/ssmParameters.py`` reads). Every call
is recorded. ``serve(monkeypatch, store)`` points the test's fresh resourceNames and ssmParameters
copies (the root conftest registers both for every test) at it and sets the prefix.
"""

import json
import sys

from botocore.exceptions import ClientError

PREFIX = "/vams-test/resourceNames"


def _client_error(code, operation):
    return ClientError({"Error": {"Code": code, "Message": code},
                        "ResponseMetadata": {"HTTPStatusCode": 400}}, operation)


def throttling(operation="GetParameters"):
    return _client_error("ThrottlingException", operation)


class ChunkedParameterStore:
    """``names`` in chunk 0 (``None``: no chunk published); ``parameters`` by full name. ``fault``,
    when given, runs before every call with the operation and the name(s), and may raise."""

    def __init__(self, names=None, parameters=None, fault=None, prefix=PREFIX):
        self.prefix = prefix
        self.names = None if names is None else dict(names)
        self.parameters = dict(parameters or {})
        self.fault = fault
        self.calls = []

    def _chunk(self):
        return json.dumps({"v": 1, "n": 1, "i": 0, "l": "layout1", "m": self.names}, separators=(",", ":"))

    def get_parameters(self, Names, **_):
        self.calls.append(("GetParameters", tuple(Names)))
        if self.fault is not None:
            self.fault("GetParameters", Names)
        chunk0 = f"{self.prefix}Chunks/0"
        found = [{"Name": chunk0, "Value": self._chunk(), "Type": "String"}] \
            if self.names is not None and chunk0 in Names else []
        return {"Parameters": found, "InvalidParameters": [n for n in Names if not found or n != chunk0]}

    def get_parameter(self, Name, **_):
        self.calls.append(("GetParameter", Name))
        if self.fault is not None:
            self.fault("GetParameter", Name)
        if Name not in self.parameters:
            raise _client_error("ParameterNotFound", "GetParameter")
        return {"Parameter": {"Name": Name, "Value": self.parameters[Name], "Type": "String"}}

    def get_parameters_by_path(self, Path, Recursive=False, MaxResults=10, NextToken=None, **_):
        self.calls.append(("GetParametersByPath", NextToken))
        under = sorted(n for n in self.parameters if n.startswith(f"{Path}/"))
        start = int(NextToken or 0)
        page = {"Parameters": [{"Name": n, "Value": self.parameters[n], "Type": "String"}
                               for n in under[start:start + MaxResults]]}
        if start + MaxResults < len(under):
            page["NextToken"] = str(start + MaxResults)
        return page

    def count(self, operation):
        return sum(1 for op, _ in self.calls if op == operation)

    def parameter_reads(self):
        """The names of every GetParameter call, in order."""
        return [name for op, name in self.calls if op == "GetParameter"]


def serve(monkeypatch, store):
    """Make the test's fresh common.resourceNames and common.ssmParameters read ``store``, under its
    prefix. Call before loading the handler by path."""
    monkeypatch.setenv("VAMS_RESOURCE_PARAM_PREFIX", store.prefix)
    for name in ("common.resourceNames", "common.ssmParameters"):
        monkeypatch.setattr(sys.modules[name], "_ssm_client", store)
    return store
