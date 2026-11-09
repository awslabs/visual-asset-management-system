# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test suites: VAMS unit suites (mirroring .github/workflows/ci.yml), live vamscli smoke, Playwright
web/e2e, customer regression hooks from ledger tests/customer.yaml, and JUnit aggregation.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from vra import data as vra_data
from pathlib import Path
from typing import Any

import yaml

from vra import util
from vra.errors import TestsFailed, UsageError, VraError
from vra.runner import workflow

ALL_SUITES = ("unit", "synth", "live-cli", "live-web", "customer")
DEFAULT_LIVE_SUITES = ["live-cli", "live-web", "customer"]


def suites_catalog() -> dict:
    return yaml.safe_load(vra_data.text("suites.yaml"))


def default_build_suites() -> list[str]:
    return list(suites_catalog().get("defaultBuildSuites", []))


# ------------------------------------------------------------------ JUnit helpers
def write_junit(path: Path, suite: str, cases: list[dict]) -> Path:
    ts = ET.Element("testsuite", name=suite, tests=str(len(cases)),
                    failures=str(sum(1 for c in cases if c["status"] == "failed")),
                    errors=str(sum(1 for c in cases if c["status"] == "error")),
                    skipped=str(sum(1 for c in cases if c["status"] == "skipped")),
                    time=f"{sum(c.get('time', 0.0) for c in cases):.3f}")
    for c in cases:
        tc = ET.SubElement(ts, "testcase", classname=suite, name=c["name"], time=f"{c.get('time', 0.0):.3f}")
        if c["status"] == "failed":
            f = ET.SubElement(tc, "failure", message=(c.get("message") or "failed")[:500])
            f.text = util.redact(c.get("output", ""))[-8000:]
        elif c["status"] == "error":
            e = ET.SubElement(tc, "error", message=(c.get("message") or "error")[:500])
            e.text = util.redact(c.get("output", ""))[-8000:]
        elif c["status"] == "skipped":
            ET.SubElement(tc, "skipped", message=c.get("message") or "skipped")
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(ts).write(path, encoding="utf-8", xml_declaration=True)
    return path


def aggregate_junit(junit_dir: Path) -> dict:
    """Totals over every JUnit file under ``junit_dir``.

    ``ok`` means "something ran and nothing failed": a set where every case was skipped, or no results at all,
    is NOT ok (``executed`` = 0). Callers that accept an empty set (a dry run) check ``failures``/``errors``.
    """
    if not junit_dir.exists():
        return _aggregate([])
    return _aggregate(sorted(junit_dir.rglob("*.xml")))


def _aggregate(files: list[Path]) -> dict:
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "files": [], "failed": []}
    for f in files:
        try:
            data = f.read_bytes()
            # JUnit XML never needs a DTD; refusing one rules out entity expansion and external entities, so
            # the standard library parser is safe on what is left (defusedxml is not a dependency)
            if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
                raise ET.ParseError("DTD in a JUnit file")
            # nosemgrep: python.lang.security.use-defused-xml-parse.use-defused-xml-parse
            root = ET.fromstring(data)  # nosec B314 - no DTD (checked above)
        except ET.ParseError:
            total["errors"] += 1
            total["failed"].append(f"{f.name}: unparseable JUnit")
            continue
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        for s in suites:
            for k in ("tests", "failures", "errors", "skipped"):
                total[k] += int(float(s.get(k, 0) or 0))
            for tc in s.iter("testcase"):
                if tc.find("failure") is not None or tc.find("error") is not None:
                    total["failed"].append(f"{s.get('name')}::{tc.get('name')}")
        total["files"].append(f.name)
    total["passed"] = total["tests"] - total["failures"] - total["errors"] - total["skipped"]
    total["executed"] = total["tests"] - total["skipped"]
    total["ok"] = total["failures"] == 0 and total["errors"] == 0 and total["executed"] > 0
    if total["executed"] == 0:
        total["reason"] = "no test results" if not total["files"] else "every test case was skipped"
    return total


def aggregate_by_suite(jdir: Path) -> dict[str, dict]:
    """Per-suite counts under ``junit/test-<env>/``: ``<suite>.xml`` files and ``<suite>/`` directories."""
    groups: dict[str, list[Path]] = {}
    if not jdir.exists():
        return {}
    for p in sorted(jdir.iterdir()):
        if p.is_file() and p.suffix == ".xml":
            groups.setdefault(p.stem, []).append(p)
        elif p.is_dir():
            groups.setdefault(p.name, []).extend(sorted(p.rglob("*.xml")))
    keys = ("tests", "failures", "errors", "skipped", "passed", "executed")
    return {name: {k: s[k] for k in keys} for name, files in groups.items() for s in [_aggregate(files)]}


def required_check(ctx, env: str, by_suite: dict[str, dict], selected: list[str] | None = None) -> list[str]:
    """Problems with the required live suites of ``env`` (ledger.required_suites): not selected, or 0 passed."""
    from vra.ledger import required_suites
    problems = []
    for s in required_suites(ctx.ledger, env):
        if selected is not None and s not in selected:
            problems.append(f"required suite {s} was not selected")
        elif (by_suite.get(s) or {}).get("passed", 0) < 1:
            problems.append(f"required suite {s} executed no passing test")
    return problems


# ------------------------------------------------------------------ unit suites
def _expand(arg: str, values: dict[str, str]) -> str:
    for k, v in values.items():
        arg = arg.replace("${" + k + "}", v)
    return arg


def _missing_npm_script(cwd: Path, argv: list[str]) -> str | None:
    """Name of the script when ``argv`` is ``npm run <script>`` and cwd/package.json does not define it."""
    if len(argv) < 3 or Path(argv[0]).name != "npm" or argv[1] not in ("run", "run-script"):
        return None
    pkg = cwd / "package.json"
    if not pkg.exists():
        return None
    try:
        scripts = (json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {})
    except (OSError, json.JSONDecodeError):
        return None
    return None if argv[2] in scripts else argv[2]


def run_unit_suites(ctx, run, src: Path, suites: list[str], sc, junit_subdir: str = "unit") -> dict:
    catalog = suites_catalog()["suites"]
    unknown = [s for s in suites if s not in catalog]
    if unknown:
        raise UsageError(f"unknown suites {unknown}; known: {sorted(catalog)}")
    jdir = run.junit_dir / junit_subdir
    values = {"PYTHON": os.environ.get("VRA_SUITE_PYTHON", "python3"),
              "SCRATCH": str(ctx.scratch_dir / "suite-tmp"), "NPM_VERSION": os.environ.get("VRA_NPM_VERSION", "11.19.1")}
    results = {}
    if ctx.dry_run:
        return {"dryRun": True, "suites": suites, "summary": None}
    if jdir.exists():
        # a resumed run re-executes the step; JUnit from the failed attempt must not count again
        shutil.rmtree(jdir)
    sh = ctx.shell(sc.log_path)
    for name in suites:
        cases = []
        failed = False
        for i, step in enumerate(catalog[name]):
            argv = [_expand(a, values) for a in step["cmd"]]
            tool = step.get("optionalTool")
            if tool and not shutil.which(tool):
                cases.append({"name": f"{name}[{i}] {' '.join(argv[:3])}", "status": "skipped",
                              "message": f"{tool} not installed"})
                continue
            if step.get("optionalPath") and not (src / step["optionalPath"]).exists():
                cases.append({"name": f"{name}[{i}] {' '.join(argv[:3])}", "status": "skipped",
                              "message": f"{step['optionalPath']} not in this tree"})
                continue
            if failed:
                cases.append({"name": f"{name}[{i}] {' '.join(argv[:3])}", "status": "skipped",
                              "message": "previous command failed"})
                continue
            missing = _missing_npm_script(src / step["cwd"], argv)
            if missing:
                # the catalog mirrors the newest CI; older VAMS trees (a release tag) may not define the script
                cases.append({"name": f"{name}[{i}] {' '.join(argv[:3])}", "status": "skipped",
                              "message": f"npm script {missing!r} is not defined in {step['cwd']}/package.json "
                                         f"at this VAMS version"})
                continue
            junit_file = None
            if step.get("junit"):
                junit_file = jdir / f"{name}-{i}.xml"
                junit_file.parent.mkdir(parents=True, exist_ok=True)
                argv = argv + [f"{step['junit']}={junit_file}"]
            t0 = time.monotonic()
            p = sh.run(argv, cwd=src / step["cwd"], check=False, timeout=step.get("timeoutSec", 7200))
            dt = time.monotonic() - t0
            if junit_file is None or not junit_file.exists():
                cases.append({"name": f"{name}[{i}] {' '.join(argv[:4])}", "time": dt,
                              "status": "passed" if p.returncode == 0 else "failed",
                              "message": f"exit {p.returncode}", "output": (p.stdout or "") + (p.stderr or "")})
            if p.returncode != 0:
                failed = True
        if cases:
            write_junit(jdir / f"{name}.xml", name, cases)
        results[name] = "failed" if failed else "passed"
    summary = aggregate_junit(jdir)
    out = {"suites": results, "summary": summary}
    if any(v == "failed" for v in results.values()):
        raise TestsFailed(f"unit suites failed: {[k for k, v in results.items() if v == 'failed']}", out)
    if suites and summary["executed"] == 0:
        # every command was skipped (tools missing, scripts absent): a build gate that ran nothing is not a pass
        raise TestsFailed(f"unit suites {suites} executed nothing ({summary.get('reason')})", out)
    return out


# ------------------------------------------------------------------ live tests
def test_role(ctx, env: str) -> str:
    """Identity of `vra test`: ``testRoleArn`` (a test-only role, never deploy-capable) when set, else the read-only
    role, else the deploy role."""
    e = ctx.ledger.env(env)
    return "test" if e.get("testRoleArn") else ("readonly" if e.get("readOnlyRoleArn") else "deploy")


def live_auth(ctx, env: str, live: dict | None = None, role: str | None = None) -> dict:
    """How the live suites sign in (env.yaml ``liveTest.auth``; ``credentialsSecret`` is the password form).
    ``role``: the identity that reads the secret (default :func:`test_role`; a deploy or rollback run's own
    post-deploy smoke passes ``"deploy"``, see :func:`live_cli_smoke`).

    - ``password``: secret ``{username, password}`` - a Cognito user with a local password;
    - ``apiKey``: secret ``{userId, apiKey}`` - a VAMS API key (works with MFA, SAML/OIDC federation and external
      IdPs, where no password sign-in exists);
    - ``token``: ``tokenCommand`` prints a VAMS token on stdout (refreshed by the customer's own tooling).
    Secret values are registered for redaction and passed on stdin only."""
    lt = live or (ctx.ledger.env(env).get("liveTest") or {})
    auth = dict(lt.get("auth") or {})
    if not auth and lt.get("credentialsSecret"):
        auth = {"type": "password", "secret": lt["credentialsSecret"]}
    typ = auth.get("type")
    if not typ:
        return {}
    if typ in ("password", "apiKey"):
        if not auth.get("secret"):
            raise UsageError(f"envs/{env} liveTest.auth.type={typ} needs liveTest.auth.secret (a secret NAME)")
        ident = role or test_role(ctx, env)
        try:
            sm = ctx.aws.client(env, "secretsmanager", ident)
            raw = sm.get_secret_value(SecretId=auth["secret"])["SecretString"]
        except VraError:
            raise
        except Exception as exc:  # noqa: BLE001
            # E2E-050 B-050-28: a raw botocore error here crashed the deploy run after `cdk deploy` (status FAILED,
            # nothing recorded, lock held). A denial parks the run BLOCKED_ON_IT (a pipeline retry resumes it);
            # anything else (no such secret yet, e.g. the first deploy) is a VraError, so the deploy's failure
            # path records the deploy and follows rollbackPolicy (ask: NEEDS_HUMAN, which a retry resumes).
            from vra import permissions
            from vra.errors import PermissionDenied
            if permissions.is_denied(exc):
                raise PermissionDenied(f"cannot read the live-test secret {auth['secret']} of {env} as the "
                                       f"{ident} identity", permissions.classify_exception(exc, "secretsmanager"),
                                       env=env) from exc
            raise UsageError(f"cannot read the live-test secret {auth['secret']} of {env} "
                             f"({type(exc).__name__}: {util.redact(str(exc))[:200]}); create it (JSON "
                             f"{{username, password}} or {{userId, apiKey}}), then resume the run") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            want = "{\"username\":...,\"password\":...}" if typ == "password" else "{\"userId\":...,\"apiKey\":...}"
            raise UsageError(f"secret {auth['secret']} must be JSON {want}") from exc
        if typ == "password":
            util.register_secret(data.get("password"))
            return {"type": "password", "username": data.get("username"), "password": data.get("password")}
        util.register_secret(data.get("apiKey"))
        return {"type": "apiKey", "userId": data.get("userId") or auth.get("userId"), "token": data.get("apiKey")}
    if typ == "token":
        cmd = auth.get("tokenCommand")
        if not cmd or not auth.get("userId"):
            raise UsageError(f"envs/{env} liveTest.auth.type=token needs userId and tokenCommand")
        import subprocess
        # run directly (not through the logging Shell): stdout is the token
        proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=300,
                              env={**os.environ, "VRA_ENV": env})
        token = (proc.stdout or "").strip()
        if proc.returncode != 0 or not token:
            raise UsageError(f"liveTest.auth.tokenCommand for {env} failed (exit {proc.returncode}) or printed nothing")
        util.register_secret(token)
        return {"type": "token", "userId": auth["userId"], "token": token, "expiresIn": auth.get("expiresIn")}
    raise UsageError(f"envs/{env} liveTest.auth.type {typ!r} is not password, apiKey or token")


def live_credentials(ctx, env: str) -> dict:
    """Password sign-in values (empty for the other auth types), for callers that need a username/password."""
    a = live_auth(ctx, env)
    return {"username": a.get("username"), "password": a.get("password")} if a.get("type") == "password" else {}


def resolve_live(ctx, env: str, live: dict | None = None, role: str | None = None) -> dict:
    """liveTest with ``webUrl`` filled from the SSM parameter VAMS publishes (``/<name>-<base>-<region>/web/deployedUrl``)
    when env.yaml leaves it unset, so a custom domain or slot change never leaves a stale hand-entered URL.
    ``role``: the identity that reads it (default :func:`test_role`)."""
    lt = dict(live or (ctx.ledger.env(env).get("liveTest") or {}))
    if lt.get("webUrl") or ctx.dry_run:
        return lt
    try:
        name = f"/{ctx.ledger.env(env)['configName']}-{ctx.ledger.stack_suffix(env, lt.get('baseOverride'))}/web/deployedUrl"
        ssm = ctx.aws.client(env, "ssm", role or test_role(ctx, env))
        lt["webUrl"] = ssm.get_parameter(Name=name)["Parameter"]["Value"]
        lt["webUrlFrom"] = name
    except Exception as exc:  # noqa: BLE001 - the suite then reports "webUrl not set"
        lt["webUrlError"] = f"{type(exc).__name__}"
    return lt


def login_step(cli: str, profile: str, auth: dict) -> tuple[str, list[str], str] | None:
    if auth.get("type") == "password" and auth.get("username"):
        return ("login", [cli, "--profile", profile, "auth", "login", "-u", auth["username"], "--password-stdin",
                          "--skip-version-check", "--json-output"], (auth.get("password") or "") + "\n")
    if auth.get("type") in ("apiKey", "token") and auth.get("userId"):
        argv = [cli, "--profile", profile, "auth", "login", "--user-id", auth["userId"], "--token-override-stdin",
                "--skip-version-check", "--json-output"]
        if auth.get("expiresIn"):
            argv += ["--expires-at", f"+{int(auth['expiresIn'])}"]
        return ("login", argv, (auth.get("token") or "") + "\n")
    return None


def resolve_vamscli(ctx, builds: list[str | None], sc) -> str:
    """The vamscli the live suites run. ``VRA_VAMSCLI`` (an operator's own install) wins. Otherwise the VamsCLI
    wheel of the first of ``builds`` whose bundle carries one (``vra build`` packs it from the fork's
    ``tools/VamsCLI``), installed into a venv of its own after its sha256 matched the manifest; else ``vamscli``
    on PATH. E2E-050 B-050-18a: the pipeline's CodeBuild jobs had no VamsCLI at all."""
    import sys
    if os.environ.get("VRA_VAMSCLI"):
        return os.environ["VRA_VAMSCLI"]
    from vra import build as build_mod
    for b in builds:
        if not b:
            continue
        try:
            m = build_mod.load_manifest(ctx, b)
        except Exception:  # noqa: BLE001 - a build without a local bundle has no wheel to offer
            continue
        info = m.get("vamscli") or {}
        if not info.get("vendored"):
            continue
        whl = build_mod.build_dir(ctx, b) / info["wheel"]
        if not whl.exists() or util.sha256_file(whl) != info["sha256"]:
            raise VraError(f"VamsCLI wheel {info['wheel']} of build {b} is missing or does not match its manifest "
                           f"sha256; refusing to run it")
        venv = ctx.work_dir / "vamscli" / info["sha256"][:16]
        exe = venv / ("Scripts/vamscli.exe" if os.name == "nt" else "bin/vamscli")
        marker = venv / ".vra-wheel-sha256"
        if ctx.dry_run:
            return str(exe)
        if not (exe.exists() and marker.exists() and marker.read_text().strip() == info["sha256"]):
            if venv.exists():
                shutil.rmtree(venv)
            sh = ctx.shell(sc.log_path)
            sh.run([sys.executable, "-m", "venv", str(venv)], timeout=300)
            py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            sh.run([str(py), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check", str(whl)],
                   timeout=900)
            marker.write_text(info["sha256"] + "\n")
        sc.log(f"vamscli: wheel {info['wheel']} of build {b} (sha256 {info['sha256'][:16]})")
        return str(exe)
    return "vamscli"


def live_cli_smoke(ctx, env: str, jdir: Path, sc, live: dict | None = None,
                   builds: list[str | None] | None = None, role: str | None = None) -> dict:
    """Read-only vamscli smoke: setup -> login (password on stdin) -> auth status, features, databases.
    ``builds``: whose bundled VamsCLI wheel to run, first match wins (see :func:`resolve_vamscli`).
    ``role``: the identity that reads the web URL parameter and the sign-in secret. `vra test` leaves it unset
    (:func:`test_role`: ``testRoleArn`` when set). A deploy or rollback run passes ``"deploy"``: it runs where only
    the deploy identity is trusted (the pipeline's deploy job), and ``vra-test-<env>`` trusts only the env's test
    CI identity, so assuming it there is denied (E2E-050 B-050-27). The deploy role grants both reads
    (``VamsResourceNamesRead``, ``TestUserCredentials``)."""
    live = resolve_live(ctx, env, live, role)
    web = live.get("webUrl")
    if not web:
        write_junit(jdir / "live-cli.xml", "live-cli", [{"name": "config", "status": "skipped",
                                                         "message": "liveTest.webUrl not set"}])
        return {"skipped": "liveTest.webUrl not set"}
    if ctx.dry_run:
        return {"dryRun": True}
    profile = live.get("cliProfile") or f"vra-{env}"
    cli = resolve_vamscli(ctx, builds or [], sc)
    auth = live_auth(ctx, env, live, role)
    sh = ctx.shell(sc.log_path)
    cases = []
    steps = [("setup", [cli, "--profile", profile, "setup", web, "--force", "--skip-version-check", "--json-output"],
              None)]
    login = login_step(cli, profile, auth)
    if login:
        steps.append(login)
    steps += [("auth-status", [cli, "--profile", profile, "auth", "status", "--json-output"], None),
              ("features", [cli, "--profile", profile, "features", "list", "--json-output"], None),
              ("databases", [cli, "--profile", profile, "database", "list", "--json-output"], None)]
    broken = False
    for name, argv, stdin in steps:
        if broken:
            cases.append({"name": name, "status": "skipped", "message": "previous step failed"})
            continue
        t0 = time.monotonic()
        try:
            p = sh.run(argv, check=False, input_text=stdin, mutating=False, timeout=600)
        except FileNotFoundError:
            # a failed case, not a crash: the deploy's failure path then records the deploy and rolls back
            # (E2E-050 B-050-18: a bare FileNotFoundError in CodeBuild skipped both)
            cases.append({"name": name, "status": "failed", "time": time.monotonic() - t0,
                          "message": f"vamscli not found ({cli}): install VamsCLI on this runner or set VRA_VAMSCLI"})
            broken = True
            continue
        ok = p.returncode == 0
        cases.append({"name": name, "status": "passed" if ok else "failed", "time": time.monotonic() - t0,
                      "message": f"exit {p.returncode}", "output": (p.stdout or "") + (p.stderr or "")})
        if not ok and name in ("setup", "login"):
            broken = True
    write_junit(jdir / "live-cli.xml", "live-cli", cases)
    return {"cases": {c["name"]: c["status"] for c in cases}, "profile": profile}


def live_web(ctx, env: str, src: Path | None, jdir: Path, sc) -> dict:
    lt = resolve_live(ctx, env)
    if not lt.get("webUrl"):
        write_junit(jdir / "live-web.xml", "live-web", [{"name": "config", "status": "skipped",
                                                         "message": "liveTest.webUrl not set"}])
        return {"skipped": "liveTest.webUrl not set"}
    if src is None or not (src / "web" / "e2e").exists():
        write_junit(jdir / "live-web.xml", "live-web", [{"name": "source", "status": "skipped",
                                                         "message": "no web/e2e in the deployed source"}])
        return {"skipped": "no web/e2e"}
    if ctx.dry_run:
        return {"dryRun": True}
    auth = live_auth(ctx, env, lt)
    if auth.get("type") not in (None, "password"):
        write_junit(jdir / "live-web.xml", "live-web", [{"name": "auth", "status": "skipped",
                                                         "message": f"the web e2e suite signs in with a password; "
                                                                    f"liveTest.auth.type is {auth.get('type')}"}])
        return {"skipped": f"live-web needs password sign-in (auth {auth.get('type')})"}
    creds = {"username": auth.get("username"), "password": auth.get("password")}
    sh = ctx.shell(sc.log_path)
    web = src / "web"
    if not (web / "node_modules").exists():
        sh.run(["npm", "ci"], cwd=web)
    sh.run(["npx", "playwright", "install", "chromium"], cwd=web, check=False)
    out = jdir / "live-web.xml"
    out.parent.mkdir(parents=True, exist_ok=True)
    child_env = {"E2E_BASE_URL": lt["webUrl"], "PLAYWRIGHT_JUNIT_OUTPUT_NAME": str(out), "CI": "1"}
    if creds.get("username"):
        child_env.update({"E2E_USERNAME": creds["username"], "E2E_PASSWORD": creds.get("password") or ""})
    p = sh.run(["npx", "playwright", "test", "--reporter=junit"], cwd=web, env=child_env, check=False,
               mutating=False, timeout=5400)
    if not out.exists():
        write_junit(out, "live-web", [{"name": "playwright", "status": "passed" if p.returncode == 0 else "failed",
                                       "message": f"exit {p.returncode}", "output": (p.stdout or "") + (p.stderr or "")}])
    return {"exit": p.returncode}


def customer_hooks(ctx, run, env: str, jdir: Path, sc) -> dict:
    hooks = ctx.ledger.customer_tests()
    lt = ctx.ledger.env(env).get("liveTest") or {}
    ran = {}
    if ctx.dry_run:
        return {"dryRun": True, "hooks": [h["name"] for h in hooks]}
    hook_dir = jdir / "customer"
    for h in hooks:
        if h.get("envs") and env not in h["envs"]:
            continue
        hdir = hook_dir / h["name"]
        hdir.mkdir(parents=True, exist_ok=True)
        argv = h["cmd"] if isinstance(h["cmd"], list) else shlex.split(h["cmd"])
        child_env = {"VRA_ENV": env, "VRA_RUN_DIR": str(run.dir), "VRA_JUNIT_DIR": str(hdir),
                     "VAMS_API_URL": lt.get("apiUrl") or "", "VAMS_WEB_URL": lt.get("webUrl") or "",
                     "VAMSCLI_PROFILE": lt.get("cliProfile") or f"vra-{env}"}
        cwd = ctx.ledger.root / (h.get("cwd") or ".")
        t0 = time.monotonic()
        try:
            p = ctx.shell(sc.log_path).run(argv, cwd=cwd, env=child_env, check=False, mutating=False,
                                           timeout=int(float(h.get("timeoutMin", 30)) * 60))
            code, output = p.returncode, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired as exc:
            code, output = 124, f"timeout after {h.get('timeoutMin', 30)} min: {exc}"
        except FileNotFoundError as exc:
            code, output = 127, str(exc)
        if not any(hdir.glob("*.xml")):
            write_junit(hdir / f"{h['name']}.xml", f"customer.{h['name']}",
                        [{"name": h["name"], "status": "passed" if code == 0 else "failed", "time": time.monotonic() - t0,
                          "message": f"exit {code}", "output": output}])
        ran[h["name"]] = "passed" if code == 0 else "failed"
    return {"hooks": ran}


def do_test(ctx, run, env: str, suites: list[str] | None = None, prefix: str = "", build: str | None = None,
            live_override: dict | None = None, record: bool = True) -> dict:
    suites = suites or DEFAULT_LIVE_SUITES
    bad = [s for s in suites if s not in ALL_SUITES]
    if bad:
        raise UsageError(f"unknown suites {bad}; choose from {ALL_SUITES}")
    current = ctx.ledger.current_deploy(env)
    build = build or (current or {}).get("build")
    jdir = run.junit_dir / f"test-{env}"
    src = None
    needs_src = any(s in suites for s in ("unit", "synth", "live-web"))
    if needs_src and build:
        def _src(sc):
            from vra import build as build_mod, gitops
            m = build_mod.load_manifest(ctx, build)
            dest = ctx.scratch_dir / f"test-{build}" / "src"
            if ctx.dry_run or (dest / "web").exists():
                return {"src": str(dest)}
            g = gitops.ensure_repo(ctx, sc.log_path)
            gitops.archive_to(g, m["commit"], dest)
            return {"src": str(dest)}
        src = Path(run.step(f"{prefix}test.{env}.source", _src, f"source for tests on {env}")["src"])
    results: dict[str, Any] = {}
    for s in suites:
        sid = f"{prefix}test.{env}.{s}"
        if s == "unit":
            if src is None:
                run.skip(sid, "no deployed build to take source from")
                continue
            results[s] = run.step(sid, lambda sc: _soft(run_unit_suites, ctx, run, src, default_build_suites(), sc,
                                                        f"test-{env}/unit"), f"unit on {env}")
        elif s == "synth":
            run.skip(sid, "synth is verified by `vra build`; re-run build to re-synth")
        elif s == "live-cli":
            results[s] = run.step(sid, lambda sc: live_cli_smoke(ctx, env, jdir, sc, live_override, [build]),
                                  f"vamscli smoke {env}")
        elif s == "live-web":
            results[s] = run.step(sid, lambda sc: live_web(ctx, env, src, jdir, sc), f"playwright {env}")
        elif s == "customer":
            results[s] = run.step(sid, lambda sc: customer_hooks(ctx, run, env, jdir, sc), f"customer hooks {env}")

    def _summary(sc):
        summ = aggregate_junit(jdir)
        by_suite = aggregate_by_suite(jdir)
        failures = summ["failures"] + summ["errors"]
        missing = required_check(ctx, env, by_suite, suites)
        if failures:
            result = "FAILED"
        elif summ["executed"] == 0:
            result = "NO_TESTS"          # all skipped / nothing ran: never promotion evidence
        elif missing:
            result = "INCOMPLETE"        # passed, but a required suite did not run: not promotion evidence
        else:
            result = "SUCCEEDED"
        summ = {**summ, "suiteCounts": by_suite, "result": result, "requiredProblems": missing}
        if record:
            from vra.ledger import required_suites
            ctx.ledger.append_history(env, {"action": "test", "build": build, "runId": run.run_id, "suites": suites,
                                            "result": result, "tests": summ["tests"], "executed": summ["executed"],
                                            "passed": summ["passed"], "failures": failures,
                                            "suiteCounts": by_suite,
                                            "requiredSuites": required_suites(ctx.ledger, env)},
                                      dry_run=ctx.dry_run)
        if result != "SUCCEEDED" and not ctx.dry_run:
            if failures:
                why = f"{failures} failure(s)"
            elif result == "NO_TESTS":
                why = summ.get("reason") or "nothing executed"
            else:
                why = "; ".join(missing)
            raise TestsFailed(f"tests on {env}: {result} ({why})", {"failed": summ["failed"][:50],
                                                                    "requiredProblems": missing})
        return summ

    summary = run.step(f"{prefix}test.{env}.summary", _summary, f"test summary {env}")
    return {"env": env, "build": build, "suites": results, "summary": summary}


def _soft(fn, *args):
    """Run a unit suite inside `vra test` without aborting the other suites; failures show in JUnit."""
    try:
        return fn(*args)
    except TestsFailed as exc:
        return {"failed": True, **(exc.detail or {})}


@workflow("test")
def wf_test(ctx, run, args):
    # The env lock, like every other env workflow: a test run while another run deploys or restores the env would
    # test a half-changed env and record its result as promotion evidence for the env's current build (E2E-040).
    from vra.runner import env_lock
    with env_lock(ctx, run, args["env"]):
        return do_test(ctx, run, args["env"], args.get("suites"))
