# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An evaluation template's checkpoint tag may be blank ONLY where something resolves the blank.

This file previously asserted the opposite — that every evaluation template must mark its checkpoint
tag `required: true` — and that rule was wrong. It is recorded here rather than deleted because the
reasoning that produced it is the interesting part.

**What was measured.** `isaaclab-evaluation` was launched through its default template with no tag
values and reached FAILED with `PipelineError: Evaluation failed with exit code 1`, its recorded
`renderedConfig` carrying `{"trainingConfig": {..., "checkpointPath": ""}}`. That observation is real.

**What it was mis-attributed to.** The conclusion drawn was "an optional checkpoint tag renders empty
and the container cannot run". But both evaluation pipelines resolve a blank checkpoint on purpose:

* `isaacLabTraining/lambda/openPipeline.py` documents a three-tier priority — `checkpointPath`, then
  an operator-supplied `policyS3Uri`, then **auto-discovery of the newest `.pt` in the asset** — and
  returns an empty string only when discovery finds nothing.
* `gr00t/container/__main__.py::resolve_checkpoint_folder` lists the asset's `gr00tOutput_*` folders
  and returns the newest when `requested` is blank. Its own template says so: "Leave Checkpoint folder
  blank to evaluate the most recent gr00tOutput_* folder, which is what an evaluation run straight
  after a fine-tuning run wants."

So the run failed because the test asset held **no policy file for discovery to find**, not because the
tag was optional. Marking the tag required "fixed" the symptom by forbidding the documented
zero-argument flow — for gr00t, the primary one. Both `inputInstructions` explicitly invite the blank
value the rule then rejected, which is the contradiction that should have stopped it.

**What this file pins now.** The property that actually holds: a checkpoint tag may be optional with no
default only when its pipeline has a resolver for the blank case. A new evaluation template whose
pipeline has no such resolver has to say so, by marking the tag required or giving it a default.

Deliberately NOT asserting the reverse either. The lesson is that this shape is a per-pipeline
question, so the rule is written to consult the pipeline rather than to impose one answer on all of
them.
"""

import json
import os
import re

import pytest

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
_PIPELINES_DIR = os.path.join(_REPO_ROOT, "backendPipelines")


def _template_files():
    """Every vamsSchema template bundle, at any nesting depth.

    Walked rather than globbed: the Isaac Lab bundles sit under `vamsSchema/evaluation/templates/` and
    `vamsSchema/training/templates/`, one level deeper than every other pipeline, so a
    `vamsSchema/templates/*.json` glob silently matches none of them. The same class of miss (a glob
    validating 0 of 29 templates) has already happened once in this tree.
    """
    found = []
    for dirpath, _dirnames, filenames in os.walk(_PIPELINES_DIR):
        parts = dirpath.split(os.sep)
        if "vamsSchema" not in parts or "templates" not in parts:
            continue
        for name in filenames:
            if name.endswith(".json"):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _is_evaluation_template(template_id, body):
    """An evaluation template names itself, or sets an evaluate mode in its body."""
    tid = (template_id or "").lower()
    if "evaluate" in tid or "evaluation" in tid or tid.endswith("-eval"):
        return True
    text = body if isinstance(body, str) else json.dumps(body or {})
    return '"mode": "evaluate"' in text or '"mode":"evaluate"' in text


def _checkpoint_tags(schema):
    return [s for s in (schema or []) if "CHECKPOINT" in str(s.get("tagKey", "")).upper()]


def _pipeline_root(template_path):
    """The pipeline directory a template bundle belongs to (the parent of its `vamsSchema`)."""
    parts = template_path.split(os.sep)
    return os.sep.join(parts[: parts.index("vamsSchema")])


# Markers that a pipeline resolves a blank checkpoint itself. Each is a real resolver in the tree:
# the gr00t container's newest-folder lookup, and the Isaac Lab Lambda's auto-discovery of the newest
# .pt. Matched on the SOURCE of the owning pipeline, so a new pipeline gets the benefit only if it
# actually implements one.
_BLANK_RESOLVER_PATTERNS = (
    re.compile(r"def resolve_checkpoint_folder\b"),
    re.compile(r"newest\s+\.pt|discover.{0,20}\.pt|Auto-discover\s+\.pt", re.IGNORECASE),
    re.compile(r"policy_s3_uri|policyS3Uri"),
)


def _resolves_blank_checkpoint(pipeline_root):
    """True when the pipeline's own source resolves an empty checkpoint value."""
    for dirpath, dirnames, filenames in os.walk(pipeline_root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "tests", "node_modules")]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            try:
                with open(os.path.join(dirpath, name), encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            if any(p.search(text) for p in _BLANK_RESOLVER_PATTERNS):
                return True
    return False


@pytest.mark.unit
class TestEvaluationCheckpointTagMatchesItsPipeline:
    def test_at_least_one_evaluation_template_exists_to_check(self):
        """Control. Without it every rule below passes on an empty set if the walk breaks.

        This is also the assertion that the nested-directory walk reaches the Isaac Lab bundles; a
        `vamsSchema/templates/*.json` glob would satisfy the rest of this file vacuously.
        """
        evaluation = [
            p for p in _template_files()
            if _is_evaluation_template(_load(p).get("templateId"), _load(p).get("configBody"))
        ]
        assert evaluation, (
            "no evaluation template was found under backendPipelines/**/vamsSchema/**/templates/, so "
            "the rules below would pass without examining anything")
        # And that at least one of them carries a checkpoint tag, which is the subject.
        with_tag = [p for p in evaluation if _checkpoint_tags(_load(p).get("tagSchema"))]
        assert with_tag, "no evaluation template carries a CHECKPOINT tag to reason about"

    def test_an_optional_checkpoint_tag_has_a_resolver_for_the_blank_case(self):
        """The property that actually holds, stated per pipeline rather than imposed on all.

        A blank checkpoint is legitimate where the pipeline resolves it — and both of today's
        evaluation pipelines do, which is why requiring the tag was a regression. What is NOT
        legitimate is a template inviting a blank value that nothing downstream can act on.
        """
        offenders = []
        for path in _template_files():
            body = _load(path)
            if not _is_evaluation_template(body.get("templateId"), body.get("configBody")):
                continue
            for tag in _checkpoint_tags(body.get("tagSchema")):
                optional_and_blank = (tag.get("required") is not True
                                      and tag.get("default") in (None, ""))
                if not optional_and_blank:
                    continue
                if not _resolves_blank_checkpoint(_pipeline_root(path)):
                    offenders.append(
                        f"{os.path.relpath(path, _REPO_ROOT)}: {tag.get('tagKey')} is optional with "
                        f"no default, and no resolver for a blank checkpoint was found in its pipeline")

        assert offenders == [], (
            "an evaluation template invites a blank checkpoint that its pipeline cannot resolve, so a "
            "zero-argument run renders an empty path and the container exits non-zero AFTER a GPU "
            "instance has been provisioned. Either implement the blank case in the pipeline, or mark "
            "the tag required / give it a default so the execute API refuses the run up front:\n  "
            + "\n  ".join(offenders))

    def test_the_resolver_detector_discriminates(self):
        """Control for the rule above, which is an empty-set assertion.

        Without this, a detector that answered True for every directory would excuse every template.
        Asserts it answers True for the two pipelines that DO resolve a blank checkpoint and False for
        a pipeline that has no checkpoint concept at all.
        """
        gr00t = os.path.join(_PIPELINES_DIR, "genAi", "nvidia", "gr00t")
        isaac = os.path.join(_PIPELINES_DIR, "simulation", "isaacLabTraining")
        potree = os.path.join(_PIPELINES_DIR, "preview", "pcPotreeViewer")
        for path in (gr00t, isaac, potree):
            assert os.path.isdir(path), f"{path} moved; this control no longer has a subject"
        assert _resolves_blank_checkpoint(gr00t) is True, \
            "gr00t resolve_checkpoint_folder() was not detected"
        assert _resolves_blank_checkpoint(isaac) is True, \
            "the Isaac Lab Lambda's .pt auto-discovery was not detected"
        assert _resolves_blank_checkpoint(potree) is False, \
            "the detector answered True for a pipeline with no checkpoint concept, so it excuses everything"

    def test_both_evaluation_templates_still_invite_the_blank_value_they_document(self):
        """A regression guard on the revert itself.

        Requiring these two tags broke the flow their own `inputInstructions` advertise — for gr00t,
        the primary one ("an evaluation run straight after a fine-tuning run"). This pins that the
        instructions and the tag agree, in the direction that was actually wrong.
        """
        expectations = {
            "gr00t-evaluate-default": "CHECKPOINT_FOLDER",
            "isaaclab-evaluation-cartpole": "CHECKPOINT_PATH",
        }
        seen = {}
        for path in _template_files():
            body = _load(path)
            tid = body.get("templateId")
            if tid not in expectations:
                continue
            seen[tid] = True
            instructions = (body.get("inputInstructions") or "").lower()
            tag = next((t for t in _checkpoint_tags(body.get("tagSchema"))
                        if t.get("tagKey") == expectations[tid]), None)
            assert tag is not None, f"{tid} no longer carries {expectations[tid]}"
            assert "blank" in instructions, (
                f"{tid} no longer documents the blank case; if that is deliberate, this guard and the "
                f"tag's `required` flag must move together")
            assert tag.get("required") is not True, (
                f"{tid}: {expectations[tid]} is required again, which forbids the blank value its own "
                f"inputInstructions invite. Its pipeline resolves blank by discovery — see this "
                f"module's docstring for the measurement that was mis-attributed the first time.")
        assert set(seen) == set(expectations), (
            f"expected both evaluation templates to be present; found {sorted(seen)}")
