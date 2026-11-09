# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ledger core patches (vra.core_patches), and the GitHub issue #431 boundary patch on real VAMS v2.6.6 input.

The aspect fixture is the v2.6.6 source of infra/lib/aspects/iam-role-transform.aspect.ts; the cloud assemblies
are the real v2.6.6 synths of fixtures/iam/ (commercial-boundary: the unpatched aspect, every role NoValue;
commercial-boundary-fixed: the aspect with the boundary branch fixed, every role bounded)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import iam_fixtures as fx
import pytest
from conftest import git
from test_iam_package import _ungate, make_ctx, plan_from, response_for

from vra import core_patches, errors, iam, runner
from vra.errors import NeedsHuman

PKG = Path(__file__).resolve().parents[2]
PATCH = PKG / "ledger-template" / iam.BOUNDARY_PATCH
ASPECT = Path(__file__).parent / "fixtures" / "iam" / "vams-2.6.6-iam-role-transform.aspect.ts"
ASPECT_REL = "infra/lib/aspects/iam-role-transform.aspect.ts"
FIXED = 'this.ApplyOverride(node, "PermissionsBoundary", boundary);'
ENTRY = "-   `core-patches/iam-role-transform-boundary.patch` (CP-431)\n"


def _ledger(root: Path, entry: str = ENTRY, copy: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "core-patches.md").write_text("# Core patch register\n\n" + entry)
    if copy:
        (root / "core-patches").mkdir(exist_ok=True)
        shutil.copyfile(PATCH, root / iam.BOUNDARY_PATCH)
    return root


def _src(root: Path, text: str | None = None) -> Path:
    p = root / ASPECT_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text if text is not None else ASPECT.read_text())
    return root


# ------------------------------------------------------------------ the register
def test_the_shipped_template_lists_no_active_patch_and_the_patch_file_is_valid(tmp_path):
    tpl = PKG / "ledger-template"
    assert core_patches.listed(tpl) == []                 # the template entry sits in a fenced block
    assert "core-patches/iam-role-transform-boundary.patch" in (tpl / "core-patches.md").read_text()
    assert core_patches.validate(_ledger(tmp_path / "l")) == []
    assert core_patches.touched_paths(PATCH.read_text()) == [ASPECT_REL]
    assert "GitHub issue #431" in PATCH.read_text()


def test_patch_files_are_not_fork_paths_and_fenced_lines_are_ignored(tmp_path):
    from vra.ledger import Ledger
    root = _ledger(tmp_path / "l", ENTRY + "-   `backend/backend/core.py` fork edit\n"
                   "```markdown\n-   `web/src/never.ts` template only\n```\n")
    led = Ledger.__new__(Ledger)
    led.root = root
    assert led.core_patches() == ["backend/backend/core.py"]
    assert led.core_patch_files() == [iam.BOUNDARY_PATCH]


@pytest.mark.parametrize("entry,copy,needle", [
    (ENTRY, False, "does not exist"),
    ("-   `core-patches/../../evil.patch`\n", False, "outside core-patches/"),
])
def test_validate_rejects_missing_and_escaping_patches(tmp_path, entry, copy, needle):
    assert any(needle in e for e in core_patches.validate(_ledger(tmp_path / "l", entry, copy)))


def test_validate_rejects_a_patch_of_absolute_or_parent_paths(tmp_path):
    root = _ledger(tmp_path / "l")
    (root / iam.BOUNDARY_PATCH).write_text("--- a/../outside.txt\n+++ b/../outside.txt\n@@ -1 +1 @@\n-a\n+b\n")
    assert any("only change relative paths" in e for e in core_patches.validate(root))


# ------------------------------------------------------------------ apply
def test_boundary_patch_applies_to_the_real_v266_aspect_then_reports_present(tmp_path):
    root = _ledger(tmp_path / "l")
    src = _src(tmp_path / "src")
    recs = core_patches.apply_all(root, src)
    assert [r["status"] for r in recs] == ["applied"] and recs[0]["paths"] == [ASPECT_REL]
    text = (src / ASPECT_REL).read_text()
    assert FIXED in text and text.count('"PermissionsBoundary", Aws.NO_VALUE') == 0
    # the fork already has the change (committed, or upstream shipped it): nothing to do
    again = core_patches.apply_all(root, src)
    assert [r["status"] for r in again] == ["present"] and (src / ASPECT_REL).read_text() == text


def test_a_patch_that_no_longer_applies_stops_the_run(tmp_path):
    root = _ledger(tmp_path / "l")
    src = _src(tmp_path / "src", ASPECT.read_text().replace("this.conditionBoundary.logicalId",
                                                            "this.otherCondition.logicalId"))
    with pytest.raises(NeedsHuman) as ei:
        core_patches.apply_all(root, src)
    assert "does not apply" in ei.value.message and "Refresh the patch" in ei.value.message


def test_apply_never_escapes_into_an_enclosing_repository(tmp_path):
    """Inside a git work tree `git apply` would silently skip paths; the engine sets GIT_CEILING_DIRECTORIES."""
    outer = tmp_path / "outer"
    outer.mkdir()
    git(outer, "init", "-q")
    src = _src(outer / "scratch" / "src")
    core_patches.apply_all(_ledger(tmp_path / "l"), src)
    assert FIXED in (src / ASPECT_REL).read_text()


# ------------------------------------------------------------------ GitHub issue #1210: API deployment on a prefix switch
API_PATCH = PKG / "ledger-template" / iam.API_DEPLOYMENT_PATCH
REST_REL = "infra/lib/nestedStacks/apiLambda/constructs/rest-api-gateway-construct.ts"
REST_FIXTURES = {
    "v2.6.6": Path(__file__).parent / "fixtures" / "iam" / "vams-2.6.6-rest-api-gateway-construct.ts",
    "development": Path(__file__).parent / "fixtures" / "iam" / "vams-development-a9ac094c4-rest-api-gateway-construct.ts",
}
API_FIXED = "deployment.addToLogicalId({ iamRoleNamePrefix });"
API_ENTRY = "-   `core-patches/api-deployment-role-name.patch` (CP-1210)\n"


def _ledger_with(root: Path, *patches: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "core-patches").mkdir(exist_ok=True)
    lines = []
    for p in patches:
        shutil.copyfile(p, root / "core-patches" / p.name)
        lines.append(f"-   `core-patches/{p.name}`\n")
    (root / "core-patches.md").write_text("# Core patch register\n\n" + "".join(lines))
    return root


def test_the_template_carries_the_1210_entry_and_its_patch_is_valid(tmp_path):
    """Fails without the CP-1210 template entry / patch file (Phase B9, GitHub issue #1210)."""
    tpl = PKG / "ledger-template"
    text = (tpl / "core-patches.md").read_text()
    assert iam.API_DEPLOYMENT_PATCH in text and "CP-1210" in text and "#1210" in text
    assert core_patches.listed(tpl) == []                 # still a template entry (fenced), never active by default
    assert core_patches.validate(_ledger_with(tmp_path / "l", API_PATCH)) == []
    assert core_patches.touched_paths(API_PATCH.read_text()) == [REST_REL]
    assert "GitHub issue #1210" in API_PATCH.read_text()


@pytest.mark.parametrize("version", sorted(REST_FIXTURES))
def test_1210_patch_applies_to_the_real_construct_then_reports_present(tmp_path, version):
    """The real rest-api-gateway-construct.ts of v2.6.6 (Deployment at line 326) and of development a9ac094c4
    (line 353, a different line after the hunk): applied, the prefix is hashed in before the spec, and only when it
    is a non-empty literal; applied again it is `present` and nothing changes."""
    root = _ledger_with(tmp_path / "l", API_PATCH)
    src = tmp_path / "src"
    (src / REST_REL).parent.mkdir(parents=True)
    original = REST_FIXTURES[version].read_text()
    assert API_FIXED not in original and "deployment.addToLogicalId(spec);" in original
    (src / REST_REL).write_text(original)
    recs = core_patches.apply_all(root, src)
    assert [r["status"] for r in recs] == ["applied"] and recs[0]["paths"] == [REST_REL]
    text = (src / REST_REL).read_text()
    created = text.index('new apigw.Deployment(this, "Deployment"')
    assert created < text.index(API_FIXED) < text.index("deployment.addToLogicalId(spec);")
    assert 'tryGetContext("environments")?.aws?.IamRoleNamePrefix' in text
    assert 'iamRoleNamePrefix !== ""' in text and "!cdk.Token.isUnresolved(iamRoleNamePrefix)" in text
    # nothing but the inserted block changed
    assert text.replace(text[created:text.index("deployment.addToLogicalId(spec);")], "") == \
        original.replace(original[original.index('new apigw.Deployment(this, "Deployment"'):
                                  original.index("deployment.addToLogicalId(spec);")], "")
    again = core_patches.apply_all(root, src)
    assert [r["status"] for r in again] == ["present"] and (src / REST_REL).read_text() == text


def test_1210_and_431_patches_apply_together_to_v266(tmp_path):
    """The test env's ledger lists both (boundary + rolePrefix); one build applies both, in register order."""
    root = _ledger_with(tmp_path / "l", PATCH, API_PATCH)
    src = _src(tmp_path / "src")
    (src / REST_REL).parent.mkdir(parents=True)
    (src / REST_REL).write_text(REST_FIXTURES["v2.6.6"].read_text())
    recs = core_patches.apply_all(root, src)
    assert [(Path(r["file"]).name, r["status"]) for r in recs] == [
        ("iam-role-transform-boundary.patch", "applied"), ("api-deployment-role-name.patch", "applied")]
    assert FIXED in (src / ASPECT_REL).read_text() and API_FIXED in (src / REST_REL).read_text()


@pytest.mark.parametrize("listed,warned", [(False, True), (True, False)])
def test_validate_warns_about_1210_when_a_role_prefix_is_set_without_the_patch(tmp_path, listed, warned):
    """Without the CP-1210 entry, `vra ledger validate` warns for an env with iam.mode boundary and a rolePrefix (the
    demo4 500 of Phase B8b); with it listed, no warning."""
    c = make_ctx(tmp_path, prod_iam={"mode": "boundary", "rolePrefix": "ITVAMS-",
                                     "boundaryArn": "arn:aws:iam::111111111111:policy/it-vams-boundary"})
    if listed:
        _ledger_with(c.ledger.root, PATCH, API_PATCH)
    hits = [w for w in c.ledger.validate()["warnings"] if "#1210" in w]
    assert bool(hits) is warned
    if warned:
        assert "envs/prod" in hits[0] or "prod" in hits[0]
        assert iam.API_DEPLOYMENT_PATCH in hits[0] and "HTTP 500" in hits[0]


def test_validate_does_not_warn_about_1210_without_a_role_prefix(tmp_path):
    c = make_ctx(tmp_path, prod_iam={"mode": "boundary",
                                     "boundaryArn": "arn:aws:iam::111111111111:policy/it-vams-boundary"})
    assert not [w for w in c.ledger.validate()["warnings"] if "#1210" in w]


# ------------------------------------------------------------------ vra build with the patch (real assemblies)
def _boundary_synth(seen: list):
    """`npx cdk synth`: the real v2.6.6 assembly the source would produce: bounded roles only when the aspect in
    the scratch source carries the fix (vra build ran synth in <src>/infra)."""
    def h(argv, cwd, env):
        if "synth" in argv and "-o" in argv:
            aspect = Path(cwd).parent / ASPECT_REL
            fixed = aspect.exists() and FIXED in aspect.read_text()
            name = "commercial-boundary-fixed" if fixed else "commercial-boundary"
            fx.write(name, Path(argv[argv.index("-o") + 1]))
            seen.append(name)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return None
    return h


def _fork_with_aspect(repos, text: str) -> None:
    work = repos["fork_work"]
    _src(work, text)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "fork at VAMS v2.6.6 aspect")
    git(work, "push", "-q", str(repos["fork"]), "HEAD:main")


def _boundary_ctx(tmp_path, repos, seen):
    c = make_ctx(tmp_path, repos, prod_iam={"mode": "boundary", "rolePrefix": "ITVAMS-",
                                            "boundaryArn": "arn:aws:iam::111111111111:policy/it-vams-boundary"},
                 handler=_boundary_synth(seen))
    _ungate(c)
    plan_from(c, "prod", "commercial-report", tmp_path)
    iam.apply(c, "prod", response_for(c, "prod"))
    return c


def test_build_without_the_patch_refuses_boundary_mode_and_names_the_patch(tmp_path, repos):
    seen: list = []
    _fork_with_aspect(repos, ASPECT.read_text())
    c = _boundary_ctx(tmp_path, repos, seen)
    out = runner.start(c, "build", {"lane": "release", "ref": "main", "suites": [], "envs": ["prod"]})
    assert out.exit_code == errors.NEEDS_HUMAN
    assert "88 IAM role(s)" in out.message and iam.BOUNDARY_PATCH in out.message and "#431" in out.message
    assert seen == ["commercial-boundary"]


def test_build_applies_the_ledger_patch_and_every_role_is_bounded(tmp_path, repos):
    seen: list = []
    _fork_with_aspect(repos, ASPECT.read_text())
    c = _boundary_ctx(tmp_path, repos, seen)
    _ledger(c.ledger.root)
    out = runner.start(c, "build", {"lane": "release", "ref": "main", "suites": [], "envs": ["prod"]})
    assert out.exit_code == 0, out.message
    assert seen == ["commercial-boundary-fixed"]
    assert out.run.step_result("build.synth.prod")["iam"]["rolesWithoutBoundary"] == 0
    from vra import build, util
    m = util.read_json(build.build_dir(c, out.run.state["build"]) / "manifest.json")
    assert m["corePatches"] == [{"file": iam.BOUNDARY_PATCH, "sha256": util.sha256_file(PATCH), "status": "applied"}]
    # the fork clone itself is never patched
    assert FIXED not in (c.repo_dir / ASPECT_REL).read_text()


def test_build_of_a_fork_that_committed_the_fix_reports_the_patch_present(tmp_path, repos):
    seen: list = []
    fixed = tmp_path / "fixed"
    core_patches.apply_all(_ledger(tmp_path / "l"), _src(fixed))
    _fork_with_aspect(repos, (fixed / ASPECT_REL).read_text())
    c = _boundary_ctx(tmp_path, repos, seen)
    _ledger(c.ledger.root)
    out = runner.start(c, "build", {"lane": "release", "ref": "main", "suites": [], "envs": ["prod"]})
    assert out.exit_code == 0, out.message
    src = out.run.step_result("build.source")
    assert [p["status"] for p in src["corePatches"]] == ["present"] and seen == ["commercial-boundary-fixed"]


def test_build_stops_when_the_listed_patch_no_longer_applies(tmp_path, repos):
    seen: list = []
    _fork_with_aspect(repos, ASPECT.read_text().replace("this.conditionBoundary.logicalId", "this.x.logicalId"))
    c = _boundary_ctx(tmp_path, repos, seen)
    _ledger(c.ledger.root)
    out = runner.start(c, "build", {"lane": "release", "ref": "main", "suites": [], "envs": ["prod"]})
    assert out.exit_code == errors.NEEDS_HUMAN and "does not apply" in out.message
    assert seen == []            # stopped before synth


# ------------------------------------------------------------------ permissionsBoundaryName (CDK context)
def test_cdk_boundary_context_only_works_from_cdk_json_on_real_synths(tmp_path):
    """Real v2.6.6 synths, both with the boundary patch: `-c @aws-cdk/core:permissionsBoundary={json}` reaches
    no role (the CDK CLI passes -c values as strings), the same context in infra/cdk.json bounds all 88."""
    c = make_ctx(tmp_path, prod_iam=None)
    c.ledger.env("prod")["permissionsBoundaryName"] = "it-vams-boundary"
    from test_iam_package import new_run
    flag = fx.write("commercial-cdkflag-patched", tmp_path / "flag")
    with pytest.raises(NeedsHuman) as ei:
        iam.check_assembly(c, new_run(c, "build"), "prod", flag, "build.synth.prod")
    assert "88 IAM role(s)" in ei.value.message
    ok = fx.write("commercial-cdkboundary-patched", tmp_path / "ok")
    assert iam.check_assembly(c, new_run(c, "build"), "prod", ok, "build.synth.prod")["rolesWithoutBoundary"] == 0


def test_build_writes_the_cdk_boundary_into_scratch_cdk_json(tmp_path, repos):
    seen: list = []

    def h(argv, cwd, env):
        if "synth" in argv and "-o" in argv:
            assert not any("permissionsBoundary" in a for a in argv), argv
            import json
            cdk_json = Path(cwd) / "cdk.json"
            ctx_ = json.loads(cdk_json.read_text()).get("context", {}) if cdk_json.exists() else {}
            aspect = Path(cwd).parent / ASPECT_REL
            good = ctx_.get("@aws-cdk/core:permissionsBoundary") == {"name": "it-vams-boundary"} and \
                FIXED in aspect.read_text()
            name = "commercial-cdkboundary-patched" if good else "commercial-cdkflag-patched"
            fx.write(name, Path(argv[argv.index("-o") + 1]))
            seen.append((name, cdk_json))
            return subprocess.CompletedProcess(argv, 0, "", "")
        return None

    _fork_with_aspect(repos, ASPECT.read_text())
    c = make_ctx(tmp_path, repos, prod_iam=None, handler=h)
    _ungate(c)
    c.ledger.env("prod")["permissionsBoundaryName"] = "it-vams-boundary"
    _ledger(c.ledger.root)
    out = runner.start(c, "build", {"lane": "release", "ref": "main", "suites": [], "envs": ["prod"]})
    assert out.exit_code == 0, out.message
    assert [n for n, _ in seen] == ["commercial-cdkboundary-patched"]
    assert out.run.step_result("build.synth.prod")["iam"]["rolesWithoutBoundary"] == 0
