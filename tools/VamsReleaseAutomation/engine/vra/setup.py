# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vra setup`: first-run wizard. Prompts for every ledger value (or reads an
``--answers`` YAML), writes the ledger skeleton, then runs ``ledger validate`` and ``preflight --all``.

Partition-generic: the partition defaults from the region (us-gov-* -> aws-us-gov, eusc-* -> aws-eusc,
cn-* -> aws-cn, else aws) and the template base follows the partition; all ARNs are built from it.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from vra import data as vra_data
from pathlib import Path
from typing import Any, Callable

from vra import util
from vra.errors import UsageError
from vra.ledger import DEFAULT_UPSTREAM, TEMPLATE_FILES, Ledger, partition_for_region

DEFAULT_ENVS = ["dev", "dev-preview", "test", "prod"]
ENV_PRESETS = {
    "dev": {"lane": "release", "promotesFrom": None, "autoDeploy": True, "requiresApproval": False,
            "rollbackPolicy": "auto"},
    "dev-preview": {"lane": "preview", "promotesFrom": None, "autoDeploy": True, "requiresApproval": False,
                    "rollbackPolicy": "auto"},
    "test": {"lane": "release", "promotesFrom": "dev", "autoDeploy": False, "requiresApproval": True,
             "rollbackPolicy": "ask", "allowedSources": ["prod"]},
    "prod": {"lane": "release", "promotesFrom": "test", "autoDeploy": False, "requiresApproval": True,
             "rollbackPolicy": "ask"},
}
TEMPLATE_FOR_PARTITION = {"aws-us-gov": "govcloud", "aws-eusc": "eusovereign"}


class Asker:
    def __init__(self, answers: dict, interactive: bool, prompt: Callable[[str, str | None], str] | None = None):
        self.answers = answers or {}
        self.interactive = interactive
        self.prompt = prompt or _default_prompt
        self.missing: list[str] = []
        self.asked: list[str] = []

    def get(self, path: str, question: str, default: Any = None, required: bool = False, kind: str = "str",
            choices: list[str] | None = None) -> Any:
        self.asked.append(path)
        val = util.get_path(self.answers, path) if "." in path else self.answers.get(path)
        if val is None and self.interactive:
            d = _fmt(default)
            hint = f" [{'/'.join(choices)}]" if choices else ""
            raw = self.prompt(f"{question}{hint}", d)
            val = raw if raw not in ("", None) else default
            val = _coerce(val, kind)
        elif val is None:
            val = default
        else:
            val = _coerce(val, kind)
        if val is None and choices:
            val = default  # "none" is a real choice value, not "unset"
        if choices and val is not None and val not in choices:
            raise UsageError(f"{path}: {val!r} is not one of {choices}")
        if required and val in (None, "", []):
            self.missing.append(path)
        return val


def _fmt(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list):
        return ",".join(map(str, v))
    return str(v)


def _coerce(v: Any, kind: str) -> Any:
    if v is None:
        return None
    if kind == "bool":
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("y", "yes", "true", "1")
    if kind == "list":
        if isinstance(v, list):
            return v
        return [x.strip() for x in str(v).split(",") if x.strip()]
    if kind == "int":
        return int(v)
    if isinstance(v, str):
        v = v.strip()
        # a blank answer in the answers file means "unset", like a blank answer at the prompt
        return None if v.lower() in ("", "none", "null", "-") else v
    return v


def _default_prompt(question: str, default: str | None) -> str:
    sys.stderr.write(f"{question}{f' ({default})' if default else ''}: ")
    sys.stderr.flush()
    return sys.stdin.readline().strip()


def repo_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else None


def collect(ctx, answers: dict, interactive: bool) -> dict:
    a = Asker(answers, interactive, ctx.prompt)
    proj: dict[str, Any] = {"schemaVersion": 1}
    proj["project"] = {"name": a.get("project.name", "Project name", answers.get("project") if isinstance(
        answers.get("project"), str) else "vams-fork", True)}
    proj["upstream"] = {"url": a.get("upstream", "Upstream VAMS git URL", DEFAULT_UPSTREAM, True)
                        if not isinstance(answers.get("upstream"), dict) else answers["upstream"]["url"]}
    fork_url = answers["fork"]["url"] if isinstance(answers.get("fork"), dict) else \
        a.get("fork", "Customer fork git URL", None, True)
    proj["fork"] = {"url": fork_url}
    gtype = a.get("githost.type", "Git host", "github", True, choices=["github", "gitlab", "bitbucket", "codecommit",
                                                                     "local"])
    gh: dict[str, Any] = {"type": gtype}
    if gtype in ("github", "gitlab", "bitbucket"):
        gh["repo"] = a.get("githost.repo", "Repository (owner/name)", repo_from_url(fork_url), True)
    if gtype == "bitbucket":
        gh["tokenSecret"] = a.get("githost.tokenSecret", "Secrets Manager secret NAME holding the Bitbucket token",
                                  None, True)
    if gtype == "codecommit":
        gh["repositoryName"] = a.get("githost.repositoryName", "CodeCommit repository name",
                                     (repo_from_url(fork_url) or "").split("/")[-1] or None, True)
        gh["region"] = a.get("githost.region", "CodeCommit region", None)
    if gtype == "gitlab":
        gh["apiUrl"] = a.get("githost.apiUrl", "GitLab URL (blank for gitlab.com)", None)
    proj["githost"] = gh
    proj["trunk"] = a.get("trunk", "Fork trunk branch", "main", True)
    proj["tagPrefix"] = a.get("tagPrefix", "Release tag prefix", "cust-")
    proj["lanes"] = {"release": {"source": a.get("lanes.release.source", "Upstream release branch", "main")},
                     "preview": {"source": a.get("lanes.preview.source", "Upstream preview branch", "development"),
                                 "targetBranch": a.get("lanes.preview.targetBranch", "Fork preview branch",
                                                       "preview/main")}}
    nt = a.get("notifications.type", "Notification channel", "none",
               choices=["none", "sns", "email", "slack-webhook", "teams-webhook"])
    proj["notifications"] = {"type": nt}
    if nt != "none":
        proj["notifications"]["target"] = a.get("notifications.target", "Secrets Manager secret NAME holding the "
                                                "topic ARN / addresses / webhook URL", None, True)
        proj["notifications"]["region"] = a.get("notifications.region", "Region of that secret", None)
    if nt == "email":
        proj["notifications"]["from"] = a.get("notifications.from", "SES verified sender address", None, True)
    sst = a.get("stateStore.type", "State backend (home = the shared home folder; s3 = S3 + DynamoDB)", "home",
                choices=["home", "local", "s3"])
    sst = "home" if sst == "local" else sst
    proj["stateStore"] = {"type": sst}
    if sst == "s3":
        proj["stateStore"].update({"bucket": a.get("stateStore.bucket", "State bucket", None, True),
                                   "prefix": a.get("stateStore.prefix", "State key prefix", "vra"),
                                   "lockTable": a.get("stateStore.lockTable", "DynamoDB lock table", "vra-locks", True),
                                   "region": a.get("stateStore.region", "State region", None)})
    tools = a.get("toolsAccount", "Tools/CI account id (blank if none)", None)
    if tools:
        proj["toolsAccount"] = str(tools)
    proj["build"] = {"ash": a.get("build.ash", "Run ASH security scan in builds", False, kind="bool"),
                     "sbom": a.get("build.sbom", "Generate SBOM (CycloneDX) in builds", True, kind="bool"),
                     "signTags": a.get("build.signTags", "Sign release tags (git tag -s)", False, kind="bool")}
    sign_key = a.get("build.signing.kmsKeyArn",
                     "KMS signing key ARN for manifests and release tags (ECC_NIST_P256; blank = no signing; "
                     "recommended for prod)", None)
    if sign_key:
        proj["build"]["signing"] = {"kmsKeyArn": sign_key,
                                    "tags": a.get("build.signing.tags", "Also sign release tags with it", True,
                                                  kind="bool")}
        proj["build"]["signTags"] = False

    env_answers = answers.get("envs") if isinstance(answers.get("envs"), dict) else None
    names = list(env_answers) if env_answers else a.get("envNames", "Environment names", DEFAULT_ENVS, True, "list")
    envs: dict[str, dict] = {}
    prev_account, prev_region = None, None
    prev_release = None
    for n in names:
        # an env without a preset promotes from the previous release-lane env (a chain, not unrelated roots)
        preset = ENV_PRESETS.get(n, {"lane": "release", "promotesFrom": prev_release, "autoDeploy": False,
                                     "requiresApproval": True, "rollbackPolicy": "ask"})
        sub = Asker((env_answers or {}).get(n) or {}, interactive, ctx.prompt)
        q = lambda key, text, default=None, required=False, kind="str", choices=None: sub.get(  # noqa: E731
            key, f"[{n}] {text}", default, required, kind, choices)
        e: dict[str, Any] = {}
        e["account"] = str(q("account", "AWS account id", prev_account, True) or "")
        e["region"] = q("region", "Region", prev_region or "us-east-1", True)
        part_default = partition_for_region(e["region"] or "") or "aws"
        e["partition"] = q("partition", "Partition", part_default, True)
        e["profile"] = q("profile", "Named AWS profile (blank to assume deployRoleArn)", None)
        if not e["profile"]:
            e["deployRoleArn"] = q("deployRoleArn", "Deploy role ARN",
                                   f"arn:{e['partition']}:iam::{e['account']}:role/vra-deploy" if e["account"] else None)
        e["templateBase"] = q("templateBase", "VAMS config template",
                              TEMPLATE_FOR_PARTITION.get(e["partition"], "commercial"), True,
                              choices=["commercial", "govcloud", "eusovereign", "custom"])
        if e["templateBase"] == "custom":
            e["templatePath"] = q("templatePath", "Repo-relative template path", "infra/config/custom/config.template.json")
        e["configName"] = q("configName", "VAMS config name (config.json `name`)", "vams")
        e["baseStackName"] = q("baseStackName", "VAMS baseStackName", re.sub(r"[^a-z0-9]", "", n.lower()) or n, True)
        e["lane"] = q("lane", "Lane", preset["lane"], True, choices=["release", "preview"])
        pf = q("promotesFrom", "Promotes from env (blank for none)", preset["promotesFrom"])
        if pf and pf not in names:
            raise UsageError(f"envs.{n}.promotesFrom: {pf!r} is not one of the environments {names}")
        e["promotesFrom"] = pf or None
        e["autoDeploy"] = q("autoDeploy", "Auto-deploy from cron (never for gated envs)", preset["autoDeploy"],
                            kind="bool")
        e["requiresApproval"] = q("requiresApproval", "Require approval to deploy", preset["requiresApproval"],
                                  kind="bool")
        e["approvers"] = q("approvers", "Approver ids (comma separated)", [], e["requiresApproval"], "list")
        derived = "preview" if e["lane"] == "preview" else ("prod" if e["requiresApproval"] and n.startswith("prod")
                                                              else "nonprod")
        e["tier"] = q("tier", "Environment tier (prod: no data copies into it, no scanner waivers)", derived, True,
                      choices=["prod", "nonprod", "preview"])
        if e["requiresApproval"] and not e["promotesFrom"]:
            e["rootEnv"] = q("rootEnv", "This gated env has no promotesFrom: deployed directly on purpose?", False,
                             kind="bool") or None
        if e["requiresApproval"]:
            # gated envs: build-only synth credentials, data owners, CDK permissions boundary
            e["synthRoleArn"] = q("synthRoleArn", "Build-only synth role ARN (blank: readOnlyRoleArn, else no "
                                                  "credentials at synth)", None)
            owners = q("dataOwners", "Data owners who approve copies OUT of this env (blank: the approvers)", [],
                       kind="list")
            e["dataOwners"] = owners or None
            e["permissionsBoundaryName"] = q("permissionsBoundaryName",
                                             "CDK permissions boundary policy name (blank if none)", None)
        e["iam"] = ask_iam(q, e)
        e["deployMode"] = q("deployMode", "Default deploy mode", "in-place", choices=["in-place", "ab"])
        e["rollbackPolicy"] = q("rollbackPolicy", "Rollback policy", preset["rollbackPolicy"], choices=["auto", "ask"])
        e["dataCopy"] = {"allowedSources": q("dataCopy.allowedSources", "Envs allowed to copy data INTO this env",
                                             preset.get("allowedSources", []), kind="list"),
                         "maskHook": None}
        if e["dataCopy"]["allowedSources"]:
            e["dataCopy"]["stagingBucket"] = q("dataCopy.stagingBucket", "Staging bucket for PITR exports", None)
        # VAMS asset buckets can be registered at any time (s3AssetBucketsStorage), also customer-named buckets and
        # buckets in other accounts with their own KMS keys; the stock role templates only grant the stack's own
        granted = q("registeredBuckets.granted",
                    "Have the vra roles of this env (deploy role, data-copy role) been granted access to the asset "
                    "buckets registered in VAMS that are NOT named after the core stack, and to their KMS keys?",
                    False, kind="bool")
        rb: dict[str, Any] = {"granted": bool(granted), "bucketArns": [], "kmsKeyArns": []}
        if granted:
            rb["bucketArns"] = q("registeredBuckets.bucketArns",
                                 f"Bucket ARNs or patterns granted (comma separated, * allowed, e.g. "
                                 f"arn:{e['partition']}:s3:::example-assets-*)", [], True, "list")
            rb["kmsKeyArns"] = q("registeredBuckets.kmsKeyArns",
                                 "KMS key ARNs of those buckets granted (comma separated; blank for SSE-S3 or keys in "
                                 "this env's own account and region)", [], kind="list")
        e["registeredBuckets"] = rb
        e["liveTest"] = {"webUrl": q("liveTest.webUrl", "Web URL for live tests (blank if unknown yet)", None),
                         "apiUrl": q("liveTest.apiUrl", "API URL for live tests", None),
                         "cliProfile": q("liveTest.cliProfile", "vamscli profile name", f"vra-{n}")}
        auth_type = q("liveTest.auth.type", "Live-test sign-in (password: Cognito user with a local password; "
                                            "apiKey: a VAMS API key, needed for MFA, SAML/OIDC or external IdP "
                                            "pools; token: a command that prints a VAMS token)", "password",
                      choices=["password", "apiKey", "token", "none"])
        if auth_type == "password":
            e["liveTest"]["auth"] = {"type": "password", "secret": q(
                "liveTest.auth.secret", "Secrets Manager secret NAME with {username,password}", None)}
        elif auth_type == "apiKey":
            e["liveTest"]["auth"] = {"type": "apiKey", "secret": q(
                "liveTest.auth.secret", "Secrets Manager secret NAME with {userId,apiKey}", None, True)}
        elif auth_type == "token":
            e["liveTest"]["auth"] = {"type": "token", "userId": q("liveTest.auth.userId", "VAMS user id", None, True),
                                     "tokenCommand": q("liveTest.auth.tokenCommand",
                                                       "Command that prints the token (comma separated argv)", None,
                                                       True, "list")}
        cw = q("changeWindow", "Change window cron (blank for none)", None)
        if cw:
            e["changeWindow"] = {"cron": cw, "durationMinutes": int(q("changeWindowMinutes", "Window length minutes",
                                                                     120, kind="int")),
                                 "timezone": q("changeWindowTimezone", "Window timezone", "UTC")}
        e["adminEmailAddress"] = q("adminEmailAddress", "VAMS admin e-mail (config.json app.adminEmailAddress)",
                                   "admin@example.com")
        a.missing += [f"envs.{n}.{m}" for m in sub.missing]
        envs[n] = e
        prev_account, prev_region = e["account"], e["region"]
        if e["lane"] == "release":
            prev_release = n
    if a.missing:
        raise UsageError("setup is missing required values: " + ", ".join(a.missing) +
                         " (answer the prompts or add them to --answers)")
    return {"project": proj, "envs": envs}


IAM_MODE_QUESTION = ("IAM mode (standard: VAMS creates its roles; boundary: VAMS creates them under IT's permission "
                     "boundary and role name prefix; precreated: IT creates every VAMS role; bootstrap: IT creates "
                     "the CDK bootstrap roles; combine with +, e.g. boundary+bootstrap)")


def ask_iam(q, e: dict) -> dict | None:
    """Per-env IAM questions (env.yaml ``iam``; vra.iam). None for standard."""
    from vra import iam
    raw = q("iam.mode", IAM_MODE_QUESTION, "standard")
    try:
        modes = iam.parse_modes(raw)
    except Exception as exc:  # noqa: BLE001 - re-raised as a setup answer problem
        raise UsageError(str(getattr(exc, "message", exc))) from exc
    if modes == ["standard"]:
        return None
    out: dict[str, Any] = {"mode": "+".join(modes)}
    if "boundary" in modes or "precreated" in modes:
        out["boundaryArn"] = q("iam.boundaryArn", "Permission boundary policy ARN from IT (blank: IT returns it "
                                                  f"with the role package; e.g. arn:{e['partition']}:iam::"
                                                  f"{e['account']}:policy/<boundary>)", None)
        if "precreated" in modes:
            # required: the scoped CDK execution policy passes IT's roles only by this prefix (vra.iam)
            out["rolePrefix"] = q("iam.rolePrefix", "Role name prefix of IT's roles (required for precreated, "
                                                    "e.g. ITVAMS-)", None, True)
        else:
            out["rolePrefix"] = q("iam.rolePrefix", "Role name prefix IT requires (blank for none)", None)
    if "bootstrap" in modes:
        cli = q("iam.cliCredentialsSynthesizer", "Deploy without CDK bootstrap roles (CliCredentialsStackSynthesizer: "
                                                 "the deploy role needs the CDK execution permissions itself)?",
                False, kind="bool")
        out["cliCredentialsSynthesizer"] = bool(cli)
        bs: dict[str, Any] = {"qualifier": q("iam.bootstrap.qualifier", "CDK bootstrap qualifier", "hnb659fds")}
        if not cli and q("iam.bootstrap.have", "Has IT already created the bootstrap roles (enter their ARNs now; "
                                                "no: they come back with the IT role package)?", False, kind="bool"):
            for key in iam.BOOTSTRAP_ROLES.values():
                bs[key] = q(f"iam.bootstrap.{key}", f"Bootstrap {key}", None, True)
        out["bootstrap"] = {k: v for k, v in bs.items() if v}
    return {k: v for k, v in out.items() if v not in (None, "", {})} or {"mode": "+".join(modes)}


def _template(ctx, base: str, template_path: str | None) -> dict | None:
    repo = ctx.repo_dir
    if not (repo / ".git").exists():
        return None
    rel = template_path or TEMPLATE_FILES.get(base) or TEMPLATE_FILES["commercial"]
    import subprocess
    for ref in ("refs/remotes/upstream/main", "refs/remotes/origin/main", "HEAD"):
        p = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{rel}"], capture_output=True, text=True)
        if p.returncode == 0:
            try:
                return json.loads(p.stdout)
            except json.JSONDecodeError:
                return None
    return None


def write_ledger(ctx, data: dict, force: bool = False) -> list[str]:
    root = ctx.ledger.root
    written: list[str] = []

    def put(rel: str, text: str) -> None:
        p = root / rel
        if p.exists() and not force:
            return
        if not ctx.dry_run:
            util.atomic_write(p, text)
        written.append(rel)

    put("vra.yaml", util.dump_yaml(data["project"]))
    for n, e in data["envs"].items():
        admin = e.pop("adminEmailAddress", "admin@example.com")
        put(f"envs/{n}/env.yaml", util.dump_yaml({k: v for k, v in e.items() if v is not None}))
        cfg = _template(ctx, e["templateBase"], e.get("templatePath")) or {"name": e.get("configName") or "vams",
                                                                           "env": {}, "app": {}}
        cfg["name"] = e.get("configName") or cfg.get("name") or "vams"
        cfg.setdefault("env", {})
        cfg["env"]["account"] = e["account"]
        cfg["env"]["region"] = e["region"]
        cfg.setdefault("app", {})
        cfg["app"]["baseStackName"] = e["baseStackName"]
        cfg["app"]["adminEmailAddress"] = admin
        put(f"envs/{n}/overlay/infra/config/config.json", json.dumps(cfg, indent=4) + "\n")
    put("path-policy.yaml", vra_data.text("defaults", "path-policy.yaml"))
    put("core-patches.md", "# Core patch register\n\nFork edits OUTSIDE the VAMS fork lanes. Each entry: path in "
                           "backticks, reason, owner, upstream issue/PR. This list should trend to zero.\n\n"
                           "| Path | Reason | Owner | Upstream ref |\n|---|---|---|---|\n")
    put("tests/customer.yaml", "# Customer regression hooks: [{name, cmd, envs, timeoutMin}]\n"
                               "# Env vars: VRA_ENV VRA_RUN_DIR VRA_JUNIT_DIR VAMS_API_URL VAMS_WEB_URL VAMSCLI_PROFILE\n"
                               "[]\n")
    put("approvals/README.md", "# Approvals\n\n`approvals/<env>/<id>.yaml` = {build, env, approver, approvedAt, "
                               "scope, expiresAt?, ticket?, notes?, source?}. `build` is the gate subject: the build "
                               "id (deploy/promote/rollback), `restore:<checkpointId>`, `data-copy:<sourceEnv>` "
                               "(target env) or `data-copy-to:<targetEnv>` (source env, from its `dataOwners`); "
                               "the default file name is the subject with `:` replaced by `_`. `scope` lists the "
                               "actions covered (deploy, promote, rollback, restore, data-copy, emergency, "
                               "allow-replacements, accept-drift). `expiresAt` defaults to `approvedAt` + the env's "
                               "`approvalMaxAgeHours` (72). In CI, `source` must name the pipeline run (the CI "
                               "approval writer fills it in). A record is used once. The approver must be listed in "
                               "the env's `approvers` and must not be the person who starts the run.\n")
    put("README.md", f"# {data['project']['project']['name']} release ledger\n\nManaged by `vra` "
                     f"(vams-release-automation). Secrets are never stored here, only Secrets Manager / SSM names.\n")
    return written


def _make_home(ctx, answers: dict, interactive: bool, home_arg: str | None) -> dict:
    """Create the shared home (vra-home.yaml + layout) and point ctx at it. Returns what was done."""
    from vra import home as home_mod
    a = Asker(answers, interactive, ctx.prompt)
    root = Path(home_arg or a.get("home.path", "Shared home folder (ledger + state, synced across machines)",
                                  str(Path.home() / "vams-release-home"), True)).expanduser()
    stype = a.get("home.sync.type", "How the home folder is synced (none: one machine; git: a git remote; command: "
                                    "your own pull/push commands)", "none", choices=["none", "git", "command"])
    sync: dict[str, Any] = {"type": stype}
    remote = None
    if stype == "git":
        remote = a.get("home.sync.remote", "Git remote URL of the home (blank: set it up later)", None)
    if stype == "command":
        sync["pull"] = a.get("home.sync.pull", "Pull command", None, True)
        sync["push"] = a.get("home.sync.push", "Push command", None, True)
    if a.missing:
        raise UsageError("setup is missing required values: " + ", ".join(a.missing))
    out: dict[str, Any] = {"home": str(root.resolve()), "sync": stype}
    if ctx.dry_run:
        return out
    if (root / home_mod.HOME_FILE).exists():
        h = home_mod.Home.open(root)
        out["existing"] = True
    else:
        h = home_mod.Home.create(root, sync)
    ctx.home = h
    ctx.ledger = Ledger(h.ledger_dir)
    if hasattr(ctx.aws, "ledger"):
        ctx.aws.ledger = ctx.ledger
    from vra.context import _History
    ctx.ledger.history_backend = _History(ctx)
    ctx._backend = None
    if stype == "git" and not (root / ".git").exists():
        home_mod._git(h.root, "init", "-q")
        if remote:
            home_mod._git(h.root, "remote", "add", "origin", remote)
        out["remote"] = remote
    h.register_host()
    out["pointer"] = str(home_mod.write_pointer(h.root))
    return out


def run_setup(ctx, answers_path: str | None, force: bool = False, skip_preflight: bool = False,
              home_arg: str | None = None, legacy: bool | None = None) -> dict:
    answers = util.read_yaml(Path(answers_path)) if answers_path else {}
    if answers_path and not isinstance(answers, dict):
        raise UsageError("--answers must be a YAML mapping")
    interactive = not ctx.non_interactive and sys.stdin.isatty() if ctx.prompt is None else not ctx.non_interactive
    if legacy is None:
        legacy = home_arg is None
    home_info = None if legacy else _make_home(ctx, answers or {}, interactive, home_arg)
    data = collect(ctx, answers or {}, interactive)
    written = write_ledger(ctx, json.loads(json.dumps(data)), force)
    out: dict[str, Any] = {"ledger": str(ctx.ledger.root), "written": written, "dryRun": ctx.dry_run}
    if home_info:
        out["home"] = home_info
        if not ctx.dry_run:
            # `published` is the remote's real state, never "committed locally" (found live: a git home with no
            # upstream yet reported published: true before anything was pushed)
            try:
                ctx.home.publish("vra setup", paths=["."])
                out["home"].update(ctx.home.sync.publish_state())
            except Exception as exc:  # noqa: BLE001 - the home is written; pushing can be retried
                msg = getattr(exc, "message", None) or str(exc)
                out["home"].update({"published": False, "reason": f"push refused: {msg}",
                                    "publishCommand": "vra home sync", "publishError": msg})
    if ctx.dry_run:
        out["ledgerPreview"] = data
        return out
    from vra.context import _History
    ctx.ledger = Ledger(ctx.ledger.root)
    ctx.ledger.history_backend = _History(ctx)
    if hasattr(ctx.aws, "ledger"):
        ctx.aws.ledger = ctx.ledger
    out["validation"] = ctx.ledger.validate()
    out["notes"] = registered_bucket_notes(data["envs"])
    return out


def registered_bucket_notes(envs: dict) -> list[str]:
    """What setup's registered-bucket answers mean for the first bucket phase of each env."""
    notes = []
    for n, e in envs.items():
        rb = e.get("registeredBuckets") or {}
        if not rb.get("granted"):
            notes.append(f"{n}: registeredBuckets.granted=false. The first backup checkpoint, restore, data copy or "
                         f"deploy with data migrations lists the env's registered asset buckets; any bucket not named "
                         f"after the core stack (or a KMS key outside {n}'s account) gets an IT request with the exact "
                         f"actions per bucket and key, and the phase stops (exit 10) until it is granted and "
                         f"recorded in envs/{n}/env.yaml registeredBuckets")
        else:
            notes.append(f"{n}: registered buckets granted for {', '.join(rb.get('bucketArns') or []) or '(none)'}; "
                         f"each bucket phase still re-checks, a newly registered bucket outside these patterns stops "
                         f"it with an IT request")
    return notes


__all__ = ["run_setup", "collect", "write_ledger", "shutil"]
