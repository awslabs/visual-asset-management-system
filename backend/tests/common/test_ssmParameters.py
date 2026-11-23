# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""``common/ssmParameters.py``: bounded, cached single-parameter reads under throttling (#1820).

Parameter Store throughput is shared by every application in an account and Region. A read outside
the resource-name resolver used to get Rule 6's client (five adaptive attempts, 60 s timeouts) and
no cache, so a throttled read waited about 30 s, and every request or cold container paid it again.
The helper bounds a read to ``PARAMETER_READ_BUDGET_SECONDS``, backs off for the whole container
after a transient failure, caches values per container, and classifies errors as the resolver does.

Each test loads a private copy of the shipped module (``tests/ssmParametersStub.py``) with a fake
client and a fake clock, so no state leaks between tests.
"""

import ast
import sys
import threading
from unittest.mock import patch

import pytest
from botocore.exceptions import ReadTimeoutError

from backend.tests.ssmParametersStub import (
    SSM_PARAMETERS_PATH, FakeClock, ParameterStoreStub, always, client_error, install_clock,
    load_ssm_parameters, throttled,
)

NAME = "/vams-test/aos/endPoint"
OTHER = "/vams-test/web/deployedUrl"
THIRD = "/vams-test/location/apiKeyArn"


class RecordingLogger:
    """Records (level, message) for every call, at every level safeLogger exposes."""

    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        if level in ("debug", "info", "warning", "warn", "error", "critical", "exception"):
            return lambda message, *args, **kwargs: self.lines.append((level, str(message)))
        raise AttributeError(level)

    def at(self, *levels):
        return [message for level, message in self.lines if level in levels]


class CountingLock:
    """Wraps a lock and counts every acquisition."""

    def __init__(self, lock):
        self._lock = lock
        self.acquired = 0

    def __enter__(self):
        self.acquired += 1
        return self._lock.__enter__()

    def __exit__(self, *exc):
        return self._lock.__exit__(*exc)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def make_helper(clock):
    """A private helper copy reading ``client``, driven by the fake clock, logging to a recorder."""

    def make(client):
        helper = load_ssm_parameters(client)
        install_clock(helper, clock)
        helper.logger = RecordingLogger()
        return helper

    return make


def window_left(helper, clock):
    return helper._retry_after - clock.now


@pytest.mark.unit
class TestReadBudget:
    """R1: a read gives up inside PARAMETER_READ_BUDGET_SECONDS plus at most one call's timeout."""

    def test_constant_throttling_gives_up_inside_the_budget(self, make_helper, clock):
        stub = ParameterStoreStub(fault=always(throttled))
        helper = make_helper(stub)
        started = clock.now

        with pytest.raises(helper.ParameterUnavailableError) as refused:
            helper.get_parameter_value(NAME)

        assert refused.value.__cause__ is not None
        assert len(stub.calls) > 1, "the throttled read was never retried"
        assert clock.now - started <= helper.PARAMETER_READ_BUDGET_SECONDS
        deadline = started + helper.PARAMETER_READ_BUDGET_SECONDS
        assert clock.sleeps, "no retry sleep ran"
        for slept_at, seconds in clock.sleeps:
            assert seconds <= helper.READ_RETRY_MAX_SLEEP_SECONDS
            assert slept_at + seconds < deadline, "a retry sleep ran past the read budget"

    def test_an_endpoint_that_never_answers_costs_at_most_the_budget_plus_one_timeout(self, make_helper, clock):
        read_timeout = load_ssm_parameters().parameter_client_config.read_timeout
        stub = ParameterStoreStub(fault=always(lambda: ReadTimeoutError(endpoint_url="https://ssm.example.invalid/")),
                                  clock=clock, call_seconds=read_timeout)
        helper = make_helper(stub)
        started = clock.now

        with pytest.raises(helper.ParameterUnavailableError) as refused:
            helper.get_parameter_value(NAME)

        assert isinstance(refused.value.__cause__, ReadTimeoutError)
        assert len(stub.calls) >= 2, "a read timeout was not retried"
        assert clock.now - started <= helper.PARAMETER_READ_BUDGET_SECONDS + read_timeout

    def test_a_short_throttle_completes_the_read(self, make_helper):
        stub = ParameterStoreStub({NAME: "https://search.example"}, errors={NAME: [throttled, throttled]})
        helper = make_helper(stub)

        assert helper.get_parameter_value(NAME) == "https://search.example"
        assert stub.calls == [NAME, NAME, NAME]
        assert helper._failed_reads == 0


@pytest.mark.unit
class TestReadClient:
    """R1: the client gives up a call quickly and leaves the retries to the read loop. Search, the
    indexers and the secure-config handler built theirs with Rule 6's dict: 60 s timeouts, six attempts."""

    def test_the_client_has_short_timeouts_and_one_attempt(self, monkeypatch):
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        config = load_ssm_parameters()._get_ssm_client().meta.config
        assert config.connect_timeout == 1
        assert config.read_timeout == 2
        assert config.retries.get("total_max_attempts") == 1

    def test_the_client_is_built_once_per_container(self, monkeypatch):
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        helper = load_ssm_parameters()
        assert helper._get_ssm_client() is helper._get_ssm_client()


@pytest.mark.unit
class TestBackoffWindow:
    """R2: one failed read per back-off window, for every parameter of the container."""

    def test_after_one_throttled_read_other_names_are_refused_without_a_call(self, make_helper, clock):
        stub = ParameterStoreStub({OTHER: "https://web.example", THIRD: "arn"}, fault=always(throttled))
        helper = make_helper(stub)
        with pytest.raises(helper.ParameterUnavailableError):
            helper.get_parameter_value(NAME)
        calls, sleeps = len(stub.calls), len(clock.sleeps)

        for name in (OTHER, THIRD, NAME):
            with pytest.raises(helper.ParameterUnavailableError):
                helper.get_parameter_value(name)

        assert len(stub.calls) == calls, "a lookup inside the back-off window read Parameter Store"
        assert len(clock.sleeps) == sleeps, "a refused lookup slept"

    def test_a_stale_cached_value_is_served_inside_the_window(self, make_helper, clock):
        stub = ParameterStoreStub({NAME: "https://search.example"})
        helper = make_helper(stub)
        assert helper.get_parameter_value(NAME, ttl_seconds=60) == "https://search.example"
        clock.advance(61)
        stub.fault = always(throttled)

        assert helper.get_parameter_value(NAME, ttl_seconds=60) == "https://search.example"
        assert window_left(helper, clock) > 0
        calls = len(stub.calls)
        assert helper.get_parameter_value(NAME, ttl_seconds=60) == "https://search.example"
        assert len(stub.calls) == calls

    def test_the_window_doubles_from_two_seconds_to_thirty_and_a_success_resets_it(self, make_helper, clock):
        stub = ParameterStoreStub({NAME: "https://search.example"}, fault=always(throttled))
        helper = make_helper(stub)
        windows = []
        for _ in range(6):
            with pytest.raises(helper.ParameterUnavailableError):
                helper.get_parameter_value(NAME)
            windows.append(window_left(helper, clock))
            clock.advance(windows[-1])

        assert windows == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]

        stub.fault = None
        assert helper.get_parameter_value(NAME) == "https://search.example"
        assert helper._failed_reads == 0
        stub.fault = always(throttled)
        with pytest.raises(helper.ParameterUnavailableError):
            helper.get_parameter_value(OTHER)
        assert window_left(helper, clock) == 2.0

    def test_the_window_is_jittered_between_half_and_all_of_its_length(self, clock):
        # Each call is charged time, since the jitter below makes every retry sleep zero.
        helper = load_ssm_parameters(ParameterStoreStub(fault=always(throttled), clock=clock, call_seconds=0.5))
        install_clock(helper, clock)
        bounds = []
        helper._uniform = lambda low, high: bounds.append((low, high)) or low
        helper.logger = RecordingLogger()

        with pytest.raises(helper.ParameterUnavailableError):
            helper.get_parameter_value(NAME)

        assert (0.5, 1.0) in bounds
        assert window_left(helper, clock) == pytest.approx(1.0)

    def test_failures_log_one_error_per_read_and_one_warning_per_window_and_never_a_value(self, make_helper, clock):
        stub = ParameterStoreStub({OTHER: "https://secret-looking.example"}, fault=always(throttled))
        helper = make_helper(stub)
        with pytest.raises(helper.ParameterUnavailableError):
            helper.get_parameter_value(NAME)
        for name in (OTHER, THIRD, OTHER):
            with pytest.raises(helper.ParameterUnavailableError):
                helper.get_parameter_value(name)

        errors = helper.logger.at("error")
        warnings = helper.logger.at("warning")
        assert len(errors) == 1 and "ThrottlingException" in errors[0] and NAME in errors[0]
        assert "calls" in errors[0]
        assert len(warnings) == 1, warnings
        assert all("secret-looking" not in line for _, line in helper.logger.lines)


@pytest.mark.unit
class TestRefusalsARetryCannotFix:
    """R2: a non-retryable refusal fails at once and opens no window; a missing parameter is a KeyError."""

    def test_access_denied_makes_one_call_and_opens_no_window(self, make_helper, clock):
        stub = ParameterStoreStub({OTHER: "https://web.example"},
                                  errors={NAME: [lambda: client_error("AccessDeniedException")]})
        helper = make_helper(stub)

        with pytest.raises(helper.ParameterReadError) as refused:
            helper.get_parameter_value(NAME)

        assert not isinstance(refused.value, KeyError)
        assert stub.calls == [NAME]
        assert clock.sleeps == []
        assert helper._failed_reads == 0
        assert helper.get_parameter_value(OTHER) == "https://web.example"

    def test_a_refused_refresh_serves_the_cached_value(self, make_helper, clock):
        stub = ParameterStoreStub({NAME: "https://search.example"})
        helper = make_helper(stub)
        assert helper.get_parameter_value(NAME, ttl_seconds=60) == "https://search.example"
        clock.advance(61)
        stub.errors[NAME] = [lambda: client_error("AccessDeniedException")]

        assert helper.get_parameter_value(NAME, ttl_seconds=60) == "https://search.example"

    def test_a_missing_parameter_is_a_key_error_remembered_for_five_minutes(self, make_helper, clock):
        stub = ParameterStoreStub()
        helper = make_helper(stub)

        with pytest.raises(KeyError):
            helper.get_parameter_value(NAME)
        with pytest.raises(helper.ParameterNotFoundError):
            helper.get_parameter_value(NAME)
        assert stub.calls == [NAME]
        assert helper._failed_reads == 0

        clock.advance(helper.MISSING_PARAMETER_TTL_SECONDS + 1)
        stub.values[NAME] = "https://search.example"
        assert helper.get_parameter_value(NAME) == "https://search.example"
        assert stub.calls == [NAME, NAME]

    def test_a_parameter_deleted_after_it_was_cached_is_reported_missing(self, make_helper, clock):
        stub = ParameterStoreStub({NAME: "https://search.example"})
        helper = make_helper(stub)
        assert helper.get_parameter_value(NAME, ttl_seconds=60) == "https://search.example"
        clock.advance(61)
        del stub.values[NAME]

        with pytest.raises(helper.ParameterNotFoundError):
            helper.get_parameter_value(NAME, ttl_seconds=60)

    def test_a_name_is_required(self, make_helper):
        stub = ParameterStoreStub()
        helper = make_helper(stub)
        for read in (helper.get_parameter_value, helper.read_parameter_value):
            with pytest.raises(ValueError):
                read("")
        assert stub.calls == []


@pytest.mark.unit
class TestOneReadAtATime:
    """R4: concurrent lookups of a cold parameter in one container share one read."""

    def test_two_threads_on_a_cold_name_make_one_call(self, make_helper):
        entered, release = threading.Event(), threading.Event()

        def hold_first_call(name):
            if not entered.is_set():
                entered.set()
                assert release.wait(5), "the test never released the first call"

        stub = ParameterStoreStub({NAME: "https://search.example"}, fault=hold_first_call)
        helper = make_helper(stub)
        results = {}

        def look_up(label):
            results[label] = helper.get_parameter_value(NAME)

        first = threading.Thread(target=look_up, args=("first",))
        first.start()
        assert entered.wait(5), "the first lookup never reached Parameter Store"
        second = threading.Thread(target=look_up, args=("second",))
        second.start()
        # Long enough for the second lookup to make a call of its own if nothing stops it.
        second.join(0.3)
        release.set()
        first.join(5)
        second.join(5)

        assert not first.is_alive() and not second.is_alive()
        assert results == {"first": "https://search.example", "second": "https://search.example"}
        assert stub.calls == [NAME]


@pytest.mark.unit
class TestWarmPath:
    """R4: a cached value costs no call and no lock."""

    def test_a_thousand_cached_reads_make_no_call_and_take_no_lock(self, make_helper):
        stub = ParameterStoreStub({NAME: "https://search.example"})
        helper = make_helper(stub)
        assert helper.get_parameter_value(NAME) == "https://search.example"
        lock = CountingLock(helper._read_lock)
        helper._read_lock = lock

        for _ in range(1000):
            assert helper.get_parameter_value(NAME) == "https://search.example"

        assert stub.calls == [NAME]
        assert lock.acquired == 0

    def test_a_value_is_read_again_after_its_ttl(self, make_helper, clock):
        stub = ParameterStoreStub({NAME: "first"})
        helper = make_helper(stub)
        assert helper.get_parameter_value(NAME, ttl_seconds=300) == "first"
        stub.values[NAME] = "second"
        clock.advance(299)
        assert helper.get_parameter_value(NAME, ttl_seconds=300) == "first"
        clock.advance(2)
        assert helper.get_parameter_value(NAME, ttl_seconds=300) == "second"
        assert stub.calls == [NAME, NAME]

    def test_the_default_ttl_is_an_hour(self):
        assert load_ssm_parameters().CACHE_TTL_SECONDS == 3600


@pytest.mark.unit
class TestReadWithoutCache:
    """read_parameter_value reads now, bounded and classified the same way, sharing the window."""

    def test_every_read_calls_and_nothing_is_cached(self, make_helper):
        stub = ParameterStoreStub({NAME: "first"})
        helper = make_helper(stub)
        assert helper.read_parameter_value(NAME) == "first"
        stub.values[NAME] = "second"
        assert helper.read_parameter_value(NAME) == "second"
        assert stub.calls == [NAME, NAME]
        assert NAME not in helper._cache

    def test_a_cached_value_is_never_served(self, make_helper, clock):
        stub = ParameterStoreStub({NAME: "first"})
        helper = make_helper(stub)
        assert helper.get_parameter_value(NAME) == "first"
        stub.fault = always(throttled)

        with pytest.raises(helper.ParameterUnavailableError):
            helper.read_parameter_value(NAME)

    def test_a_transient_failure_opens_the_shared_window(self, make_helper):
        stub = ParameterStoreStub({OTHER: "https://web.example"}, errors={NAME: [throttled] * 100})
        helper = make_helper(stub)
        with pytest.raises(helper.ParameterUnavailableError):
            helper.read_parameter_value(NAME)
        calls = len(stub.calls)

        with pytest.raises(helper.ParameterUnavailableError):
            helper.get_parameter_value(OTHER)
        with pytest.raises(helper.ParameterUnavailableError):
            helper.read_parameter_value(OTHER)
        assert len(stub.calls) == calls

    def test_a_missing_parameter_is_a_key_error_and_not_remembered(self, make_helper):
        stub = ParameterStoreStub()
        helper = make_helper(stub)
        with pytest.raises(helper.ParameterNotFoundError):
            helper.read_parameter_value(NAME)
        stub.values[NAME] = "now-published"
        assert helper.get_parameter_value(NAME) == "now-published"

    def test_access_denied_opens_no_window(self, make_helper):
        stub = ParameterStoreStub({OTHER: "https://web.example"},
                                  errors={NAME: [lambda: client_error("AccessDeniedException")]})
        helper = make_helper(stub)
        with pytest.raises(helper.ParameterReadError):
            helper.read_parameter_value(NAME)
        assert helper.read_parameter_value(OTHER) == "https://web.example"

    def test_concurrent_reads_run_one_at_a_time_and_open_one_window(self, make_helper):
        """Two threads reading while Parameter Store throttles: the second waits for the first read,
        finds the window it opened, and is refused without a call, so the failure is counted once."""
        entered, release = threading.Event(), threading.Event()

        def hold_first_call_then_throttle(name):
            if not entered.is_set():
                entered.set()
                assert release.wait(5), "the test never released the first call"
            raise throttled()

        stub = ParameterStoreStub(fault=hold_first_call_then_throttle)
        helper = make_helper(stub)
        results = {}

        def read(label, name):
            try:
                results[label] = helper.read_parameter_value(name)
            except Exception as e:  # recorded for the assertions below
                results[label] = e

        first = threading.Thread(target=read, args=("first", NAME))
        first.start()
        assert entered.wait(5), "the first read never reached Parameter Store"
        second = threading.Thread(target=read, args=("second", OTHER))
        second.start()
        # Long enough for the second read to make a call of its own if nothing stops it.
        second.join(0.3)
        calls_while_held = list(stub.calls)
        release.set()
        first.join(5)
        second.join(5)

        assert not first.is_alive() and not second.is_alive()
        assert calls_while_held == [NAME], "a second read ran while the first was in progress"
        assert isinstance(results["first"], helper.ParameterUnavailableError)
        assert isinstance(results["second"], helper.ParameterUnavailableError)
        assert OTHER not in stub.calls
        assert helper._failed_reads == 1

    def test_a_read_and_a_cached_lookup_share_the_lock(self, make_helper):
        entered, release = threading.Event(), threading.Event()

        def hold_first_call(name):
            if not entered.is_set():
                entered.set()
                assert release.wait(5), "the test never released the first call"

        stub = ParameterStoreStub({NAME: "now", OTHER: "cold"}, fault=hold_first_call)
        helper = make_helper(stub)
        results = {}

        first = threading.Thread(target=lambda: results.update(first=helper.read_parameter_value(NAME)))
        first.start()
        assert entered.wait(5), "the first read never reached Parameter Store"
        second = threading.Thread(target=lambda: results.update(second=helper.get_parameter_value(OTHER)))
        second.start()
        second.join(0.3)
        calls_while_held = list(stub.calls)
        release.set()
        first.join(5)
        second.join(5)

        assert calls_while_held == [NAME]
        assert results == {"first": "now", "second": "cold"}
        assert stub.calls == [NAME, OTHER]


@pytest.mark.unit
class TestClassificationIsTheResolvers:
    """One error classification for the resolver and this helper, so a change to it (draft PR #1832
    makes more transport errors transient) applies to both."""

    def test_the_helper_uses_the_resolver_functions(self):
        resolver = sys.modules["common.resourceNames"]
        with patch.dict(sys.modules, {"common.resourceNames": resolver}):
            helper = load_ssm_parameters()
        assert helper._is_retryable is resolver._is_retryable
        assert helper._error_code is resolver._error_code

    def test_the_module_imports_only_the_two_classification_functions_and_defines_no_codes_of_its_own(self):
        """Port pin: release/2.6.8's resolver has these two functions and no chunk internals."""
        with open(SSM_PARAMETERS_PATH, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = {(node.module, alias.name) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    for alias in node.names if node.module == "common.resourceNames"}
        assert imported == {("common.resourceNames", "_error_code"), ("common.resourceNames", "_is_retryable")}
        assigned = {target.id for node in tree.body if isinstance(node, ast.Assign)
                    for target in node.targets if isinstance(target, ast.Name)}
        assert not {name for name in assigned if "RETRYABLE" in name}

    @pytest.mark.parametrize("error, transient", [
        (throttled(), True),
        (client_error("InternalServerError", status=500), True),
        (client_error("SomethingNew", status=503), True),
        (ReadTimeoutError(endpoint_url="https://ssm.example.invalid/"), True),
        (client_error("AccessDeniedException"), False),
        (client_error("ValidationException"), False),
    ])
    def test_the_helper_retries_what_the_resolver_retries(self, make_helper, error, transient):
        assert sys.modules["common.resourceNames"]._is_retryable(error) is transient
        stub = ParameterStoreStub({NAME: "value"}, errors={NAME: [error]})
        helper = make_helper(stub)
        if transient:
            assert helper.get_parameter_value(NAME) == "value"
            assert stub.calls == [NAME, NAME]
        else:
            with pytest.raises(helper.ParameterReadError):
                helper.get_parameter_value(NAME)
            assert stub.calls == [NAME]


@pytest.mark.unit
class TestNoDecryption:
    """Plain String parameters only: nothing the helper caches can be a decrypted secret."""

    def test_reads_never_ask_for_decryption(self, make_helper):
        stub = ParameterStoreStub({NAME: "value"})
        helper = make_helper(stub)
        helper.get_parameter_value(NAME)
        helper.read_parameter_value(NAME)
        assert stub.calls == [NAME, NAME]
        assert stub.call_arguments == [{}, {}], "a read passed an argument other than Name"

    def test_the_source_never_mentions_decryption_as_an_argument(self):
        with open(SSM_PARAMETERS_PATH, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        keywords = {keyword.arg for node in ast.walk(tree) if isinstance(node, ast.Call) for keyword in node.keywords}
        assert "WithDecryption" not in keywords
