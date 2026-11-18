"""`examples/seed_viewer_samples.py` downloads public samples politely (#1634): every request names the
script and the project URL in its User-Agent, as the sample hosts' bot policies ask, and an HTTP 429 is
retried after its Retry-After delay, a bounded number of times and never for longer than the script's cap.
"""

import datetime as dt
import email.message
import importlib.util
import io
import sys
import urllib.error
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture(scope="module")
def seed():
    sys.path.insert(0, str(EXAMPLES))
    try:
        spec = importlib.util.spec_from_file_location(
            "seed_viewer_samples_under_test", EXAMPLES / "seed_viewer_samples.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(EXAMPLES))


URL = "https://upload.wikimedia.org/wikipedia/commons/sample.png"


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, retry_after=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(URL, code, "Too Many Requests", headers, io.BytesIO(b""))


class _Opener:
    """Answers each call with the next of `answers` (an exception is raised, bytes are returned)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return _Response(answer)


class TestUserAgent:
    def test_names_the_script_and_the_project_url(self, seed):
        opener = _Opener(b"png")
        assert seed.fetch(URL, opener=opener, sleep=lambda s: None) == b"png"
        request, timeout = opener.requests[0]
        agent = request.get_header("User-agent")
        assert agent == seed.USER_AGENT
        assert agent.startswith("vams-seed-viewer-samples/")
        assert "https://github.com/awslabs/visual-asset-management-system" in agent
        assert timeout == seed.DOWNLOAD_TIMEOUT_SECONDS

    def test_download_uses_fetch_for_an_uncached_sample(self, seed, tmp_path, monkeypatch):
        import hashlib

        calls = []
        monkeypatch.setattr(seed, "fetch", lambda url: calls.append(url) or b"data")
        sample = {"id": "s1", "url": URL, "size": 4, "sha256": hashlib.sha256(b"data").hexdigest()}
        assert seed.download(sample, tmp_path) == b"data"
        assert calls == [URL]


class TestRateLimit:
    def test_a_429_is_retried_after_its_retry_after_seconds(self, seed):
        slept = []
        opener = _Opener(_http_error(429, "7"), b"png")
        assert seed.fetch(URL, opener=opener, sleep=slept.append) == b"png"
        assert slept == [7.0]
        assert len(opener.requests) == 2

    def test_an_http_date_retry_after_is_waited_until(self, seed):
        when = dt.datetime(2026, 11, 18, 12, 0, 30, tzinfo=dt.timezone.utc)
        now = dt.datetime(2026, 11, 18, 12, 0, 0, tzinfo=dt.timezone.utc)
        header = email.utils.format_datetime(when, usegmt=True)
        assert seed.retry_after_seconds(header, now=now) == 30.0
        assert seed.retry_after_seconds(header, now=when + dt.timedelta(seconds=5)) == 0.0

    @pytest.mark.parametrize("value", [None, "", "soon", "-3"])
    def test_a_missing_or_unreadable_retry_after_waits_the_default(self, seed, value):
        assert seed.retry_after_seconds(value) == seed.RETRY_AFTER_DEFAULT_SECONDS

    def test_retries_are_bounded(self, seed):
        slept = []
        opener = _Opener(*[_http_error(429, "1") for _ in range(seed.DOWNLOAD_ATTEMPTS)])
        with pytest.raises(seed.SeedError, match="still rate limited"):
            seed.fetch(URL, opener=opener, sleep=slept.append)
        assert len(opener.requests) == seed.DOWNLOAD_ATTEMPTS
        assert slept == [1.0] * (seed.DOWNLOAD_ATTEMPTS - 1)

    def test_a_wait_over_the_cap_stops_without_sleeping(self, seed):
        slept = []
        too_long = str(seed.RETRY_AFTER_MAX_SECONDS + 1)
        with pytest.raises(seed.SeedError, match="asked to wait"):
            seed.fetch(URL, opener=_Opener(_http_error(429, too_long)), sleep=slept.append)
        assert slept == []

    def test_another_http_error_is_not_retried(self, seed):
        opener = _Opener(_http_error(404))
        with pytest.raises(urllib.error.HTTPError) as raised:
            seed.fetch(URL, opener=opener, sleep=lambda s: None)
        assert raised.value.code == 404
        assert len(opener.requests) == 1

    def test_a_non_https_url_is_refused(self, seed):
        with pytest.raises(seed.SeedError, match="non-HTTPS"):
            seed.fetch("http://example.com/a.png", opener=_Opener(), sleep=lambda s: None)
