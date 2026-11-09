# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ledger: the customer-private repo with project + per-env config.

Schema validation (jsonschema) plus semantic checks the schema cannot express
(promotion graph, gates, partition/region consistency, no inline secrets).
"""

from __future__ import annotations

import copy
import difflib
import json
import re
import shutil
from vra import data as vra_data
from pathlib import Path, PurePosixPath
from typing import Any

import jsonschema
import yaml

from vra import util
from vra.errors import ConfigError, UsageError

DEFAULT_UPSTREAM = "https://github.com/awslabs/visual-asset-management-system.git"

LANE_DEFAULTS = {
    "release": {"source": "main", "vendorBranch": "vendor/vams-main", "targetBranch": None, "autoMerge": False},
    "preview": {"source": "development", "vendorBranch": "vendor/vams-development", "targetBranch": "preview/main",
                "autoMerge": True},
}

ENV_DEFAULTS: dict[str, Any] = {
    "partition": None, "deployRoleArn": None, "profile": None, "promotesFrom": None, "approvers": [],
    "deployMode": "in-place", "rollbackPolicy": "auto", "backupBeforeDeploy": True, "cognitoExport": False,
    "dataCopy": {"allowedSources": [], "maskHook": None, "subsetHook": None, "copyUserRoles": False,
                 "batchOperationsThreshold": 10000},
    "liveTest": {}, "changeWindow": None, "configName": None, "baseStackName": None, "migrations": {},
    "tier": None, "fips": "auto", "rootEnv": False, "allowGenericCi": False, "allowCrossPartitionPromotion": False,
    "synthEnv": {},
}

TIERS = ("prod", "nonprod", "preview")

# Environment variables VAMS infra/config/config.ts reads at synth: each one changes the synthesized template.
# The engine never lets the operator's environment set them; a value recorded in env.yaml ``synthEnv`` is used
# instead, and any other one found in the operator environment stops the build.
VAMS_CONFIG_ENV_VARS = ("STACK_NAME", "REGION", "ADMIN_EMAIL_ADDRESS", "ADMIN_USER_ID", "CRED_TOKEN_TIMEOUT_SECONDS",
                        "PRESIGNED_URL_TIMEOUT_SECONDS", "AWS_USE_FIPS_ENDPOINT", "AWS_USE_WAF",
                        "DEFAULT_USER_ROLE_NAME")

TEMPLATE_FILES = {
    "commercial": "infra/config/config.template.commercial.json",
    "govcloud": "infra/config/config.template.govcloud.json",
    "eusovereign": "infra/config/config.template.eusovereign.json",
}

CONFIG_OVERLAY_PATH = "infra/config/config.json"

# Keys whose literal value would be a secret. Names of secrets (…Secret, …SecretName, …SecretArn) are fine.
_SECRET_KEY_RE = re.compile(r"(?i)^(password|passwd|clientsecret|client_secret|token|apikey|api_key|"
                            r"secretaccesskey|aws_secret_access_key|privatekey|private_key)$")
_ACCESS_KEY_RE = re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")


def schema(name: str) -> dict:
    return json.loads(vra_data.text("schemas", f"{name}.schema.json"))


def partition_for_region(region: str) -> str | None:
    """Partition of a region from botocore's partition data (every partition, including EU Sovereign and ISO)."""
    from vra import aws
    return aws.partition_for_region(region)


class Ledger:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self._project: dict | None = None
        self._envs: dict[str, dict] = {}

    # ------------------------------------------------------------------ loading
    @property
    def exists(self) -> bool:
        return (self.root / "vra.yaml").exists()

    def raw_project(self) -> dict:
        p = self.root / "vra.yaml"
        if not p.exists():
            raise ConfigError(f"ledger not found: {p} (run `vra setup` or pass --ledger)")
        data = util.read_yaml(p) or {}
        if not isinstance(data, dict):
            raise ConfigError("vra.yaml must be a mapping")
        return data

    @property
    def project(self) -> dict:
        if self._project is None:
            self._project = self._normalize_project(self.raw_project())
        return self._project

    @staticmethod
    def _normalize_project(raw: dict) -> dict:
        p = copy.deepcopy(raw)
        if isinstance(p.get("project"), str):
            p["project"] = {"name": p["project"]}
        if isinstance(p.get("upstream"), str):
            p["upstream"] = {"url": p["upstream"]}
        p.setdefault("upstream", {"url": DEFAULT_UPSTREAM})
        if isinstance(p.get("fork"), str):
            p["fork"] = {"url": p["fork"]}
        p.setdefault("trunk", "main")
        p.setdefault("tagPrefix", "")
        lanes = p.setdefault("lanes", {})
        for lane, d in LANE_DEFAULTS.items():
            merged = dict(d, enabled=lane in lanes or lane == "release")
            merged.update({k: v for k, v in (lanes.get(lane) or {}).items() if v is not None})
            if lane == "release" and not merged.get("targetBranch"):
                merged["targetBranch"] = p["trunk"]
            lanes[lane] = merged
        p.setdefault("notifications", {"type": "none"})
        p.setdefault("stateStore", {"type": "local"})
        if p["stateStore"].get("type") in ("local", "home", None):
            p["stateStore"]["type"] = "home"  # the shared home folder (vra-home.yaml); `local` is its old name
        p.setdefault("build", {})
        p["build"].setdefault("ash", False)
        p["build"].setdefault("sbom", True)
        p["build"].setdefault("signTags", False)
        p["build"].setdefault("pushTags", True)
        p.setdefault("cdk", {})
        p["cdk"].setdefault("qualifier", "hnb659fds")
        gh = p.setdefault("githost", {"type": "local"})
        gh.setdefault("pushVendorBranches", True)
        return p

    def env_names(self) -> list[str]:
        d = self.root / "envs"
        if not d.exists():
            return []
        return sorted(x.name for x in d.iterdir() if (x / "env.yaml").exists())

    def raw_env(self, name: str) -> dict:
        p = self.root / "envs" / name / "env.yaml"
        if not p.exists():
            raise UsageError(f"unknown env {name!r} (no {p})")
        data = util.read_yaml(p) or {}
        if not isinstance(data, dict):
            raise ConfigError(f"{p} must be a mapping")
        return data

    def env(self, name: str) -> dict:
        if name not in self._envs:
            raw = self.raw_env(name)
            e = copy.deepcopy(ENV_DEFAULTS)
            for k, v in raw.items():
                if isinstance(v, dict) and isinstance(e.get(k), dict):
                    e[k] = util.deep_merge(e[k], v)
                else:
                    e[k] = v
            e["name"] = name
            e["account"] = str(e.get("account", ""))
            cfg = self.vams_config(name)
            if cfg:
                e["configName"] = e.get("configName") or cfg.get("name") or "vams"
                e["baseStackName"] = e.get("baseStackName") or (cfg.get("app") or {}).get("baseStackName")
            e["configName"] = e.get("configName") or "vams"
            if e.get("tier") is None:
                e["tierDerived"] = True
            self._envs[name] = e
        return self._envs[name]

    # ------------------------------------------------------------------ paths
    def env_dir(self, env: str) -> Path:
        return self.root / "envs" / env

    def overlay_dir(self, env: str) -> Path:
        return self.env_dir(env) / "overlay"

    def history_path(self, env: str) -> Path:
        return self.env_dir(env) / "history.jsonl"

    def approval_path(self, env: str, build: str) -> Path:
        return self.root / "approvals" / env / f"{build}.yaml"

    def vams_config(self, env: str) -> dict | None:
        p = self.overlay_dir(env) / CONFIG_OVERLAY_PATH
        if not p.exists():
            return None
        try:
            return util.read_json(p)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{p} is not valid JSON: {exc}") from exc

    # ------------------------------------------------------------------ derived VAMS names
    def identity_problems(self, env: str) -> list[str]:
        """Where env.yaml and the overlay config.json disagree on a value that names VAMS resources.

        Synth reads only the overlay; the engine names stacks, SSM paths and checkpoints from env.yaml. Both must
        say the same, or the engine would check, back up and migrate a different stack from the one it deploys."""
        raw = self.raw_env(env)
        cfg = self.vams_config(env) or {}
        if not cfg:
            return []
        out = []
        pairs = (("configName", raw.get("configName"), cfg.get("name"), "name"),
                 ("baseStackName", raw.get("baseStackName"), (cfg.get("app") or {}).get("baseStackName"),
                  "app.baseStackName"),
                 ("region", raw.get("region"), (cfg.get("env") or {}).get("region"), "env.region"),
                 ("account", str(raw.get("account")) if raw.get("account") is not None else None,
                  str((cfg.get("env") or {}).get("account")) if (cfg.get("env") or {}).get("account") else None,
                  "env.account"))
        for key, mine, theirs, where in pairs:
            if mine and theirs and str(mine) != str(theirs):
                out.append(f"envs/{env}: env.yaml {key} {mine!r} != overlay config.json {where} {theirs!r}")
        return out

    def stack_suffix(self, env: str, base_override: str | None = None) -> str:
        e = self.env(env)
        if base_override is None:
            probs = self.identity_problems(env)
            if probs:
                raise ConfigError("; ".join(probs) + " (the deployed stack name would not match the one the engine "
                                  "checks; align them, `vra ledger import` derives env.yaml from the config)")
        base = base_override or e.get("baseStackName")
        if not base:
            raise ConfigError(f"env {env}: baseStackName unknown (set it in env.yaml or overlay config.json)")
        return f"{base}-{e['region']}"

    def core_stack_name(self, env: str, base_override: str | None = None) -> str:
        # infra/bin/infra.ts: `${config.name}-core-${config.app.baseStackName}`; config.ts appends -<region>.
        return f"{self.env(env)['configName']}-core-{self.stack_suffix(env, base_override)}"

    def ssm_prefix(self, env: str, base_override: str | None = None) -> str:
        # /{configName}-{baseStackName}/resourceNames (baseStackName already region-suffixed by config.ts)
        return f"/{self.env(env)['configName']}-{self.stack_suffix(env, base_override)}/resourceNames"

    # ------------------------------------------------------------------ history
    # History lives in the state backend only (the shared home's state/history/<env>/<seq>-<run>.json, or the
    # S3 store): one write-once record per action, numbered per env. Nothing in the ledger counts as evidence;
    # a leftover envs/<env>/history.jsonl from vra 0.3 is reported by `vra history verify`.
    history_backend: Any = None

    def history(self, env: str) -> list[dict]:
        if self.history_backend is None:
            return []
        return list(self.history_backend.read(env))

    def append_history(self, env: str, record: dict, dry_run: bool = False) -> dict | None:
        if dry_run:
            return None
        if self.history_backend is None:
            raise ConfigError("no state backend for the env history (use the ledger through a vra Context)")
        rec = {"ts": util.now_iso(), "env": env, **record}
        rec["id"] = record_id(rec)
        return self.history_backend.append(env, rec)

    def last_good_deploy(self, env: str, exclude_build: str | None = None) -> dict | None:
        for rec in reversed(self.history(env)):
            if rec.get("action") in ("deploy", "rollback") and rec.get("result") == "SUCCEEDED":
                if exclude_build and rec.get("build") == exclude_build:
                    continue
                return rec
        return None

    def current_deploy(self, env: str) -> dict | None:
        return self.last_good_deploy(env)

    # ------------------------------------------------------------------ other files
    def path_policy(self) -> dict:
        p = self.root / "path-policy.yaml"
        if p.exists():
            data = util.read_yaml(p) or []
        else:
            data = yaml.safe_load(vra_data.text("defaults", "path-policy.yaml"))
        if isinstance(data, list):
            data = {"default": "agent", "rules": data}
        data.setdefault("default", "agent")
        return data

    def customer_tests(self) -> list[dict]:
        p = self.root / "tests" / "customer.yaml"
        if not p.exists():
            return []
        data = util.read_yaml(p) or []
        if isinstance(data, dict):
            data = data.get("hooks", [])
        return data

    def core_patches(self) -> list[str]:
        """Fork paths listed in core-patches.md (any `backticked/path` on a bullet or table line outside fenced
        code blocks). Ledger patch files (`core-patches/<name>.patch`, vra.core_patches) are not fork paths."""
        from vra import core_patches as cp
        out = []
        for line in cp.register_lines(self.root):
            out += [x for x in re.findall(r"`([^`\s]+)`", line)
                    if not (x.startswith(cp.PATCH_DIR + "/") and x.endswith(".patch"))]
        return out

    def core_patch_files(self) -> list[str]:
        """Ledger patch files core-patches.md lists (`core-patches/<name>.patch`), applied by every build."""
        from vra import core_patches as cp
        return cp.listed(self.root)

    # ------------------------------------------------------------------ validation
    def validate(self) -> dict:
        errors: list[str] = []
        warnings: list[str] = []
        try:
            raw = self.raw_project()
        except ConfigError as exc:
            return {"ok": False, "errors": [str(exc)], "warnings": []}
        errors += _schema_errors(raw, schema("vra"), "vra.yaml")
        proj = self._normalize_project(raw)
        gh = proj.get("githost", {})
        if gh.get("type") in ("github", "gitlab", "bitbucket") and not gh.get("repo"):
            errors.append(f"vra.yaml: githost.repo is required for githost.type={gh.get('type')}")
        if gh.get("type") == "codecommit" and not gh.get("repositoryName"):
            errors.append("vra.yaml: githost.repositoryName is required for codecommit")
        unit = (proj.get("build") or {}).get("unitSuites")
        if unit:
            from vra import tests_runner
            known = tests_runner.suites_catalog()["suites"]
            bad = [s for s in unit if s not in known]
            if bad:
                errors.append(f"vra.yaml: build.unitSuites {bad} unknown; known: {sorted(known)}")
        sig = (proj.get("build") or {}).get("signing") or {}
        if sig.get("tags") and sig.get("algorithm", "ECDSA_SHA_256") != "ECDSA_SHA_256":
            errors.append("vra.yaml: build.signing.tags needs an ECC_NIST_P256 key with algorithm ECDSA_SHA_256 "
                          "(SSH tag signatures)")
        ss = proj.get("stateStore", {})

        if ss.get("type") == "s3" and not (ss.get("bucket") and ss.get("lockTable")):
            errors.append("vra.yaml: stateStore.type=s3 requires bucket and lockTable")
        nt = proj.get("notifications", {})
        if nt.get("type") not in (None, "none") and not nt.get("target"):
            errors.append("vra.yaml: notifications.target (secret name) is required")
        if nt.get("type") == "email" and not nt.get("from"):
            errors.append("vra.yaml: notifications.from is required for email")

        names = self.env_names()
        if not names:
            errors.append("no environments under envs/")
        envs: dict[str, dict] = {}
        for n in names:
            raw_env = self.raw_env(n)
            errs = _schema_errors(raw_env, schema("env"), f"envs/{n}/env.yaml")
            errors += errs
            if errs:
                continue
            envs[n] = self.env(n)
        for n, e in envs.items():
            where = f"envs/{n}"
            region_part = partition_for_region(e["region"])
            if e.get("partition") and region_part and e["partition"] != region_part:
                errors.append(f"{where}: partition {e['partition']} does not match region {e['region']} ({region_part})")
            for k in ("deployRoleArn", "dataCopyRoleArn", "readOnlyRoleArn"):
                arn = e.get(k)
                if arn:
                    part = arn.split(":")[1]
                    if e.get("partition") and part != e["partition"]:
                        errors.append(f"{where}: {k} partition {part} != env partition {e['partition']}")
                    if arn.split(":")[4] != e["account"]:
                        warnings.append(f"{where}: {k} is in account {arn.split(':')[4]}, env account is {e['account']}")
            if not e.get("deployRoleArn") and not e.get("profile"):
                warnings.append(f"{where}: neither deployRoleArn nor profile set; ambient credentials will be used")
            if e["templateBase"] == "govcloud" and (e.get("partition") or region_part) not in ("aws-us-gov", None):
                warnings.append(f"{where}: templateBase govcloud with partition {e.get('partition') or region_part}")
            if e["templateBase"] == "custom" and not e.get("templatePath"):
                warnings.append(f"{where}: templateBase custom without templatePath; upgrade-config falls back to commercial")
            if e["requiresApproval"] and not e.get("approvers"):
                errors.append(f"{where}: requiresApproval=true needs at least one approver")
            if e["autoDeploy"] and e["requiresApproval"]:
                errors.append(f"{where}: autoDeploy and requiresApproval are mutually exclusive (watch can never "
                              f"deploy an approval-gated env)")
            pf = e.get("promotesFrom")
            if pf:
                if pf not in envs and pf not in names:
                    errors.append(f"{where}: promotesFrom {pf!r} is not a known env")
                elif pf in envs and envs[pf]["lane"] == "preview":
                    errors.append(f"{where}: promotesFrom {pf!r} is a preview-lane env (never promotable)")
                if e["lane"] == "preview":
                    errors.append(f"{where}: preview-lane envs cannot promote from another env")
            for src in e.get("dataCopy", {}).get("allowedSources", []):
                if src not in names:
                    errors.append(f"{where}: dataCopy.allowedSources {src!r} is not a known env")
            if e.get("dataCopy", {}).get("allowedSources"):
                from vra.datacopy import target_allowed
                ok, why = target_allowed(self, n)
                if not ok:
                    errors.append(f"{where}: dataCopy.allowedSources is set but {why}")
            rb = e.get("registeredBuckets") or {}
            if rb.get("granted") and not rb.get("bucketArns"):
                warnings.append(f"{where}: registeredBuckets.granted is true but bucketArns is empty; only buckets "
                                f"named after the core stack count as granted")
            for arn in list(rb.get("bucketArns") or []) + list(rb.get("kmsKeyArns") or []):
                part = arn.split(":")[1] if arn.startswith("arn:") else None
                if part and e.get("partition") and part != e["partition"]:
                    errors.append(f"{where}: registeredBuckets ARN {arn} is not in partition {e['partition']}")
            from vra import iam as iam_mod
            ie, iw = iam_mod.validate_settings(e, where)
            errors += ie
            warnings += iw
            if not ie and iam_mod.settings(e)["rolePrefix"] and "boundary" in iam_mod.settings(e)["modes"] and \
                    iam_mod.API_DEPLOYMENT_PATCH not in self.core_patch_files():
                # GitHub issue #1210: without it, introducing the prefix on a deployed env leaves the API stage on a
                # deployment whose authorizer credentials name a deleted role (HTTP 500 on every route)
                warnings.append(f"{where}: iam.rolePrefix is set but core-patches.md does not list "
                                f"{iam_mod.API_DEPLOYMENT_PATCH} (GitHub issue #1210): on an env deployed without "
                                f"this prefix, the role replacement leaves the REST API stage on a deployment that "
                                f"names a deleted role and every API route answers HTTP 500. Copy the patch from the "
                                f"package's ledger-template/ and list it, unless the fork already has the fix")
            if not ie and e.get("ab") and "precreated" in iam_mod.settings(e)["modes"]:
                errors.append(f"{where}: iam.mode precreated with A/B slots is not supported (each slot's stack "
                              f"name gives other construct paths); use boundary mode or in-place deploys")
            if e["requiresApproval"] and e.get("setSourceIdentity") is False:
                warnings.append(f"{where}: setSourceIdentity=false on an approval-gated env; its deploy role "
                                f"requires sts:SourceIdentity by default (RequireSourceIdentity=auto)")
            if is_prod_class(self, n) and not sig.get("kmsKeyArn"):
                warnings.append(f"{where}: prod-class env without build.signing (KMS manifest + tag signatures); "
                                f"recommended for production")
            cw = e.get("changeWindow")

            if cw:
                from vra import cron
                try:
                    cron.parse_window(cw)
                except ValueError as exc:
                    errors.append(f"{where}: changeWindow invalid: {exc}")
            if e["deployMode"] == "ab" and not e.get("ab"):
                errors.append(f"{where}: deployMode=ab requires ab.slots")
            cfg = None
            try:
                cfg = self.vams_config(n)
            except ConfigError as exc:
                errors.append(str(exc))
            if cfg is None and not e["requiresApproval"]:
                warnings.append(f"{where}: no overlay/{CONFIG_OVERLAY_PATH}; synth uses the repo's config")
            elif cfg is not None:
                cr = (cfg.get("env") or {}).get("region")
                if cr and cr != e["region"]:
                    errors.append(f"{where}: overlay config env.region {cr} != env.yaml region {e['region']}")
                ca = (cfg.get("env") or {}).get("account")
                if ca and str(ca) != e["account"]:
                    errors.append(f"{where}: overlay config env.account {ca} != env.yaml account {e['account']}")
                if not e.get("baseStackName"):
                    errors.append(f"{where}: baseStackName missing (env.yaml or overlay config app.baseStackName)")
            for rel in _overlay_files(self.overlay_dir(n)):
                pp = PurePosixPath(rel)
                if ".." in pp.parts or pp.is_absolute():
                    errors.append(f"{where}: overlay path escapes the checkout: {rel}")
        # cycles in promotion graph
        for n in envs:
            seen, cur = set(), n
            while cur and cur in envs:
                if cur in seen:
                    errors.append(f"envs/{n}: promotesFrom cycle")
                    break
                seen.add(cur)
                cur = envs[cur].get("promotesFrom")
        # base stack names must be unique per account+region
        seen_stacks: dict[tuple, str] = {}
        for n, e in envs.items():
            key = (e["account"], e["region"], e.get("configName"), e.get("baseStackName"))
            if e.get("baseStackName") and key in seen_stacks:
                errors.append(f"envs/{n}: same account/region/baseStackName as envs/{seen_stacks[key]}")
            seen_stacks[key] = n
        # path policy + customer tests
        pp = self.root / "path-policy.yaml"
        if pp.exists():
            errors += _schema_errors(util.read_yaml(pp) or [], schema("path-policy"), "path-policy.yaml")
        else:
            warnings.append("path-policy.yaml missing; the packaged default is used")
        ct = self.root / "tests" / "customer.yaml"
        if ct.exists():
            errors += _schema_errors(util.read_yaml(ct) or [], schema("customer-tests"), "tests/customer.yaml")
        ad = self.root / "approvals"
        if ad.exists():
            for f in sorted(ad.rglob("*.yaml")):
                errors += _schema_errors(util.read_yaml(f) or {}, schema("approval"), str(f.relative_to(self.root)))
        e2, w2 = self._topology_checks(proj, names, envs)
        errors += e2
        warnings += w2
        from vra import pipeline as pipeline_mod  # automation.mode ladder (agent | hybrid | pipeline | pipeline+actions)
        e3, w3 = pipeline_mod.validate(self)
        errors, warnings = errors + e3, warnings + w3
        from vra import core_patches as cp
        errors += cp.validate(self.root)
        errors += self._secret_scan()
        return {"ok": not errors, "errors": errors, "warnings": warnings}

    def _topology_checks(self, proj: dict, names: list[str], envs: dict[str, dict]) -> tuple[list[str], list[str]]:
        """Config identity, naming, A/B, tiers, partitions and promotion graph checks."""
        errors: list[str] = []
        warnings: list[str] = []
        raw_lanes = (self.raw_project().get("lanes") or {})
        if (raw_lanes.get("release") or {}).get("autoMerge"):
            errors.append("vra.yaml: lanes.release.autoMerge is not supported: a release build is promotable only "
                          "from a reviewed merge commit on the trunk; drop the key (the release lane opens a PR)")
        fork_local = (proj.get("fork") or {}).get("localPath")
        if fork_local:
            fl = Path(fork_local).expanduser().resolve()
            if fl == self.root or self.root in fl.parents:
                warnings.append("vra.yaml: fork.localPath is inside the ledger; the fork checkout is engine-managed "
                                "and belongs outside the shared home")
        for n, e in envs.items():
            where = f"envs/{n}"
            try:
                errors += self.identity_problems(n)
            except ConfigError as exc:
                errors.append(str(exc))
            for key in ("baseStackName", "configName"):
                v = e.get(key)
                if v and v != v.lower():
                    errors.append(f"{where}: {key} {v!r} must be lower case (S3 and IAM names derived from it are "
                                  f"case-sensitive in ARNs but lower-cased in bucket names)")
            try:
                core = self.core_stack_name(n)
                if len(core) > 30:
                    warnings.append(f"{where}: core stack name {core!r} is {len(core)} characters; auto-generated "
                                    f"bucket and function names may truncate it and fall outside the role patterns")
            except ConfigError:
                pass
            cfg = None
            try:
                cfg = self.vams_config(n)
            except ConfigError:
                cfg = None
            if cfg is None and (e.get("requiresApproval") or e.get("tier") == "prod"):
                errors.append(f"{where}: no overlay/{CONFIG_OVERLAY_PATH}; an approval-gated env must carry its own "
                              f"VAMS config (the fork's shared config.json is for no env in particular)")
            pol = self.overlay_dir(n) / "infra/config/policy/iamRoleConfig.json"
            if pol.exists():
                try:
                    pdoc = util.read_json(pol)
                except ValueError:
                    pdoc = {}
                if pdoc.get("useCliCredentialsSynthesizer"):
                    errors.append(f"{where}: overlay policy/iamRoleConfig.json useCliCredentialsSynthesizer is not "
                                  f"supported; vra deploys through the CDK bootstrap roles")
            app = (cfg or {}).get("app") or {}
            if e.get("deployMode") == "ab" or e.get("ab"):
                slots = (e.get("ab") or {}).get("slots") or {}
                slot_ov = (e.get("ab") or {}).get("slotOverlays") or {}
                if (app.get("useAlb") or {}).get("enabled"):
                    hosts = {s: util.get_path(slot_ov.get(s) or {}, "app.useAlb.domainHost") for s in ("blue", "green")}
                    if not all(hosts.values()) or hosts["blue"] == hosts["green"]:
                        errors.append(f"{where}: A/B on the ALB path needs ab.slotOverlays.<blue|green>.app.useAlb."
                                      f"domainHost, different per slot (VAMS names the web bucket after it, and S3 "
                                      f"names are global); got {hosts}")
                for path in ("app.useCloudFront.customDomain", "app.api.apiGatewayRest.customDomain"):
                    cd = util.get_path(cfg or {}, path) or {}
                    if cd.get("enabled"):
                        per = {s: util.get_path(slot_ov.get(s) or {}, f"{path}.domainHost") for s in ("blue", "green")}
                        if not all(per.values()) or per["blue"] == per["green"]:
                            warnings.append(f"{where}: {path} is enabled; both A/B slots need their own domainHost "
                                            f"in ab.slotOverlays, or the idle slot cannot be created")
                if slots and len(set(slots.values())) != 2:
                    errors.append(f"{where}: ab.slots blue and green must name different base stack names")
            if e.get("fips") not in ("auto", True, False, None):
                errors.append(f"{where}: fips must be auto, true or false")
            # promotion graph
            pf = e.get("promotesFrom")
            if pf and pf in envs:
                pe = envs[pf]
                p1 = e.get("partition") or partition_for_region(e["region"])
                p2 = pe.get("partition") or partition_for_region(pe["region"])
                if p1 and p2 and p1 != p2 and not e.get("allowCrossPartitionPromotion"):
                    errors.append(f"{where}: promotesFrom {pf!r} crosses partitions ({p2} -> {p1}): the bundle, "
                                  f"history and state live in one partition. Use a per-partition test env, or set "
                                  f"allowCrossPartitionPromotion: true")
                if pe.get("templateBase") != e.get("templateBase"):
                    warnings.append(f"{where}: promotesFrom {pf!r} uses templateBase {pe.get('templateBase')}, this "
                                    f"env {e.get('templateBase')}: the tested synth differs from this env's")
                if e.get("requiresApproval") and pe.get("requiresApproval") and e.get("tier") is None \
                        and pe.get("tier") is None:
                    warnings.append(f"{where}: gated env promoted from gated env {pf!r} without an explicit tier; "
                                    f"set `tier: prod` on every production env (a wave env is otherwise not "
                                    f"treated as production)")
            elif e.get("requiresApproval") and not pf and not e.get("rootEnv"):
                warnings.append(f"{where}: approval-gated env without promotesFrom is checked on approval only; set "
                                f"promotesFrom, or `rootEnv: true` to confirm it is deployed directly")
            if e.get("tier") == "preview" and e.get("lane") != "preview":
                errors.append(f"{where}: tier preview is for preview-lane envs")
            if e.get("tier") == "prod" and not e.get("requiresApproval"):
                errors.append(f"{where}: tier prod requires requiresApproval: true")
        # A/B and multi-stack collisions across envs
        bases: dict[tuple, list[str]] = {}
        domains: dict[str, list[str]] = {}
        for n, e in envs.items():
            if e.get("baseStackName"):
                bases.setdefault((e["account"], e["region"], e.get("configName")), []).append(n)
            try:
                cfg = self.vams_config(n) or {}
            except ConfigError:
                cfg = {}
            for path in ("app.useAlb.domainHost", "app.useCloudFront.customDomain.domainHost",
                         "app.api.apiGatewayRest.customDomain.domainHost"):
                v = util.get_path(cfg, path)
                if v and "example.com" not in str(v):
                    domains.setdefault(str(v).lower(), []).append(f"{n}:{path}")
        for key, ns in bases.items():
            for a in ns:
                for b in ns:
                    ba, bb = envs[a]["baseStackName"], envs[b]["baseStackName"]
                    if a != b and ba.endswith("-" + bb):
                        warnings.append(f"envs/{a}: baseStackName {ba!r} ends in '-{bb}' (env {b}, same account and "
                                        f"region): the stack-name patterns of {b}'s roles also match {a}'s stacks")
        for host, where in domains.items():
            if len(where) > 1:
                errors.append(f"domain host {host!r} is used by more than one env ({', '.join(where)})")
        lanes = proj.get("lanes") or {}
        if not (lanes.get("preview") or {}).get("enabled", True):
            for n, e in envs.items():
                if e.get("lane") == "preview":
                    errors.append(f"envs/{n}: lane preview but lanes.preview is disabled")
        return errors, warnings

    def _secret_scan(self) -> list[str]:
        found: list[str] = []
        candidates = [f for f in sorted(self.root.rglob("*")) if ".git" not in f.relative_to(self.root).parts]
        for f in candidates:
            if not f.is_file() or f.suffix not in (".yaml", ".yml", ".json", ".jsonl", ".md", ".txt", ".ts"):
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            rel = f.relative_to(self.root)
            if _ACCESS_KEY_RE.search(text):
                found.append(f"{rel}: contains an AWS access key id; secrets must live in Secrets Manager/SSM")
            data = None
            try:
                data = json.loads(text) if f.suffix == ".json" else (yaml.safe_load(text) if f.suffix in (".yaml", ".yml") else None)
            except Exception:  # noqa: BLE001 - non-structured file
                data = None
            for path, val in _walk(data):
                key = path.rsplit(".", 1)[-1]
                if _SECRET_KEY_RE.match(key) and isinstance(val, str) and val and not val.startswith(("arn:", "{{resolve:")) \
                        and val not in ("<REPLACE>", "null"):
                    found.append(f"{rel}: key {path} holds a literal secret value; store the secret NAME instead")
        return found

    # ------------------------------------------------------------------ show / diff / import / upgrade
    def show(self, env: str | None = None) -> dict:
        if env:
            e = self.env(env)
            return {"env": e, "tier": tier(self, env), "isProdClass": is_prod_class(self, env),
                    "requiresApproval": bool(e.get("requiresApproval")), "requiredSuites": required_suites(self, env),
                    "overlayFiles": _overlay_files(self.overlay_dir(env)),
                    "coreStack": _safe(lambda: self.core_stack_name(env)),
                    "ssmPrefix": _safe(lambda: self.ssm_prefix(env)),
                    "current": self.current_deploy(env)}
        return {"project": self.project, "envs": {n: {k: self.env(n).get(k) for k in
                ("account", "region", "partition", "lane", "promotesFrom", "autoDeploy", "requiresApproval",
                 "deployMode", "templateBase")} | {"tier": tier(self, n)} for n in self.env_names()}}

    def diff(self, env: str, artifacts_dir: Path) -> dict:
        """Overlay now vs the overlay recorded in the env's last successful deploy bundle."""
        now = util.tree_hashes(self.overlay_dir(env))
        last = self.current_deploy(env)
        if not last:
            return {"env": env, "baseline": None, "added": sorted(now), "removed": [], "changed": [], "diffs": {}}
        snap = artifacts_dir / last["build"] / "overlays" / env
        before = util.tree_hashes(snap)
        added = sorted(set(now) - set(before))
        removed = sorted(set(before) - set(now))
        changed = sorted(k for k in set(now) & set(before) if now[k] != before[k])
        diffs = {}
        for k in changed:
            a = (snap / k).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            b = (self.overlay_dir(env) / k).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            diffs[k] = "".join(difflib.unified_diff(a, b, f"deployed/{k}", f"ledger/{k}"))
        return {"env": env, "baseline": last["build"], "added": added, "removed": removed, "changed": changed,
                "diffs": diffs}

    def import_config(self, env: str, src: Path, dry_run: bool = False) -> dict:
        """Copy an existing deployment's config.json into the env overlay, and take the names that identify the
        deployment (``name``, ``app.baseStackName``, ``env.region``, ``env.account``) into env.yaml from it.

        Refuses when env.yaml already names a different deployment (setup answered ``baseStackName: dev`` but the
        imported config deploys ``demo8``): importing would make the engine check, back up and migrate one stack
        while synth deploys another."""
        src = Path(src)
        if not src.exists():
            raise UsageError(f"{src} not found")
        try:
            cfg = util.read_json(src)
        except json.JSONDecodeError as exc:
            raise UsageError(f"{src} is not valid JSON: {exc}") from exc
        dest = self.overlay_dir(env) / CONFIG_OVERLAY_PATH
        if dest.exists() and dest.resolve() == src.resolve():
            raise UsageError(f"{src} already is the overlay of {env}")
        raw = self.raw_env(env)
        secrets = [p for p, v in _walk(cfg) if _SECRET_KEY_RE.match(p.rsplit('.', 1)[-1]) and isinstance(v, str) and v]
        if secrets:
            raise UsageError(f"refusing import: literal secret values at {secrets}; replace with secret names first")
        cfg_env = cfg.get("env") or {}
        imported = {"configName": cfg.get("name") or "vams",
                    "baseStackName": (cfg.get("app") or {}).get("baseStackName"),
                    "region": cfg_env.get("region"),
                    "account": str(cfg_env["account"]) if cfg_env.get("account") else None}
        mismatch = []
        for key, val in imported.items():
            mine = raw.get(key)
            if mine is not None and val is not None and str(mine) != str(val):
                mismatch.append(f"{key}: env.yaml {mine!r}, imported config {val!r}")
        if mismatch:
            raise UsageError(f"refusing import into {env}: the config names a different deployment than "
                             f"envs/{env}/env.yaml ({'; '.join(mismatch)}). Fix env.yaml to the deployed values "
                             f"(or import into the env that describes that deployment)")
        if not imported["baseStackName"]:
            raise UsageError(f"refusing import: {src} has no app.baseStackName")
        notes = []
        env_updates = {k: v for k, v in imported.items() if v is not None and raw.get(k) is None}
        cfg_fill = {}
        if not cfg_env.get("region") and raw.get("region"):
            cfg_fill["region"] = raw["region"]
        if not cfg_env.get("account") and raw.get("account"):
            cfg_fill["account"] = str(raw["account"])
        if cfg_fill:
            cfg = {**cfg, "env": {**cfg_env, **cfg_fill}}
            notes.append(f"config env.{'/'.join(cfg_fill)} was empty; set from env.yaml so synth never falls back "
                         f"to the host's region")
        if env_updates:
            notes.append(f"env.yaml takes {sorted(env_updates)} from the imported config")
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            util.write_json(dest, cfg) if cfg_fill else shutil.copyfile(src, dest)
            if env_updates:
                util.write_yaml(self.env_dir(env) / "env.yaml", {**raw, **env_updates})
            custom = src.parent / "custom" / "customConfig.json"
            if custom.exists():
                cdest = self.overlay_dir(env) / "infra/config/custom/customConfig.json"
                cdest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(custom, cdest)
                notes.append("imported custom/customConfig.json alongside")
        self._envs.pop(env, None)
        return {"env": env, "written": str(dest), "dryRun": dry_run, "notes": notes, "envYamlUpdates": env_updates,
                "baseStackName": imported["baseStackName"], "configName": imported["configName"]}

    def template_path(self, env: str) -> str:
        e = self.env(env)
        if e["templateBase"] == "custom":
            return e.get("templatePath") or TEMPLATE_FILES["commercial"]
        return TEMPLATE_FILES[e["templateBase"]]

    def upgrade_config(self, env: str, template_text: str, ref: str) -> dict:
        """Compare the env config against the upstream template at ``ref``; propose merged values."""
        template = json.loads(template_text)
        current = self.vams_config(env) or {}
        t_keys, c_keys = util.key_paths(template), util.key_paths(current)
        new_keys = sorted(k for k in t_keys - c_keys if not _parent_in(k, t_keys - c_keys))
        removed = sorted(k for k in c_keys - t_keys if not _parent_in(k, c_keys - t_keys))
        proposed = util.deep_merge(template, _prune(current, removed))
        return {"env": env, "ref": ref, "template": self.template_path(env), "newKeys": new_keys,
                "newKeyDefaults": {k: util.get_path(template, k) for k in new_keys},
                "removedKeys": removed, "proposed": proposed}


    def upgrade_custom_config(self, env: str, upstream_text: str, ref: str) -> dict:
        """customConfig.json is customer-owned (path policy: fork). Upstream ships it (empty today); if its
        upstream copy gains keys, report them and propose adding them with upstream defaults. Customer keys
        are never removed or changed."""
        upstream = json.loads(upstream_text or "{}")
        p = self.overlay_dir(env) / CUSTOM_CONFIG
        current = util.read_json(p) if p.exists() else {}
        u_keys, c_keys = util.key_paths(upstream), util.key_paths(current)
        new_keys = sorted(k for k in u_keys - c_keys if not _parent_in(k, u_keys - c_keys))
        return {"file": CUSTOM_CONFIG, "ref": ref, "newKeys": new_keys,
                "newKeyDefaults": {k: util.get_path(upstream, k) for k in new_keys},
                "proposed": util.deep_merge(upstream, current)}


CUSTOM_CONFIG = "infra/config/custom/customConfig.json"


def record_id(rec: dict) -> str:
    """Stable id of a history record (sha256 of its canonical JSON without ``id``), used for de-duplication."""
    body = {k: v for k, v in rec.items() if k != "id"}
    return util.sha256_bytes(json.dumps(body, sort_keys=True, default=str).encode())[:24]


def tier(ledger, env: str) -> str:
    """``prod`` | ``nonprod`` | ``preview``. env.yaml ``tier`` wins; unset, it is derived: preview lane -> preview;
    approval-gated with no env promoting from it (end of a chain) -> prod; else nonprod. Per-region prod waves
    (``test -> prod-us -> prod-eu``) must set ``tier: prod`` on every wave env (validate warns)."""
    e = ledger.env(env)
    if e.get("tier"):
        return e["tier"]
    if e.get("lane") == "preview":
        return "preview"
    promoted_into = any(ledger.env(n).get("promotesFrom") == env for n in ledger.env_names() if n != env)
    return "prod" if e.get("requiresApproval") and not promoted_into else "nonprod"


def is_prod_class(ledger, env: str) -> bool:
    """Production env: data copies into it, waived scanners and shared buckets are refused (see :func:`tier`)."""
    return tier(ledger, env) == "prod"


def source_identity_on(e: dict) -> bool:
    """sts:SourceIdentity on env role sessions: ``setSourceIdentity`` when set, else on for approval-gated envs
    (test, prod: their deploy roles require it by default, vra-deploy-role.yaml RequireSourceIdentity=auto)."""
    v = e.get("setSourceIdentity")
    return bool(e.get("requiresApproval")) if v is None else bool(v)


def required_suites(ledger, env: str) -> list[str]:
    """Live suites that must execute (>= 1 passed test, none failed) for `vra test` / the smoke on ``env``.

    ``requiredSuites`` in env.yaml wins. Default: ``[live-cli]`` for approval-gated envs and for every env a
    gated env promotes from (its test result is promotion evidence); none otherwise.
    """
    e = ledger.env(env)
    if e.get("requiredSuites") is not None:
        return list(e["requiredSuites"])
    feeds_gated = any(ledger.env(n).get("promotesFrom") == env and ledger.env(n).get("requiresApproval")
                      for n in ledger.env_names() if n != env)
    return ["live-cli"] if (e.get("requiresApproval") or feeds_gated) else []


def promoted_into(names, envs: dict, env: str) -> bool:
    return any((envs.get(x) or {}).get("promotesFrom") == env for x in names if x != env)


def _parent_in(key: str, keys: set[str]) -> bool:
    parts = key.split(".")
    return any(".".join(parts[:i]) in keys for i in range(1, len(parts)))


def _prune(obj: dict, removed: list[str]) -> dict:
    out = copy.deepcopy(obj)
    for k in removed:
        parts = k.split(".")
        cur = out
        for p in parts[:-1]:
            cur = cur.get(p, {}) if isinstance(cur, dict) else {}
        if isinstance(cur, dict):
            cur.pop(parts[-1], None)
    return out


def _schema_errors(data: Any, sch: dict, where: str) -> list[str]:
    v = jsonschema.Draft202012Validator(sch)
    return [f"{where}: {'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
            for e in sorted(v.iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))]


def _overlay_files(d: Path) -> list[str]:
    if not d.exists():
        return []
    return sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file())


def _walk(obj: Any, prefix: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            yield p, v
            yield from _walk(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{prefix}[{i}]")


def _safe(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return f"<unknown: {exc}>"
