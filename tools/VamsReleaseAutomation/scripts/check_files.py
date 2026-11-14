#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Static package checks used by scripts/check.sh.

  parse      every *.yaml / *.yml / *.json (and *.kiro.hook) file parses
  links      every relative Markdown link (and every GitHub source link into this package) resolves
  neutral    customer-neutrality and inclusive-language scan
  ledger     `vra ledger validate` on ledger-template/ and on its GovCloud variant
  identical  engine copies == reviewed files (IAM manifests, path policy, CDK bootstrap roles)

Usage: check_files.py <package-root> <check> [<check> ...] [--with <path> ...]

Every scan covers the package root plus the paths named with ``--with`` (the package's pages in the
VAMS documentation tree and its root-level steering, command and workflow files), and nothing else:
the package sits beside other VAMS components and never scans them.

A link that leaves the scanned paths but stays inside the repository (a VAMS documentation page, for
example) is checked against the working tree, then against ``git ls-tree`` of ``VAMS_REF`` (default
``origin/development``) in the VAMS clone named by ``VAMS_REPO``. Without either it is reported as
skipped, not as broken.

``VRA_NEUTRAL_TERMS_FILE`` names an optional file of extra regular expressions (one per line,
``#`` comments allowed) that the neutrality scan rejects, for terms a customer keeps out of the
package without naming them in it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

SKIP_DIRS = {"node_modules", ".pytest_cache", "__pycache__", ".git", "cdk.out", ".venv", "build", "dist"}
ALLOWED_ACCOUNTS = {"111111111111", "222222222222", "333333333333", "444444444444", "555555555555"}
GITHUB_SOURCE = re.compile(
    r"^https://github\.com/awslabs/visual-asset-management-system/(blob|tree)/main/"
    r"tools/VamsReleaseAutomation/([^#]*)(?:#.*)?$"
)


def files(root: Path, suffixes: tuple[str, ...] | None = None):
    """Files under ``root`` (a directory or a single file), skipping build and cache directories."""
    if root.is_file():
        if suffixes is None or root.name.endswith(suffixes):
            yield root
        return
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).parts
        if not p.is_file() or SKIP_DIRS & set(rel) or any(part.endswith(".egg-info") for part in rel):
            continue
        if suffixes is None or p.name.endswith(suffixes):
            yield p


def scanned(root: Path, extra: list[Path], suffixes: tuple[str, ...] | None = None):
    seen = set()
    for base in [root, *extra]:
        for p in files(base, suffixes):
            if p not in seen:
                seen.add(p)
                yield p


def display(p: Path, repo: Path) -> str:
    try:
        return p.relative_to(repo).as_posix()
    except ValueError:
        return p.as_posix()


class _Loader(yaml.SafeLoader):
    pass


# CloudFormation short-form intrinsics (!Ref, !Sub, ...) parse as plain tagged nodes
_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def check_parse(root: Path, extra: list[Path]) -> list[str]:
    repo = root.parent.parent
    bad = []
    for p in scanned(root, extra, (".yaml", ".yml")):
        loader = _Loader(p.read_text(encoding="utf-8"))  # a yaml.SafeLoader subclass that only adds the CFN tags
        try:
            while loader.check_data():
                loader.get_data()
        except yaml.YAMLError as exc:
            bad.append(f"{display(p, repo)}: YAML: {exc}".splitlines()[0])
        finally:
            loader.dispose()
    for p in scanned(root, extra, (".json", ".kiro.hook")):
        if p.name in ("tsconfig.json",):  # JSONC (comments allowed by TypeScript)
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            bad.append(f"{display(p, repo)}: JSON: {exc}")
    return bad


LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.S)


def slug(heading: str) -> str:
    """GitHub-style heading anchor (the slugger Docusaurus uses)."""
    text = re.sub(r"`([^`]*)`", r"\1", heading.strip())
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[^\w\- ]", "", text.lower())
    return text.replace(" ", "-")


def anchors(text: str) -> set[str]:
    text = re.sub(r"```.*?```", "", FRONTMATTER_RE.sub("", text), flags=re.S)
    out: set[str] = set(re.findall(r'<a\s+(?:id|name)="([^"]+)"', text))
    counts: dict[str, int] = {}
    for line in text.splitlines():
        m = re.match(r"^#{1,6}\s+(.*?)\s*(?:\{#([\w-]+)\})?\s*$", line)
        if not m:
            continue
        if m.group(2):
            out.add(m.group(2))
            continue
        s = slug(m.group(1))
        n = counts.get(s, 0)
        counts[s] = n + 1
        out.add(s if n == 0 else f"{s}-{n}")
    return out


class _Repo:
    """Read-only view of the VAMS clone named by VAMS_REPO, for paths outside the scanned set."""

    def __init__(self) -> None:
        self.path = os.environ.get("VAMS_REPO")
        self.ref = os.environ.get("VAMS_REF", "origin/development")
        self._names: set[str] | None = None
        self.error = ""

    def names(self) -> set[str] | None:
        if not self.path:
            return None
        if self._names is None:
            out = subprocess.run(["git", "-C", self.path, "ls-tree", "-r", "--name-only", self.ref],
                                 capture_output=True, text=True)
            if out.returncode != 0:
                self.error = f"cannot list {self.path} {self.ref}: {out.stderr.strip()[:200]}"
                self._names = set()
            else:
                self._names = set(out.stdout.splitlines())
                self._names |= {str(Path(n).parent) for n in self._names}
        return self._names

    def read(self, rel: str) -> str:
        out = subprocess.run(["git", "-C", self.path, "show", f"{self.ref}:{rel}"], capture_output=True, text=True)
        return out.stdout if out.returncode == 0 else ""


def check_links(root: Path, extra: list[Path]) -> list[str]:
    repo = root.parent.parent.resolve()
    vams = _Repo()
    bad: list[str] = []
    skipped = 0
    for p in scanned(root, extra, (".md",)):
        text = re.sub(r"```.*?```", "", p.read_text(encoding="utf-8"), flags=re.S)
        where = display(p, repo)
        for target in LINK_RE.findall(text):
            if target.startswith("#"):
                if target[1:] not in anchors(p.read_text(encoding="utf-8")):
                    bad.append(f"{where}: no heading for anchor {target}")
                continue
            gh = GITHUB_SOURCE.match(target)
            if gh:
                dest = root / gh.group(2)
                if not dest.exists() or (gh.group(1) == "blob") != dest.is_file():
                    bad.append(f"{where}: GitHub source link to a missing {gh.group(1)} path: {target}")
                continue
            if re.match(r"^[a-z][a-z0-9+.-]*:", target):
                continue
            path, _, anchor = target.partition("#")
            if not path:
                continue
            dest = (p.parent / path).resolve()
            try:
                rel = dest.relative_to(repo).as_posix()
            except ValueError:
                bad.append(f"{where}: link leaves the repository: {target}")
                continue
            if dest.exists():
                if anchor and dest.suffix == ".md" and dest.is_file() \
                        and anchor not in anchors(dest.read_text(encoding="utf-8")):
                    bad.append(f"{where}: no heading for anchor in {target}")
                continue
            in_scope = any(dest == x.resolve() or x.resolve() in dest.parents for x in [root, *extra])
            names = None if in_scope else vams.names()
            if in_scope:
                bad.append(f"{where}: broken link {target}")
            elif names is None:
                skipped += 1
            elif rel not in names:
                bad.append(f"{where}: not found in the repository at {vams.ref}: {target}")
            elif anchor and rel.endswith(".md") and anchor not in anchors(vams.read(rel)):
                bad.append(f"{where}: no heading for anchor at {vams.ref}: {target}")
    if vams.error:
        bad.append(vams.error)
    if skipped:
        print(f"  (skipped {skipped} links outside the scanned paths; set VAMS_REPO to verify them)")
    return bad


INCLUSIVE_TERMS = [
    (re.compile(r"\b(master|slave)\b", re.I), "non-inclusive term"),
    (re.compile(r"\b(white|black)list(s|ed|ing)?\b", re.I), "non-inclusive term"),
    (re.compile(r"\b(white|black)days?\b", re.I), "non-inclusive term"),
]
# Internal test-run labels: sandbox stack names, live-test run and phase labels, finding / requirement ids, run
# folders, and the environment and role names of test deployments. The package ships to customers; describe
# what happened instead (for example "found live", "a fresh prod install").
LABEL_TERMS = [
    (re.compile(r"\bdemo\d+\b", re.I), "sandbox stack name"),
    (re.compile(r"\bE2E-\d{3}\b|\be2e-\d{3}(-run)?\b", re.I), "live-test run label"),
    (re.compile(r"\bPhase [A-Z]\d+[a-z]?\b"), "live-test phase label"),
    (re.compile(r"\b[BO]-\d{3}-\d+[a-z]?\b|\bR\d{2}-\d+\b|\bREVIEW-\d{3}\b"), "internal finding id"),
    (re.compile(r"\b(?:live test|drill finding) [BOKW]\d+[a-z]?\b", re.I), "internal finding id"),
    # assembled, so this file does not carry the name it scans for
    (re.compile(r"\bvra" + r"e2e\b|\bprod0\d\b|\btest1\b"), "test deployment name"),
]
ACCOUNT_RE = re.compile(r"(?<![\w-])\d{12}(?![\w-])")
# code and deployable templates must derive the partition; examples, tests and docs may show sample ARNs
ARN_SCOPES = ("engine/vra/", "adapters/shared/", "cicd/codebuild/", "cicd/pipeline/lib/", "cicd/pipeline/bin/",
              "cicd/oidc/", "cicd/github-actions/", "cicd/gitlab-ci/", "iam/cloudformation/", "iam/actions/")


def private_terms() -> list[tuple[re.Pattern, str]]:
    path = os.environ.get("VRA_NEUTRAL_TERMS_FILE")
    if not path:
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append((re.compile(line), "private term"))
    return out


def check_neutral(root: Path, extra: list[Path]) -> list[str]:
    repo = root.parent.parent
    terms = INCLUSIVE_TERMS + LABEL_TERMS + private_terms()
    bad = []
    me = Path(__file__).resolve()
    for p in scanned(root, extra):
        if p.resolve() == me or p.name in ("package-lock.json",) or p.suffix in (".png", ".jpg", ".pyc", ".zip"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        where = display(p, repo)
        try:
            pkg_rel = p.relative_to(root).as_posix()
        except ValueError:
            pkg_rel = ""
        for n, line in enumerate(text.splitlines(), 1):
            for rx, why in terms:
                if rx.search(line):
                    bad.append(f"{where}:{n}: {why}: {line.strip()[:120]}")
            for m in ACCOUNT_RE.finditer(line):
                if m.group(0) not in ALLOWED_ACCOUNTS:
                    bad.append(f"{where}:{n}: 12-digit number {m.group(0)} is not a placeholder account")
            if "arn:aws:" in line and pkg_rel.startswith(ARN_SCOPES) \
                    and "/tests/" not in pkg_rel and "/test/" not in pkg_rel:
                bad.append(f"{where}:{n}: hardcoded commercial partition 'arn:aws:'")
    return bad


def _validate(ledger_dir: Path) -> list[str]:
    from vra.ledger import Ledger
    res = Ledger(ledger_dir).validate()
    return [f"{ledger_dir.name}: {e}" for e in res["errors"]]


def check_ledger(root: Path, extra: list[Path]) -> list[str]:
    tpl = root / "ledger-template"
    bad = _validate(tpl)
    with tempfile.TemporaryDirectory() as td:
        gov = Path(td) / "ledger-govcloud"
        shutil.copytree(tpl, gov, ignore=shutil.ignore_patterns("variants"))
        variant = tpl / "variants" / "govcloud"
        for src in variant.rglob("*"):
            if src.is_file() and src.name != "README.md":
                dest = gov / src.relative_to(variant)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
        bad += [b.replace("ledger-govcloud", "ledger-template+variants/govcloud") for b in _validate(gov)]
    return bad


def check_identical(root: Path, extra: list[Path]) -> list[str]:
    bad = []
    reviewed = root / "iam" / "actions"
    packaged = root / "engine" / "vra" / "data" / "iam-actions"
    a = {p.name for p in reviewed.glob("*.yaml")}
    b = {p.name for p in packaged.glob("*.yaml")}
    if a != b:
        bad.append(f"iam/actions vs engine/vra/data/iam-actions differ in files: {sorted(a ^ b)}")
    for n in sorted(a & b):
        if (reviewed / n).read_bytes() != (packaged / n).read_bytes():
            bad.append(f"engine/vra/data/iam-actions/{n} != iam/actions/{n} (copy iam/actions over it)")
    pp = (root / "engine/vra/data/defaults/path-policy.yaml", root / "ledger-template/path-policy.yaml")
    if pp[0].read_bytes() != pp[1].read_bytes():
        bad.append("ledger-template/path-policy.yaml != engine/vra/data/defaults/path-policy.yaml")
    boot = (root / "iam/it-roles/cdk-bootstrap-roles.json", root / "engine/vra/data/cdk-bootstrap-roles.json")
    if boot[0].read_bytes() != boot[1].read_bytes():
        bad.append("engine/vra/data/cdk-bootstrap-roles.json != iam/it-roles/cdk-bootstrap-roles.json "
                   "(iam/scripts/extract_cdk_bootstrap_roles.py writes both)")
    return bad


CHECKS = {"parse": check_parse, "links": check_links, "neutral": check_neutral, "ledger": check_ledger,
          "identical": check_identical}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", type=Path)
    ap.add_argument("checks", nargs="+", choices=sorted(CHECKS))
    ap.add_argument("--with", dest="extra", action="append", type=Path, default=[],
                    help="an extra file or directory to scan (repeatable); it must exist")
    args = ap.parse_args(argv)
    root = args.root.resolve()
    extra = [x.resolve() for x in args.extra]
    missing = [str(x) for x in extra if not x.exists()]
    if missing:
        print(f"  --with path(s) not found: {', '.join(missing)}")
        return 1
    failed = False
    for name in args.checks:
        probs = CHECKS[name](root, extra)
        for pr in probs:
            print(f"  {pr}")
        print(f"[{name}] {'OK' if not probs else f'{len(probs)} problem(s)'}")
        failed |= bool(probs)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
