# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The tools the CAD agent may call, and the run state they share.

Every tool is a thin, bounded wrapper: the sandbox runs scripts, ``cad_io`` validates geometry, the
research tools return trimmed text and record the sources they consulted, and ``finish`` records the
outcome the agent reports. The state object is what ``run.py`` reads after the agent returns, so the
result of a run never depends on parsing the model's prose.
"""

import html
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import cad_io, report, sandbox

SEARCH_MAX_RESULTS = 6
FETCH_MAX_BYTES = 400_000
FETCH_MAX_TEXT_CHARS = 12_000
FETCH_TIMEOUT_SECONDS = 20
USER_AGENT = "vams-cad-step-agent/1.0 (+https://github.com/awslabs/visual-asset-management-system)"
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n\s*\n+")


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


def build_tools(state: RunState, search_fn: Optional[Callable] = None, fetch_fn: Optional[Callable] = None):
    """The Strands tool functions bound to ``state``.

    ``search_fn(query, max_results)`` and ``fetch_fn(url)`` are injectable so the tool layer tests
    without network access; the defaults use ``ddgs`` and ``httpx``.
    """
    from strands import tool  # lazy: the container has Strands, the tests inject fakes

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
    def finish(summary: str, unresolved: List[str], status: str) -> str:
        """Record the run's outcome. Call this exactly once when done.

        Args:
            summary: What was built or changed and how it was verified (a few sentences).
            unresolved: Each requested element that could NOT be completed, one entry per item, or
                an empty list. Include things that could not be found online.
            status: "succeeded" when every requested element is present in the output, otherwise
                "partial".
        """
        state.finished = True
        state.final_summary = str(summary or "")
        state.final_unresolved = [str(u) for u in (unresolved or []) if str(u).strip()]
        state.final_status_hint = str(status or "").strip().lower()
        return "recorded"

    tools = [inspect_input_step, run_cad_script, finish]

    if state.research_allowed:
        _search = search_fn or _ddgs_search
        _fetch = fetch_fn or _httpx_fetch

        @tool
        def web_search(query: str) -> str:
            """Search the web for reference material about a design (dimensions, datasheets, published
            board layouts, standard part sizes). Returns titles, URLs and snippets.

            Args:
                query: The search query.
            """
            try:
                results = _search(query, SEARCH_MAX_RESULTS)
            except Exception as exc:
                return json.dumps({"ok": False, "error": f"search failed: {str(exc)[:300]}"})
            trimmed = [{"title": str(r.get("title", ""))[:200], "url": str(r.get("href") or r.get("url", ""))[:500],
                        "snippet": str(r.get("body") or r.get("snippet", ""))[:500]} for r in results]
            return json.dumps({"ok": True, "results": trimmed})

        @tool
        def fetch_url(url: str) -> str:
            """Fetch a web page and return its readable text (bounded). Record-keeping: every URL fetched
            is listed as a source in the run's report.

            Args:
                url: The http(s) URL to fetch.
            """
            if not re.match(r"^https?://", url or ""):
                return json.dumps({"ok": False, "error": "only http(s) URLs can be fetched"})
            try:
                text = _fetch(url)
            except Exception as exc:
                return json.dumps({"ok": False, "error": f"fetch failed: {str(exc)[:300]}"})
            if url not in state.sources:
                state.sources.append(url)
            return json.dumps({"ok": True, "url": url, "text": text[:FETCH_MAX_TEXT_CHARS]})

        tools += [web_search, fetch_url]

    return tools


def _ddgs_search(query, max_results):
    from ddgs import DDGS  # lazy: research is optional per deployment
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


def _httpx_fetch(url):
    import httpx  # lazy: research is optional per deployment
    with httpx.Client(follow_redirects=True, timeout=FETCH_TIMEOUT_SECONDS,
                      headers={"User-Agent": USER_AGENT}) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= FETCH_MAX_BYTES:
                    break
            raw = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
    return strip_html(raw) if "html" in content_type.lower() or raw.lstrip().startswith("<") else raw


def cleanup(state: RunState):
    shutil.rmtree(state.work_root, ignore_errors=True)


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path
