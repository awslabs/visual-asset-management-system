# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The tools the CAD agent may call, and the run state they share.

Every tool is a thin, bounded wrapper: the sandbox runs scripts, ``cad_io`` validates geometry, the
research tools return trimmed text and record the sources they consulted, and ``finish`` records the
outcome the agent reports. The state object is what ``run.py`` reads after the agent returns, so the
result of a run never depends on parsing the model's prose.
"""

import html
import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

from . import cad_io, report, sandbox

SEARCH_MAX_RESULTS = 6
# Research budget per run: once it is spent, web_search / fetch_url refuse and tell the model to record
# what it could not verify. Every result reports how much of the budget remains.
SEARCH_BUDGET = 6
FETCH_BUDGET = 4
CHECKS_MAX_ITEMS = 25
CHECKS_MAX_CHARS = 300
MISMATCH_MARKER = "mismatch"
FETCH_MAX_BYTES = 400_000
FETCH_MAX_TEXT_CHARS = 12_000
FETCH_TIMEOUT_SECONDS = 20
# Redirects are followed one hop at a time, each hop validated like the first URL.
FETCH_MAX_REDIRECTS = 3
FETCH_SCHEMES = ("http", "https")
USER_AGENT = "vams-cad-step-agent/1.0 (+https://github.com/awslabs/visual-asset-management-system)"
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n\s*\n+")


class UnsafeUrl(ValueError):
    """The URL names something the fetch tool must not reach."""


def resolve_host(host, port):
    """Every address ``host`` resolves to right now (IPv4 and IPv6), as strings."""
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


def is_public_address(address):
    """True for a globally routable unicast address.

    Everything else is refused: RFC 1918 and the other private ranges, loopback, link-local (which
    holds the 169.254.169.254 instance metadata endpoint), the fc00::/7 unique-local block (which
    holds fd00:ec2::254), multicast, unspecified and reserved space. An IPv4-mapped IPv6 address is
    judged by the IPv4 address it carries.
    """
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_global and not ip.is_multicast


def validate_fetch_url(url, resolver=None):
    """The split URL when it may be fetched; raises UnsafeUrl otherwise.

    Per the SSRF guidance the check is on the ADDRESSES the host resolves to, not on the host name:
    a public-looking name that resolves to a private, loopback, link-local or metadata address is
    refused before any connection is made. The scheme must be http(s) and the URL may carry no
    user information.
    """
    parsed = urlsplit(url or "")
    if parsed.scheme.lower() not in FETCH_SCHEMES:
        raise UnsafeUrl("only http(s) URLs can be fetched")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrl("URLs with user information cannot be fetched")
    host = parsed.hostname
    if not host:
        raise UnsafeUrl("the URL names no host")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        addresses = (resolver or resolve_host)(host, port)
    except (socket.gaierror, OSError, UnicodeError) as exc:
        raise UnsafeUrl(f"{host} does not resolve") from exc
    if not addresses:
        raise UnsafeUrl(f"{host} does not resolve")
    for address in addresses:
        if not is_public_address(address):
            raise UnsafeUrl(f"{host} resolves to a non-public address")
    return parsed


@dataclass
class RunState:
    """Everything a run accumulates; ``run.py`` derives the outcome from it."""
    work_root: str
    input_step: Optional[str]
    output_name: str
    max_attempts: int
    script_timeout_seconds: int
    deadline_epoch: float
    research_allowed: bool
    attempts: List[report.AttemptRecord] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    best_output: Optional[str] = None
    best_geometry: Optional[Dict] = None
    finished: bool = False
    final_summary: str = ""
    final_unresolved: List[str] = field(default_factory=list)
    final_status_hint: str = ""
    final_checks: List[str] = field(default_factory=list)
    search_calls: int = 0
    fetch_calls: int = 0

    @property
    def attempts_left(self):
        return max(0, self.max_attempts - len(self.attempts))

    @property
    def seconds_left(self):
        return self.deadline_epoch - time.time()

    def out_of_budget(self):
        if self.attempts_left <= 0:
            return "the attempt budget is exhausted"
        if self.seconds_left <= 0:
            return "the run's time budget is exhausted"
        return ""


def strip_html(raw):
    """Readable text from an HTML document: tags dropped, entities decoded, whitespace collapsed."""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip()


def build_tools(state: RunState, search_fn: Optional[Callable] = None, fetch_fn: Optional[Callable] = None,
                screen_fn: Optional[Callable] = None):
    """The Strands tool functions bound to ``state``.

    ``search_fn(query, max_results)`` and ``fetch_fn(url)`` are injectable so the tool layer tests
    without network access; the defaults use ``ddgs`` and ``httpx``. ``screen_fn(text, what)`` is the
    guardrail applied to every fetched page before the model sees it; it raises when the guardrail
    intervenes. It is required whenever research is allowed: a fetched page is third-party text that
    must not reach the model unscreened.
    """
    from strands import tool  # lazy: the container has Strands, the tests inject fakes

    if state.research_allowed and screen_fn is None:
        raise ValueError("research tools require a guardrail screen for fetched pages")

    @tool
    def inspect_input_step() -> str:
        """Summarize the geometry of the input STEP file (solid/face/edge counts, bounding box in mm,
        volume). Returns a note when the run has no input file."""
        if not state.input_step:
            return json.dumps({"hasInput": False, "note": "This run has no input STEP file; create the geometry from scratch."})
        summary = cad_io.inspect_step(state.input_step)
        return json.dumps({"hasInput": True, "path": state.input_step, **summary.to_dict()})

    @tool
    def run_cad_script(code: str, intent: str) -> str:
        """Run a Python CadQuery script in a sandbox and validate the STEP file it writes.

        The script must read the input STEP from the CAD_INPUT_STEP environment variable when one
        exists, and MUST write its result to the path in CAD_OUTPUT_STEP (cq.exporters.export(shape,
        os.environ["CAD_OUTPUT_STEP"])). Only the Python standard library and cadquery are available;
        there is no network access. Returns a JSON result with the validation summary and the tail of the
        script's output; use it to correct the next attempt.

        Args:
            code: The complete Python script to run.
            intent: One sentence describing what this attempt changes or builds.
        """
        blocked = state.out_of_budget()
        if blocked:
            return json.dumps({"ok": False, "error": f"Refused: {blocked}. Call finish() with what was achieved."})
        number = len(state.attempts) + 1
        attempt_dir = sandbox.new_attempt_dir(state.work_root, number)
        timeout = int(max(30, min(state.script_timeout_seconds, state.seconds_left)))
        result = sandbox.run_script(
            code, attempt_dir, input_step=state.input_step, output_name="output.step",
            timeout_seconds=timeout)
        summary = cad_io.inspect_step(result.output_path) if result.output_exists else cad_io.StepSummary(
            valid=False, error="the script wrote no output file at CAD_OUTPUT_STEP")
        ok = result.succeeded and summary.valid
        state.attempts.append(report.AttemptRecord(
            number=number, succeeded=ok,
            summary=f"{intent} -> {summary.describe()}" + (" (timed out)" if result.timed_out else ""),
            output_tail=result.output_tail))
        if ok:
            state.best_output = result.output_path
            state.best_geometry = summary.to_dict()
        return json.dumps({
            "ok": ok,
            "attempt": number,
            "attemptsLeft": state.attempts_left,
            "returncode": result.returncode,
            "timedOut": result.timed_out,
            "geometry": summary.to_dict(),
            "outputTail": result.output_tail[-4000:],
        })

    @tool
    def finish(summary: str, unresolved: List[str], status: str, checks: List[str]) -> str:
        """Record the run's outcome. Call this exactly once when done.

        Args:
            summary: What was built or changed and how it was verified (a few sentences).
            unresolved: Each requested element that could NOT be completed or could not be verified,
                one entry per item, or an empty list. Include figures you assumed because they could not
                be found online or measured from the input.
            status: "succeeded" when every check below is "ok", otherwise "partial".
            checks: One entry per requested feature or dimension, comparing the instruction with the
                LAST accepted run_cad_script geometry summary, in the form
                "<feature>: expected <value> - measured <value> - ok" or "... - mismatch". Cover overall
                size, every hole/slot/pocket (count, diameter, depth or through), fillets/chamfers, and
                for a modify run the elements of the input that had to stay unchanged.
        """
        state.finished = True
        state.final_summary = str(summary or "")
        state.final_unresolved = [str(u) for u in (unresolved or []) if str(u).strip()]
        state.final_status_hint = str(status or "").strip().lower()
        state.final_checks = [str(c)[:CHECKS_MAX_CHARS] for c in (checks or []) if str(c).strip()][:CHECKS_MAX_ITEMS]
        mismatches = [c for c in state.final_checks if MISMATCH_MARKER in c.lower()]
        if mismatches and state.final_status_hint != report.STATUS_PARTIAL:
            return json.dumps({"recorded": True, "note": f"{len(mismatches)} check(s) report a mismatch; the run is "
                                                         f"recorded as partial and they are listed as unresolved."})
        return "recorded"

    tools = [inspect_input_step, run_cad_script, finish]

    if state.research_allowed:
        _search = search_fn or _ddgs_search
        _fetch = fetch_fn or _httpx_fetch

        @tool
        def web_search(query: str) -> str:
            """Search the web for reference material about a design (dimensions, datasheets, published
            board layouts, standard part sizes). Returns titles, URLs and snippets. A run may make a
            limited number of searches; the result says how many remain.

            Args:
                query: The search query.
            """
            if state.search_calls >= SEARCH_BUDGET:
                return json.dumps({"ok": False, "error": f"Refused: the research budget of {SEARCH_BUDGET} searches is spent. "
                                                         "Continue with the figures you have and list every unverified one in finish()."})
            state.search_calls += 1
            try:
                results = _search(query, SEARCH_MAX_RESULTS)
            except Exception as exc:
                return json.dumps({"ok": False, "error": f"search failed: {str(exc)[:300]}"})
            trimmed = [{"title": str(r.get("title", ""))[:200], "url": str(r.get("href") or r.get("url", ""))[:500],
                        "snippet": str(r.get("body") or r.get("snippet", ""))[:500]} for r in results]
            return json.dumps({"ok": True, "searchesLeft": SEARCH_BUDGET - state.search_calls, "results": trimmed})

        @tool
        def fetch_url(url: str) -> str:
            """Fetch a public web page and return its readable text (bounded; images and drawings in a page
            are NOT readable, only its text). Only http(s) URLs of publicly routable hosts can be fetched.
            Record-keeping: every URL fetched is listed as a source in the run's report. A run may fetch a
            limited number of pages; the result says how many remain.

            Args:
                url: The http(s) URL to fetch.
            """
            try:
                validate_fetch_url(url)
            except UnsafeUrl as exc:
                return json.dumps({"ok": False, "error": str(exc)})
            if state.fetch_calls >= FETCH_BUDGET:
                return json.dumps({"ok": False, "error": f"Refused: the research budget of {FETCH_BUDGET} page fetches is spent. "
                                                         "Continue with the figures you have and list every unverified one in finish()."})
            state.fetch_calls += 1
            try:
                text = _fetch(url)
            except Exception as exc:
                return json.dumps({"ok": False, "error": f"fetch failed: {str(exc)[:300]}"})
            text = text[:FETCH_MAX_TEXT_CHARS]
            # Third-party text: the guardrail's prompt-attack filter sees it before the model does.
            try:
                screen_fn(text, "fetched page")
            except Exception as exc:
                return json.dumps({"ok": False, "error": f"page not returned: {str(exc)[:300]}"})
            if url not in state.sources:
                state.sources.append(url)
            return json.dumps({"ok": True, "url": url, "fetchesLeft": FETCH_BUDGET - state.fetch_calls, "text": text})

        tools += [web_search, fetch_url]

    return tools


def _ddgs_search(query, max_results):
    from ddgs import DDGS  # lazy: research is optional per deployment
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


def _httpx_client():
    import httpx  # lazy: research is optional per deployment
    # Redirects are not followed by the client: each hop is re-validated below before it is requested.
    return httpx.Client(follow_redirects=False, timeout=FETCH_TIMEOUT_SECONDS,
                        headers={"User-Agent": USER_AGENT})


def _peer_address(response):
    """The address the connection actually reached, or None when the transport does not expose it."""
    stream = (getattr(response, "extensions", None) or {}).get("network_stream")
    if stream is None:
        return None
    try:
        peer = stream.get_extra_info("server_addr")
    except Exception:
        return None
    return peer[0] if isinstance(peer, (tuple, list)) and peer else None


def _read_bounded(response):
    content_type = response.headers.get("content-type", "")
    chunks, size = [], 0
    for chunk in response.iter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= FETCH_MAX_BYTES:
            break
    raw = b"".join(chunks).decode(getattr(response, "encoding", None) or "utf-8", errors="replace")
    return strip_html(raw) if "html" in content_type.lower() or raw.lstrip().startswith("<") else raw


def _httpx_fetch(url, client_factory=_httpx_client):
    """Fetch ``url`` following at most FETCH_MAX_REDIRECTS hops, each validated before it is requested.

    The address the connection reached is checked as well, so a name that resolved to a public
    address for the pre-flight check and to a private one for the connection (DNS rebinding) is not
    read either.
    """
    current = url
    with client_factory() as client:
        for _ in range(FETCH_MAX_REDIRECTS + 1):
            validate_fetch_url(current)
            with client.stream("GET", current) as response:
                peer = _peer_address(response)
                if peer is not None and not is_public_address(peer):
                    raise UnsafeUrl("the connection reached a non-public address")
                location = response.headers.get("location")
                if 300 <= response.status_code < 400 and location:
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                return _read_bounded(response)
    raise UnsafeUrl(f"more than {FETCH_MAX_REDIRECTS} redirects")

