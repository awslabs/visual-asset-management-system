# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Self-check scripts: every scan stays inside the package and the paths named with --with."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


check_files = _load("check_files")
gen_index = _load("gen_index")

GH = "https://github.com/awslabs/visual-asset-management-system"
# assembled so this file passes the neutrality scan it tests
NON_INCLUSIVE = "mas" + "ter"
REAL_LOOKING_ACCOUNT = "1234" * 3


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository with the package at tools/VamsReleaseAutomation beside another tool and a docs tree."""
    monkeypatch.delenv("VAMS_REPO", raising=False)
    monkeypatch.delenv("VRA_NEUTRAL_TERMS_FILE", raising=False)
    pkg = tmp_path / "tools/VamsReleaseAutomation"
    (pkg / "engine").mkdir(parents=True)
    (pkg / "engine/a.py").write_text("x = 1\n")
    (pkg / "README.md").write_text(
        "# Pkg\n\n[guide](../../docs/ra/page.md) [src](" + GH + "/blob/main/tools/VamsReleaseAutomation/engine/a.py)\n"
    )
    other = tmp_path / "tools/OtherTool"
    other.mkdir(parents=True)
    # a sibling component that would fail every scan if the package walked its parent directory
    (other / "notes.md").write_text(
        f"the {NON_INCLUSIVE} registry\n[gone](../../nowhere.md)\naccount {REAL_LOOKING_ACCOUNT}\n"
    )
    (other / "bad.yml").write_text("a: [\n")
    docs = tmp_path / "docs/ra"
    docs.mkdir(parents=True)
    (docs / "page.md").write_text("# Page\n\n## Section one\n\n[self](#section-one) [pkg](../../tools/VamsReleaseAutomation/README.md)\n")
    return tmp_path, pkg, docs


def test_scans_never_walk_the_package_parent(repo):
    _, pkg, docs = repo
    for check in (check_files.check_neutral, check_files.check_links, check_files.check_parse):
        assert check(pkg, [docs]) == [], check.__name__


def test_with_paths_are_scanned(repo):
    _, pkg, docs = repo
    (docs / "extra.md").write_text(f"[broken](missing.md)\nthe {NON_INCLUSIVE} copy\n")
    links = check_files.check_links(pkg, [docs])
    assert any("docs/ra/extra.md: broken link missing.md" in b for b in links)
    assert any("non-inclusive term" in b for b in check_files.check_neutral(pkg, [docs]))
    assert check_files.check_links(pkg, []) == []  # the docs tree is not scanned unless named


def test_github_source_link_must_name_an_existing_package_path(repo):
    _, pkg, docs = repo
    (docs / "src.md").write_text(
        f"[ok]({GH}/tree/main/tools/VamsReleaseAutomation/engine) "
        f"[missing]({GH}/blob/main/tools/VamsReleaseAutomation/engine/gone.py) "
        f"[kind]({GH}/blob/main/tools/VamsReleaseAutomation/engine)\n"
    )
    bad = check_files.check_links(pkg, [docs])
    assert len(bad) == 2 and all("GitHub source link" in b for b in bad)


def test_anchor_must_match_a_heading(repo):
    _, pkg, docs = repo
    (docs / "anchors.md").write_text(
        '# A\n\n## Real `code` heading\n\n<a id="html"></a>text\n\n'
        "[ok](#real-code-heading) [html](#html) [no](#nope) [x](page.md#nope)\n"
    )
    bad = check_files.check_links(pkg, [docs])
    assert len(bad) == 2


def test_link_outside_the_scanned_paths_is_skipped_without_vams_repo(repo, capsys):
    _, pkg, docs = repo
    (docs / "vams.md").write_text("[vams page](../deployment/update-the-solution.md)\n")
    assert check_files.check_links(pkg, [docs]) == []
    assert "skipped 1 links" in capsys.readouterr().out


def test_link_leaving_the_repository_fails(repo):
    _, pkg, docs = repo
    (docs / "out.md").write_text("[out](../../../elsewhere.md)\n")
    assert any("leaves the repository" in b for b in check_files.check_links(pkg, [docs]))


def test_private_terms_file_adds_terms(repo, tmp_path, monkeypatch):
    _, pkg, docs = repo
    (pkg / "engine/b.py").write_text("# built for acme corp\n")
    assert check_files.check_neutral(pkg, [docs]) == []
    terms = tmp_path / "terms.txt"
    terms.write_text("# private\n(?i)\\bacme\\b\n")
    monkeypatch.setenv("VRA_NEUTRAL_TERMS_FILE", str(terms))
    assert any("private term" in b for b in check_files.check_neutral(pkg, [docs]))


# assembled so this file passes the neutrality scan it tests
LABELS = ["de" + "mo11 stack", "the E2" + "E-050 run", "e2e-0" + "50-run/K/K11", "live test Pha" + "se B10d",
          "finding B-0" + "50-29", "R" + "50-18: a gate", "REVIE" + "W-050 fix wave", "live test O" + "4",
          "role vra" + "e2e-deploy", "the pro" + "d01 env", "the tes" + "t1 stack"]


@pytest.mark.parametrize("line", LABELS)
def test_internal_test_run_labels_fail_the_scan(repo, line):
    _, pkg, docs = repo
    (pkg / "engine/c.py").write_text(f"# {line}\n")
    assert any("engine/c.py" in b for b in check_files.check_neutral(pkg, [docs])), line


@pytest.mark.parametrize("line", ["Phase 1: pre-implementation", "found live: a fresh prod install",
                                  "env prod, stage and dev", "workflow W9", "test10 and prod1"])
def test_neutral_wording_passes_the_scan(repo, line):
    _, pkg, docs = repo
    (pkg / "engine/c.py").write_text(f"# {line}\n")
    assert check_files.check_neutral(pkg, [docs]) == []


def test_scanner_carries_no_customer_terms():
    text = (SCRIPTS / "check_files.py").read_text()
    assert "map(chr" not in text and "customer name" not in text


def test_hardcoded_partition_only_flagged_in_code_scopes(repo):
    _, pkg, docs = repo
    (pkg / "engine/vra").mkdir()
    (pkg / "engine/vra/c.py").write_text('ARN = "arn:aws:s3:::x"\n')
    (docs / "arn.md").write_text("example arn:aws:s3:::x\n")
    bad = check_files.check_neutral(pkg, [docs])
    assert len(bad) == 1 and "engine/vra/c.py" in bad[0]


def test_files_index_check_accepts_prettier_layout(tmp_path):
    (tmp_path / "Makefile").write_text("x\n")
    text = gen_index.render(tmp_path)
    compact = "\n".join("|".join(c.strip() for c in ln.split("|")) if ln.startswith("|") else ln
                        for ln in text.splitlines())
    assert compact != text
    assert gen_index.normalize(compact) == gen_index.normalize(text)
    assert gen_index.normalize(text + "| [`new`](new) | |\n") != gen_index.normalize(text)


def test_files_index_is_generated_in_prettier_table_layout(tmp_path):
    # the repository's `npm run prettier-check` covers FILES.md, so the generator writes Prettier's layout
    (tmp_path / "Makefile").write_text("x\n")
    (tmp_path / "a.txt").write_text("x\n")
    rows = [ln for ln in gen_index.render(tmp_path).splitlines() if ln.startswith("|")]
    assert rows[0].startswith("| File ") and rows[1].startswith("| ---")
    assert len({len(r) for r in rows}) == 1, rows
    cells = [r.split(" | ") for r in rows]
    assert {len(c[0]) for c in cells} == {len(cells[0][0])}
    assert set(rows[1].strip("| ").replace(" | ", "")) == {"-"}
