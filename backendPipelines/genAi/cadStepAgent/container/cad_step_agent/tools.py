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
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

from . import cad_io, cancellation, report, sandbox

logger = logging.getLogger("cad_step_agent.tools")

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
# Content types the fetch tool reads: text the model can use. Anything else (a PDF, an image, an
# archive) has no readable text at this layer and is refused before its body is read.
FETCH_TEXT_CONTENT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml")
# A body that starts like one of these is a binary document even when the server calls it text.
FETCH_BINARY_SIGNATURES = (b"%PDF-", b"\x89PNG", b"\xff\xd8\xff", b"PK\x03\x04", b"GIF8")
# Redirects are followed one hop at a time, each hop validated like the first URL.
FETCH_MAX_REDIRECTS = 3
FETCH_SCHEMES = ("http", "https")
USER_AGENT = "vams-cad-step-agent/1.0 (+https://github.com/awslabs/visual-asset-management-system)"
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n\s*\n+")


class UnsafeUrl(ValueError):
    """The URL names something the fetch tool must not reach."""


class UnreadableDocument(ValueError):
    """The response is not text the model can read (a PDF, an image, an archive)."""


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
    # The input STEP's summary, taken once per run (by inspect_input_step, or with the first script result
    # when the model skipped that call): what every MODIFY attempt is compared against.
    input_summary: Optional[cad_io.StepSummary] = None

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


def sanitize_output(path, summary):
    """The summary of a script's output STEP once the geometry that belongs to no solid is dropped from it.

    A script that re-exports an imported part whole carries the source's PMI annotation planes and curve
    sets along. The output is the design, so the file is rewritten as its solids and the summary records
    what went, whatever the script did. A rewrite that fails leaves the file and the summary -- which
    already describes the solids only, and still names the stray geometry -- as they are; the drop is
    recorded only once the rewritten file re-inspects free of it.
    """
    if not (summary.valid and summary.non_solid_geometry):
        return summary
    stray = cad_io.describe_non_solid_geometry(summary.non_solid_geometry)
    try:
        dropped = cad_io.drop_non_solid_geometry(path)
    except Exception as exc:
        logger.warning("the output's geometry outside the solids (%s) could not be dropped: %s", stray, str(exc)[:200])
        return summary
    if not dropped:
        return summary
    summary = cad_io.inspect_step(path)
    if summary.valid and summary.non_solid_geometry:
        logger.warning("the output's geometry outside the solids (%s) could not be dropped: the rewritten file "
                       "still carries %s", stray, cad_io.describe_non_solid_geometry(summary.non_solid_geometry))
    elif summary.valid:
        summary.dropped_non_solid_geometry = dropped
    return summary


# A volume difference below this fraction of the input's volume (and below this many mm^3) is re-export
# noise, not a change: the noise measured on re-exported parts is at most 1e-6 of the volume, and a
# feature small enough to fall under this band on a large part -- a 1 mm hole in a cubic metre -- is one
# the model cannot verify by volume anyway. A bounding-box coordinate that moved less than BBOX_CHANGE_MM
# has not moved.
UNCHANGED_VOLUME_FRACTION = 1e-6
UNCHANGED_VOLUME_MM3 = 1e-3
BBOX_CHANGE_MM = 0.01


def _hole_count(summary):
    return sum(int(h.get("count", 0)) for h in (summary.features or {}).get("holes", []))


def delta_vs_input(before, after):
    """What a MODIFY attempt changed against the input: volume, hole count, solid count and whether the
    bounding box moved -- with the volume band under which a difference is re-export noise
    (``unchanged_below_mm3``) and a note when the numbers say a requested cut did not land, so the model
    cannot mistake a feature placed outside the material or on the wrong plane for a detector miss. None
    unless both summaries are valid geometry."""
    if not (before and before.valid and after and after.valid):
        return None
    dv = (after.volume_mm3 or 0.0) - (before.volume_mm3 or 0.0)
    holes = _hole_count(after) - _hole_count(before)
    solids = after.solid_count - before.solid_count
    bbox_changed = any(abs(a - b) > BBOX_CHANGE_MM
                       for a, b in zip(after.bounding_box_mm or [], before.bounding_box_mm or []))
    band = max(UNCHANGED_VOLUME_MM3, UNCHANGED_VOLUME_FRACTION * abs(before.volume_mm3 or 0.0))
    delta = {"volume_change_mm3": round(dv, 3) + 0.0, "holes_count_change": holes, "solid_count_change": solids,
             "bbox_changed": bbox_changed, "unchanged_below_mm3": round(band, 6)}
    unchanged = abs(dv) <= band
    if unchanged and holes == 0 and solids == 0 and not bbox_changed:
        delta["note"] = (f"|dV| = {abs(delta['volume_change_mm3'])} mm^3 is within this part's re-export noise band "
                         f"(<= {delta['unchanged_below_mm3']} mm^3) and no hole, solid or bounding-box change is "
                         "detected, so nothing measurable changed against the input: if a cut or hole was requested, it "
                         "was placed outside the material or on the wrong plane - change the plane (see the input's "
                         "orientation.thickness_axis) or the coordinates, not the API call")
    elif dv < 0 and not unchanged and holes <= 0:
        delta["note"] = ("volume dropped but no new hole is detected: right for a pocket, slot, chamfer or an enlarged "
                         "hole; if a hole was requested, the cut is a sliver on an edge face (wrong workplane) or overlaps "
                         "an existing feature - verify the drilling axis against the input's orientation.thickness_axis")
    return delta


def _input_summary(state):
    """The run's input summary, inspected once (None for a run without an input file)."""
    if state.input_step and state.input_summary is None:
        state.input_summary = cad_io.inspect_step(state.input_step)
    return state.input_summary


def build_tools(state: RunState, search_fn: Optional[Callable] = None, fetch_fn: Optional[Callable] = None,
                screen_fn: Optional[Callable] = None):
    """The Strands tool functions bound to ``state``.

    ``search_fn(query, max_results)`` and ``fetch_fn(url)`` are injectable so the tool layer tests
    without network access; the defaults use ``ddgs`` and ``httpx``. ``screen_fn(text, what)`` is the
    guardrail applied to every fetched page before the model sees it; it raises when the guardrail
    intervenes. It is required whenever research is allowed: a fetched page is third-party text that
    must not reach the model unscreened. Every tool first checks for a stop request and raises
    ``cancellation.RunCancelled`` when one is recorded, which is what ends the agent loop.
    """
    from strands import tool  # lazy: the container has Strands, the tests inject fakes

    if state.research_allowed and screen_fn is None:
        raise ValueError("research tools require a guardrail screen for fetched pages")

    @tool
    def inspect_input_step() -> str:
        """Summarize the geometry of the input STEP file: solid/face/edge counts, bounding box in mm, volume,
        the feature summary (holes by diameter with through/blind and their centres measured from the
        bounding box's minimum corner, cylindrical outer faces, fillet-like faces, planar faces), the
        orientation (largest planar faces with their normals; the thickness axis of a sheet-like part, which
        is the axis a hole through it runs along) and, for a multi-body file, each body's volume and bounding
        box under solids. Every figure describes the solids; annotation geometry outside them (PMI planes,
        curves) is only counted, under non_solid_geometry. Returns a note when the run has no input file."""
        cancellation.raise_if_requested()
        if not state.input_step:
            return json.dumps({"hasInput": False, "note": "This run has no input STEP file; create the geometry from scratch."})
        summary = _input_summary(state)
        return json.dumps({"hasInput": True, "path": state.input_step, **summary.to_dict()})

    @tool
    def run_cad_script(code: str, intent: str) -> str:
        """Run a Python CadQuery script in a sandbox and validate the STEP file it writes.

        The script must read the input STEP from the CAD_INPUT_STEP environment variable when one
        exists, and MUST write its result to the path in CAD_OUTPUT_STEP (cq.exporters.export(shape,
        os.environ["CAD_OUTPUT_STEP"])). Only the Python standard library and cadquery are available;
        there is no network access. Returns a JSON result with the validation summary and the tail of the
        script's output; use it to correct the next attempt. Geometry outside the solids (annotation planes
        or curves carried over from an imported file) is dropped from the output file and reported under
        dropped_non_solid_geometry. On a modify run the result also carries deltaVsInput: the volume, hole
        count, solid count and bounding-box change against the input, the volume band under which a
        difference is re-export noise (unchanged_below_mm3: one millionth of the input's volume, at least
        0.001 mm^3), and a note when the change stays within that band with nothing else changed or when
        volume went without a new hole.

        Args:
            code: The complete Python script to run.
            intent: One sentence describing what this attempt changes or builds.
        """
        cancellation.raise_if_requested()
        blocked = state.out_of_budget()
        if blocked:
            return json.dumps({"ok": False, "error": f"Refused: {blocked}. Call finish() with what was achieved."})
        number = len(state.attempts) + 1
        attempt_dir = sandbox.new_attempt_dir(state.work_root, number)
        timeout = int(max(30, min(state.script_timeout_seconds, state.seconds_left)))
        result = sandbox.run_script(
            code, attempt_dir, input_step=state.input_step, output_name="output.step",
            timeout_seconds=timeout)
        # A stop request kills the running script; its result is not an attempt to reason about.
        cancellation.raise_if_requested()
        summary = cad_io.inspect_step(result.output_path) if result.output_exists else cad_io.StepSummary(
            valid=False, error="the script wrote no output file at CAD_OUTPUT_STEP")
        summary = sanitize_output(result.output_path, summary)
        ok = result.succeeded and summary.valid
        delta = delta_vs_input(_input_summary(state), summary) if ok and state.input_step else None
        state.attempts.append(report.AttemptRecord(
            number=number, succeeded=ok,
            summary=f"{intent} -> {summary.describe()}" + (" (timed out)" if result.timed_out else ""),
            output_tail=result.output_tail))
        if ok:
            state.best_output = result.output_path
            state.best_geometry = summary.to_dict()
        reply = {
            "ok": ok,
            "attempt": number,
            "attemptsLeft": state.attempts_left,
            "returncode": result.returncode,
            "timedOut": result.timed_out,
            "geometry": summary.to_dict(),
            "outputTail": result.output_tail[-4000:],
        }
        if delta is not None:
            reply["deltaVsInput"] = delta
        return json.dumps(reply)

    @tool
    def finish(summary: str, unresolved: List[str], status: str, checks: List[str]) -> str:
        """Record the run's outcome. Call this exactly once when done.

        Args:
            summary: What was built or changed and how it was verified (a few sentences). It is shown to
                the user on its own, so it states the final bounding box, volume and feature counts in
                numbers (for example "100 x 60 x 15 mm, 89046 mm^3, 4 through holes D4.5"). End it with an
                "Assumptions:" line naming every value the instruction left unspecified and you chose (a
                wall thickness, a fillet radius); such a choice is not an unresolved item.
            unresolved: Each requested element that could NOT be completed or could not be verified,
                one entry per item, or an empty list. Include figures the instruction specifies or implies
                that you assumed because they could not be found online or measured from the input, and
                every figure whose only source is a forum, Q&A site, user post or blog (a manufacturer,
                vendor or standards page confirms a figure; a forum thread or "common practice" does not).
            status: "succeeded" when every check below is "ok" and no specified or implied figure rests on
                an assumption or an unconfirmed source (a value the instruction left open and you chose,
                listed under Assumptions in the summary, does not by itself make the run partial);
                otherwise "partial".
            checks: One entry per requested feature or dimension, comparing the instruction with the
                LAST accepted run_cad_script geometry summary, in the form
                "<feature>: expected <value> - measured <value> - ok" or "... - mismatch". Cover overall
                size, every hole/slot/pocket (count, diameter, depth or through), fillets/chamfers, and
                for a modify run the elements of the input that had to stay unchanged.
        """
        cancellation.raise_if_requested()
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
            cancellation.raise_if_requested()
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
            are NOT readable, only its text, and a PDF or other binary document is refused). Only http(s)
            URLs of publicly routable hosts can be fetched.
            Record-keeping: every URL fetched is listed as a source in the run's report. A run may fetch a
            limited number of pages; the result says how many remain.

            Args:
                url: The http(s) URL to fetch.
            """
            cancellation.raise_if_requested()
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


def _is_text_content_type(content_type):
    media = content_type.split(";")[0].strip().lower()
    return media == "" or media.startswith(FETCH_TEXT_CONTENT_TYPES)


def _read_bounded(response):
    content_type = response.headers.get("content-type", "")
    if not _is_text_content_type(content_type):
        raise UnreadableDocument(f"the page is {content_type.split(';')[0].strip()}, not readable text")
    chunks, size = [], 0
    for chunk in response.iter_bytes():
        if not chunks and chunk.lstrip().startswith(FETCH_BINARY_SIGNATURES):
            raise UnreadableDocument("the page is a binary document, not readable text")
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

