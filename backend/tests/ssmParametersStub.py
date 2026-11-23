# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Test doubles for ``common/ssmParameters.py``, the bounded, cached single-parameter lookup.

A handler binds ``get_parameter_value`` when it is imported, so the helper state it reads (cache,
back-off window, lazily built client) belongs to whichever copy of the module was in ``sys.modules``
at that moment, and it outlives the test that filled it. These helpers give a test its own copy:

* ``load_ssm_parameters(client)`` loads a private copy of the shipped module whose reads go to
  ``client`` (anything answering ``get_parameter(Name=...)``), with an empty cache and no window.
* ``serve_parameters_from(module, client)`` points a loaded handler module's ``get_parameter_value``
  at such a copy and returns the copy, so the test can inspect it.

``ParameterStoreStub`` is a small fake client: named values, scripted errors per call, a ``fault``
hook, and the names of every call it answered. ``FakeClock`` and ``install_clock`` drive a helper's
read budget, cache, back-off window and sleeps without real time passing.
"""

import importlib.util
import itertools
import os

from botocore.exceptions import ClientError

SSM_PARAMETERS_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "backend", "common", "ssmParameters.py"))

_copies = itertools.count()


def load_ssm_parameters(client=None):
    """A private copy of the shipped common/ssmParameters.py. With ``client`` every read goes to it;
    without one the copy builds its bounded boto3 client on first read, as deployed."""
    spec = importlib.util.spec_from_file_location(f"ssmParameters_under_test_{next(_copies)}", SSM_PARAMETERS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if client is not None:
        module._ssm_client = client
    return module


def serve_parameters_from(module, client):
    """Point ``module.get_parameter_value`` at a private helper copy that reads ``client``. Use it on a
    handler module the test loaded itself (by path); patch a shared one with ``monkeypatch.setattr``."""
    helper = load_ssm_parameters(client)
    module.get_parameter_value = helper.get_parameter_value
    return helper


def client_error(code, operation="GetParameter", status=400):
    return ClientError({"Error": {"Code": code, "Message": code},
                        "ResponseMetadata": {"HTTPStatusCode": status}}, operation)


def throttled():
    return client_error("ThrottlingException")


class FakeClock:
    """Monotonic time that moves only when the code sleeps or a call is charged time."""

    def __init__(self, now=1000.0):
        self.now = now
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.sleeps.append((self.now, seconds))
        self.now += seconds

    def advance(self, seconds):
        self.now += seconds


def install_clock(helper, clock, monkeypatch=None):
    """Drive a helper module's read budget, cache, back-off window and sleeps from ``clock``, with the
    jitter at its upper bound. Pass ``monkeypatch`` for a module shared with other tests."""
    replacements = {"_monotonic": clock.monotonic, "_sleep": clock.sleep, "_uniform": lambda low, high: high}
    for name, value in replacements.items():
        if monkeypatch is None:
            setattr(helper, name, value)
        else:
            monkeypatch.setattr(helper, name, value)
    return clock


class ParameterStoreStub:
    """Answers ``get_parameter(Name=...)`` from ``values``. ``errors`` maps a name to a list of
    exceptions (or exception factories) raised by its next calls, in order; ``fault(name)``, when
    given, runs before every call and may raise. A name with no value and no scripted error raises
    ParameterNotFound. ``call_seconds`` is charged to ``clock`` per call, before any error, as a call
    that times out takes its time first. ``calls`` lists the name of every call and
    ``call_arguments`` the other arguments of each (``WithDecryption``, for one)."""

    def __init__(self, values=None, errors=None, fault=None, clock=None, call_seconds=0.0):
        self.values = dict(values or {})
        self.errors = {name: list(script) for name, script in (errors or {}).items()}
        self.fault = fault
        self.clock = clock
        self.call_seconds = call_seconds
        self.calls = []
        self.call_arguments = []

    def get_parameter(self, Name, **kwargs):
        self.calls.append(Name)
        self.call_arguments.append(kwargs)
        if self.clock is not None and self.call_seconds:
            self.clock.advance(self.call_seconds)
        if self.fault is not None:
            self.fault(Name)
        script = self.errors.get(Name)
        if script:
            error = script.pop(0)
            raise error() if callable(error) else error
        if Name not in self.values:
            raise client_error("ParameterNotFound")
        return {"Parameter": {"Name": Name, "Type": "String", "Value": self.values[Name]}}


def always(make_error):
    """A ``fault`` that raises ``make_error()`` on every call."""
    def fault(name):
        raise make_error()
    return fault
