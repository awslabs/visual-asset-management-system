# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`vra build`: unit suites, per-env synth from a scratch checkout with the ledger overlay applied,
optional ASH scan, SBOM, immutable bundle (per-env cdk.out + migrations + overlay snapshot +
manifest with sha256), and the release tag.

Build once, promote the artifact: deploy/promote only ever use ``artifacts/<buildId>/cdk.out/<env>``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Iterator

from vra import gitops, tests_runner, util
from vra.errors import ConfigError, NeedsHuman, UsageError, VraError
from vra.runner import workflow

VERSION_RE = re.compile(r'VAMS_VERSION\s*=\s*["\']([^"\']+)["\']')
MIGRATIONS_DIR = "infra/deploymentDataMigration"
# CDK writes derived files into a cloud assembly when it deploys from it: `.cache/<assetHash>.zip`, the zipped
# file assets it publishes. CDK re-uses a cached zip without re-checking it, so the cache is purged (not just
# ignored) before every integrity check; CDK rebuilds it from the hashed asset directories.
ASSEMBLY_CACHE_DIR = ".cache"


# Lock files the CDK CLI creates in a cloud assembly while it reads (`read.<pid>.<n>.lock`) or writes it
# (`synth.lock`). Not content: a cdk killed mid-read, or a copy taken while one ran, leaves one behind
# (E2E-040: a bundle copied to machine B during A's `cdk diff` failed the integrity check forever).
CDK_ASSEMBLY_LOCK_RE = re.compile(r"^(read\.\d+\.\d+|synth)\.lock$")


def assembly_hash(d: Path) -> str:
    hashes = util.tree_hashes(d)
    return util.combined_hash({k: v for k, v in hashes.items() if not CDK_ASSEMBLY_LOCK_RE.match(k)})


def vams_version_at(g: gitops.Git, commit: str) -> str:
    text = g.show(commit, "infra/config/config.ts")
    if not text:
        raise UsageError(f"{commit[:12]} has no infra/config/config.ts; is this a VAMS tree?")
    m = VERSION_RE.search(text)
    if not m:
        raise UsageError("VAMS_VERSION not found in infra/config/config.ts")
    return m.group(1)


# Files that carry the VAMS version next to infra/config/config.ts (root CLAUDE.md "Rolling the VAMS version").
VERSION_ROLL_FILES = {"package.json": re.compile(r'"version"\s*:\s*"([0-9][^"]*)"'),
                      "tools/VamsCLI/vamscli/version.py": re.compile(r'__version__\s*=\s*["\']([^"\']+)["\']')}


def version_roll_warnings(g: gitops.Git, commit: str, version: str) -> list[str]:
    """A fork that rolled VAMS_VERSION in some files only (half-rolled) builds, but reports a misleading version."""
    out = []
    for rel, rx in VERSION_ROLL_FILES.items():
        text = g.show(commit, rel)
        m = rx.search(text or "")
        if text and m and m.group(1) != version:
            out.append(f"{rel} says {m.group(1)}, infra/config/config.ts VAMS_VERSION says {version}: the fork's "
                       f"version roll is incomplete")
    return out


def change_authors(g: gitops.Git, commit: str) -> list[str]:
    """Author and committer of the built commit: separation of duties keeps them from approving its promotion."""
    try:
        text = g.out("log", "-1", "--format=%ae%n%ce", commit)
    except Exception:  # noqa: BLE001
        return []
    return sorted({x.strip() for x in text.splitlines() if x.strip()})


# What a synth may see of the operator's environment: tooling only. VAMS config overrides (ledger.VAMS_CONFIG_ENV_VARS)
# and AWS credentials never come from the host; the engine adds region, account and credentials itself.
SYNTH_ENV_ALLOW = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "TERM", "TZ", "TMPDIR", "TMP", "TEMP",
                   "SHELL", "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY",
                   "DOCKER_DEFAULT_PLATFORM", "DOCKER_BUILDKIT", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy",
                   "https_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "NODE_OPTIONS",
                   "CI", "CDK_DOCKER", "JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION")
SYNTH_ENV_PREFIXES = ("LC_", "NPM_CONFIG_", "npm_config_", "NVM_", "XDG_", "BUILDX_")


def synth_environment(ctx, env: str, creds: dict) -> tuple[dict, list[str]]:
    """The full environment of `cdk synth` for ``env`` (marked clean: :class:`util.Shell` starts from nothing).

    Refuses when the operator's environment sets a VAMS config override (STACK_NAME, ADMIN_EMAIL_ADDRESS,
    AWS_USE_WAF, AWS_USE_FIPS_ENDPOINT, ...) that env.yaml ``synthEnv`` does not record: the same commit and ledger
    must synthesize the same template on every machine."""
    from vra.ledger import VAMS_CONFIG_ENV_VARS
    e = ctx.ledger.env(env)
    recorded = {str(k): str(v) for k, v in (e.get("synthEnv") or {}).items()}
    bad = sorted(k for k in VAMS_CONFIG_ENV_VARS if os.environ.get(k) and k not in recorded)
    if bad:
        raise NeedsHuman(f"the operator environment sets VAMS config override(s) {bad}; they would change what is "
                         f"synthesized for {env}. Unset them, or record the value in envs/{env}/env.yaml synthEnv")
    unknown = sorted(k for k in recorded if k not in VAMS_CONFIG_ENV_VARS)
    if unknown:
        raise UsageError(f"envs/{env}/env.yaml synthEnv: {unknown} are not VAMS config variables "
                         f"({list(VAMS_CONFIG_ENV_VARS)})")
    base = {k: v for k, v in os.environ.items()
            if k in SYNTH_ENV_ALLOW or k.startswith(SYNTH_ENV_PREFIXES)}
    creds = {k: v for k, v in creds.items() if k != "AWS_USE_FIPS_ENDPOINT"}  # FIPS is a config choice, never env
    full = {util.CLEAN_ENV: "1", **base, **creds, **recorded, "CDK_DISABLE_VERSION_CHECK": "1"}
    return full, sorted(k for k in full if k != util.CLEAN_ENV)


def slot_overlay(ctx, src: Path, env: str, slot: str | None) -> dict | None:
    """A/B: deep-merge ``ab.slotOverlays.<slot>`` (domain hosts, certificates) into the overlay config of that slot."""
    if not slot:
        return None
    extra = ((ctx.ledger.env(env).get("ab") or {}).get("slotOverlays") or {}).get(slot)
    if not extra:
        return None
    p = src / "infra" / "config" / "config.json"
    cfg = util.read_json(p) if p.exists() else {}
    util.write_json(p, util.deep_merge(cfg, extra))
    return extra


def cdk_cli_lock_at(g: gitops.Git, commit: str) -> dict | None:
    """Exact aws-cdk CLI version + integrity from infra/package-lock.json (what `npm ci` installs)."""
    text = g.show(commit, "infra/package-lock.json")
    if not text:
        return None
    try:
        lock = json.loads(text)
    except json.JSONDecodeError:
        return None
    pkg = (lock.get("packages") or {}).get("node_modules/aws-cdk") or (lock.get("dependencies") or {}).get("aws-cdk")
    if not pkg or not pkg.get("version"):
        return None
    return {"version": pkg["version"], "integrity": pkg.get("integrity"), "resolved": pkg.get("resolved"),
            "source": "infra/package-lock.json"}


def cdk_cli_version_at(g: gitops.Git, commit: str) -> str | None:
    lock = cdk_cli_lock_at(g, commit)
    if lock:
        return lock["version"]
    text = g.show(commit, "infra/package.json")
    if not text:
        return None
    pkg = json.loads(text)
    spec = (pkg.get("devDependencies") or {}).get("aws-cdk") or (pkg.get("dependencies") or {}).get("aws-cdk")
    if not spec:
        return None
    m = re.search(r"(\d+\.\d+\.\d+)", spec)
    return m.group(1) if m else None


def build_dir(ctx, build_id: str) -> Path:
    return ctx.artifacts_dir / build_id


BUNDLE_STORE_ENV = "VRA_BUNDLE_STORE"
_BUNDLE_STORE_RE = re.compile(r"s3://([a-z0-9][a-z0-9.\-]{1,61}[a-z0-9])(?:/([A-Za-z0-9._\-/]*))?")


def bundle_store(ctx):
    """Where bundles of other builds come from: the state store (``stateStore.type: s3``), else the pipeline's
    bundle store (``VRA_BUNDLE_STORE=s3://<artifact bucket>/<prefix>``, set by the pipeline stack on its build,
    deploy and test jobs; B-050-21), else None (a machine with a git home has its bundles locally)."""
    if ctx.store is not None:
        return ctx.store
    url = os.environ.get(BUNDLE_STORE_ENV, "").strip()
    if not url:
        return None
    m = _BUNDLE_STORE_RE.fullmatch(url)
    if not m:
        raise ConfigError(f"{BUNDLE_STORE_ENV}={url!r} is not s3://<bucket>[/<prefix>]")
    from vra.state import StateStore
    return StateStore(ctx.aws.tools_client("s3"), m.group(1), (m.group(2) or "").strip("/") or "vra-bundles",
                      ctx.state_dir, ctx.artifacts_dir.parent)


def load_manifest(ctx, build_id: str) -> dict:
    p = build_dir(ctx, build_id) / "manifest.json"
    if not p.exists():
        store = bundle_store(ctx)
        if store is not None:
            store.pull_prefix(f"artifacts/{build_id}/")
    if not p.exists():
        hint = "" if os.environ.get(BUNDLE_STORE_ENV) or ctx.store is not None else (
            f"; a CI job holds only its own execution's bundle: set {BUNDLE_STORE_ENV} (the pipeline stack does) "
            f"or use stateStore.type s3")
        raise UsageError(f"unknown build {build_id!r} (no {p}){hint}")
    return util.read_json(p)


def verify_bundle(ctx, manifest: dict, target: str, gated: bool = False) -> dict:
    """Recompute the cdk.out hash for one synth target and compare to the manifest.

    Without signing this is an integrity check (it catches accidental change, not a writer who recomputes the
    hashes). With ``build.signing.kmsKeyArn`` in vra.yaml the manifest digest must carry a valid KMS signature
    made at build time (stored outside the bundle under ``signatures/``); that is the tamper check.
    """
    t = manifest["targets"].get(target)
    if not t:
        raise UsageError(f"build {manifest['buildId']} has no synth for {target!r}; available: "
                         f"{sorted(manifest['targets'])}")
    d = build_dir(ctx, manifest["buildId"]) / "cdk.out" / target
    cache = d / ASSEMBLY_CACHE_DIR
    if cache.is_dir() and not cache.is_symlink():
        shutil.rmtree(cache)  # left by an earlier `cdk deploy --app` from this bundle
    got = assembly_hash(d)
    if got != t["sha256"]:
        raise VraError(f"bundle integrity check failed for {manifest['buildId']}/{target}: "
                       f"expected {t['sha256'][:16]}, got {got[:16]}")
    mp = build_dir(ctx, manifest["buildId"]) / "manifest.json"
    recorded = (build_dir(ctx, manifest["buildId"]) / "manifest.sha256").read_text().split()[0]
    msha = util.sha256_file(mp)
    if msha != recorded:
        raise VraError(f"manifest.json of {manifest['buildId']} does not match manifest.sha256")
    cli = manifest.get("cdkCli") or {}
    if cli.get("tarball"):
        tp = build_dir(ctx, manifest["buildId"]) / cli["tarball"]
        if not tp.exists() or util.sha256_file(tp) != cli.get("sha256"):
            raise VraError(f"vendored CDK CLI {cli['tarball']} of {manifest['buildId']} is missing or changed")
    vcli = manifest.get("vamscli") or {}
    if vcli.get("vendored"):
        wp = build_dir(ctx, manifest["buildId"]) / vcli["wheel"]
        if not wp.exists() or util.sha256_file(wp) != vcli["sha256"]:
            raise VraError(f"VamsCLI wheel {vcli['wheel']} of {manifest['buildId']} is missing or changed")
    sig = verify_signature(ctx, manifest["buildId"], msha)
    return {"target": target, "sha256": got, "manifestSha256": msha, "signature": sig}


def signing_config(ctx) -> dict:
    return (ctx.ledger.project.get("build") or {}).get("signing") or {}


def signature_path(ctx, build_id: str) -> Path:
    return ctx.state_dir / "signatures" / f"{build_id}.json"


def sign_manifest(ctx, build_id: str, manifest_sha: str) -> dict | None:
    """KMS-sign the manifest digest (asymmetric key, ``kms:Sign`` in the tools/build account)."""
    cfg = signing_config(ctx)
    if not cfg.get("kmsKeyArn") or ctx.dry_run:
        return None
    alg = cfg.get("algorithm", "ECDSA_SHA_256")
    kms = ctx.aws.tools_client("kms", cfg.get("region"))
    resp = kms.sign(KeyId=cfg["kmsKeyArn"], Message=bytes.fromhex(manifest_sha), MessageType="DIGEST",
                    SigningAlgorithm=alg)
    import base64
    doc = {"buildId": build_id, "manifestSha256": manifest_sha, "keyId": cfg["kmsKeyArn"], "algorithm": alg,
           "signature": base64.b64encode(resp["Signature"]).decode(), "signedAt": util.now_iso(),
           "signedBy": ctx.initiator}
    p = signature_path(ctx, build_id)
    util.write_json(p, doc)
    if ctx.store is not None:
        ctx.store.push_file(p)
    return {"keyId": cfg["kmsKeyArn"], "algorithm": alg}


def verify_signature(ctx, build_id: str, manifest_sha: str) -> dict | None:
    cfg = signing_config(ctx)
    if not cfg.get("kmsKeyArn"):
        return None
    p = signature_path(ctx, build_id)
    if not p.exists() and ctx.store is not None:
        ctx.store.pull_prefix(f"signatures/{build_id}.json")
    if not p.exists():
        raise VraError(f"build {build_id} has no signature (build.signing is configured); refusing to deploy it")
    doc = util.read_json(p)
    if doc.get("manifestSha256") != manifest_sha or doc.get("keyId") != cfg["kmsKeyArn"]:
        raise VraError(f"signature of {build_id} is for another manifest or key")
    import base64
    kms = ctx.aws.tools_client("kms", cfg.get("region"))
    ok = kms.verify(KeyId=cfg["kmsKeyArn"], Message=bytes.fromhex(manifest_sha), MessageType="DIGEST",
                    Signature=base64.b64decode(doc["signature"]),
                    SigningAlgorithm=doc.get("algorithm", "ECDSA_SHA_256")).get("SignatureValid")
    if not ok:
        raise VraError(f"KMS signature of {build_id} does not verify; the bundle may have been tampered with")
    return {"keyId": cfg["kmsKeyArn"], "valid": True}


def tag_signing_enabled(ctx) -> bool:
    cfg = signing_config(ctx)
    return bool(cfg.get("tags") and cfg.get("kmsKeyArn"))


def tag_signer_path(ctx, build_id: str) -> Path:
    return ctx.state_dir / "signatures" / f"{build_id}.tag-signer.pub"


def create_signed_tag(ctx, g, tag: str, commit: str, msg: str, build_id: str) -> dict | None:
    """Annotated tag signed by the KMS key in SSH signature format (``git verify-tag`` compatible): the tag object
    is written with ``git mktag`` and the ref created only if it does not exist yet."""
    from vra import sshsig
    if ctx.dry_run:
        return {"dryRun": True}
    cfg = signing_config(ctx)
    kms = ctx.aws.tools_client("kms", cfg.get("region"))
    point = sshsig.kms_public_point(kms, cfg["kmsKeyArn"])
    ident = g.out("var", "GIT_COMMITTER_IDENT")
    payload = f"object {commit}\ntype commit\ntag {tag}\ntagger {ident}\n\n{msg}\n"
    armored = sshsig.armor(point, sshsig.kms_sign(kms, cfg["kmsKeyArn"], payload.encode()))
    sha = g.run("mktag", input_text=payload + armored, mutating=True).stdout.strip()
    g.run("update-ref", f"refs/tags/{tag}", sha, "", mutating=True)
    pub = tag_signer_path(ctx, build_id)
    pub.parent.mkdir(parents=True, exist_ok=True)
    pub.write_text(sshsig.public_key_line(point) + "\n", encoding="utf-8")
    if ctx.store is not None:
        ctx.store.push_file(pub)
    return {"keyId": cfg["kmsKeyArn"], "format": "ssh", "tagObject": sha}


def verify_release_tag(ctx, manifest: dict) -> dict | None:
    """With ``build.signing.tags``: the build's release tag must carry a valid KMS signature, point at the build
    commit and name the manifest digest. Refuses unsigned or invalid tags (deploy, promote, rollback)."""
    from vra import sshsig
    if not tag_signing_enabled(ctx) or manifest.get("lane") != "release" or not manifest.get("tag"):
        return None
    tag = manifest["tag"]
    g = gitops.ensure_repo(ctx)
    if not g.rev(f"refs/tags/{tag}"):
        g.run("fetch", "origin", f"refs/tags/{tag}:refs/tags/{tag}", check=False)
    if g.out("cat-file", "-t", f"refs/tags/{tag}", check=False) != "tag":
        raise VraError(f"release tag {tag} of {manifest['buildId']} is missing or not an annotated tag; "
                       f"build.signing.tags requires a signed tag")
    raw = g.out("cat-file", "tag", f"refs/tags/{tag}")
    payload, armored = sshsig.split_signed_tag(raw + "\n")
    if not armored:
        raise VraError(f"release tag {tag} is not signed; build.signing.tags requires a KMS-signed tag")
    obj = next((ln.split(" ", 1)[1] for ln in payload.splitlines() if ln.startswith("object ")), None)
    if obj != manifest["commit"]:
        raise VraError(f"release tag {tag} points at {str(obj)[:12]}, the build was made from {manifest['commit'][:12]}")
    msha = (build_dir(ctx, manifest["buildId"]) / "manifest.sha256").read_text().split()[0]
    if f"manifest sha256 {msha}" not in payload:
        raise VraError(f"release tag {tag} names another manifest than {manifest['buildId']}'s")
    cfg = signing_config(ctx)
    res = sshsig.kms_verify(ctx.aws.tools_client("kms", cfg.get("region")), cfg["kmsKeyArn"], payload.encode(),
                            armored)
    if not res["valid"]:
        raise VraError(f"release tag {tag} signature does not verify ({res.get('reason')})")
    return {"tag": tag, "valid": True, "keyId": cfg["kmsKeyArn"]}


def synth_targets(ctx, lane: str, only_envs: list[str] | None = None) -> list[tuple[str, str | None]]:
    out = []
    for name in ctx.ledger.env_names():
        e = ctx.ledger.env(name)
        if e["lane"] != lane or (only_envs and name not in only_envs):
            continue
        out.append((name, None))
        if e.get("ab"):
            for slot in ("blue", "green"):
                out.append((name, slot))
    return out


def target_name(env: str, slot: str | None) -> str:
    return f"{env}@{slot}" if slot else env


CDK_BOUNDARY_CONTEXT = "@aws-cdk/core:permissionsBoundary"


def cdk_context_set(cdk_json: Path, key: str, value) -> bytes | None:
    """Set one context key of a scratch ``infra/cdk.json``; returns the file's previous bytes (None: no file)."""
    before = cdk_json.read_bytes() if cdk_json.exists() else None
    doc = json.loads(before) if before else {}
    doc.setdefault("context", {})[key] = value
    cdk_json.parent.mkdir(parents=True, exist_ok=True)
    cdk_json.write_text(json.dumps(doc, indent=4) + "\n")
    return before


@contextlib.contextmanager
def overlay_applied(ctx, src: Path, env: str) -> Iterator[list[str]]:
    """Copy ledger overlay files over the scratch checkout; restore the originals afterwards.

    An overlay file named ``<path>.vra-merge.json`` is deep-merged into the checkout's JSON file ``<path>``
    instead of replacing it (``vra iam apply`` writes ``infra/cdk.json.vra-merge.json`` with only the
    ``context.environments.aws`` block, so the fork's other cdk.json context stays the fork's)."""
    from vra.iam import MERGE_SUFFIX
    od = ctx.ledger.overlay_dir(env)
    saved: dict[str, bytes | None] = {}
    applied: list[str] = []
    files = sorted(p for p in od.rglob("*") if p.is_file()) if od.exists() else []
    try:
        for f in files:
            rel = f.relative_to(od).as_posix()
            target = rel[:-len(MERGE_SUFFIX)] if rel.endswith(MERGE_SUFFIX) else rel
            dest = (src / target).resolve()
            if not str(dest).startswith(str(src.resolve()) + os.sep):
                raise ConfigError(f"overlay path escapes checkout: {rel}")
            if target not in saved:
                saved[target] = dest.read_bytes() if dest.exists() else None
            dest.parent.mkdir(parents=True, exist_ok=True)
            if target != rel:
                try:
                    base = json.loads(dest.read_text()) if dest.exists() else {}
                    extra = json.loads(f.read_text())
                except json.JSONDecodeError as exc:
                    raise ConfigError(f"overlay {rel}: {target} and the merge file must be JSON ({exc})") from exc
                dest.write_text(json.dumps(util.deep_merge(base, extra), indent=4) + "\n")
            else:
                shutil.copyfile(f, dest)
            applied.append(rel)
        yield applied
    finally:
        for rel, data in saved.items():
            dest = src / rel
            if data is None:
                dest.unlink(missing_ok=True)
            else:
                dest.write_bytes(data)


def discard_dir(ctx, path: Path, sc=None) -> None:
    """Remove a stale directory (e.g. a partial cdk.out from a failed synth before a resume).

    CDK's Docker bundling leaves files owned by the container user (often ``nobody``) under
    ``asset.*/python``; the operator cannot delete them, and a plain rmtree made every resumed synth fail
    with PermissionError. Such a tree is moved out of the bundle into ``<work>/scratch/discarded/`` instead,
    so the bundle hash never sees it; clean that folder up with the container user's help later."""
    try:
        shutil.rmtree(path)
        return
    except PermissionError as exc:
        dest = ctx.scratch_dir / "discarded" / f"{path.parent.parent.name}-{path.name}-{util.now_iso().replace(':', '')}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, dest)
        if sc is not None:
            sc.log(f"could not delete {path} ({exc}); moved it to {dest}")


def cdk_argv(manifest_or_version: str | None, *args: str, allow_override: bool = True,
             tarball: str | None = None) -> list[str]:
    """The CLI the build was made with: the vendored tarball (``npx --package <tgz> cdk``), else
    ``npx --yes aws-cdk@<exact>``. ``VRA_CDK_CLI`` overrides only when ``allow_override`` (never for gated envs)."""
    override = os.environ.get("VRA_CDK_CLI") if allow_override else None
    if override:
        return ["npx", "--yes", override, *args]
    if tarball:
        return ["npx", "--yes", "--package", tarball, "cdk", *args]
    pinned = f"aws-cdk@{manifest_or_version}" if manifest_or_version else "aws-cdk"
    return ["npx", "--yes", pinned, *args]


NO_CREDENTIALS_ENV = {k: "" for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE",
                                      "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE",
                                      "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
                                      "AWS_CONTAINER_CREDENTIALS_FULL_URI")}
NO_CREDENTIALS_ENV.update({"AWS_EC2_METADATA_DISABLED": "true", "AWS_CONFIG_FILE": os.devnull,
                           "AWS_SHARED_CREDENTIALS_FILE": os.devnull})


def synth_credentials(ctx, env: str) -> tuple[dict, str]:
    """Credentials for `cdk synth` (which runs fork + upstream code and its whole dependency tree).

    Build-only identities only: ``synthRoleArn`` (e.g. a role that may only assume the CDK lookup role), else
    ``readOnlyRoleArn``. An ungated env without either falls back to its deploy role. An
    approval-gated env NEVER lends its deploy role to the build: it synthesizes with no AWS credentials at all
    (context lookups must then come from the committed cdk.context.json).
    """
    e = ctx.ledger.env(env)
    base = {"AWS_REGION": e["region"], "AWS_DEFAULT_REGION": e["region"], "CDK_DEFAULT_REGION": e["region"],
            "CDK_DEFAULT_ACCOUNT": str(e["account"])}
    role = "synth" if e.get("synthRoleArn") else ("readonly" if e.get("readOnlyRoleArn") else None)
    if role is None and not e.get("requiresApproval"):
        role = "deploy"
    if role is None:
        return {**base, **NO_CREDENTIALS_ENV}, "none"
    return {**base, **ctx.aws.subprocess_env(env, role)}, role


def prove_synth_role(ctx, env: str, role: str) -> None:
    """Assume the build-only synth role once, the way `vra credential-process` will inside cdk (with
    sts:SourceIdentity on gated envs), before minutes of synth. A role CDK cannot use otherwise surfaces deep in
    synth as "no credentials have been configured" (E2E-040: the trust lacked sts:SetSourceIdentity)."""
    from vra import permissions
    from vra.errors import PermissionDenied
    try:
        ctx.aws.client(env, "sts", role).get_caller_identity()
    except Exception as exc:  # noqa: BLE001
        arn = ctx.aws.role_arn(env, role) if hasattr(ctx.aws, "role_arn") else None
        denials = permissions.classify_exception(exc, "sts", arn)
        if denials:
            raise PermissionDenied(f"the {role} role of {env} for cdk synth could not be assumed ({arn}); with "
                                   f"setSourceIdentity on (default for approval-gated envs) its trust policy must "
                                   f"also allow sts:SetSourceIdentity", denials, env=env, role=role) from exc
        raise


LOOKUP_NEEDS_CREDENTIALS = "but no credentials have been configured"


def _raise_if_lookups_need_credentials(log_path, env: str, cred_role: str) -> None:
    """A synth with no AWS credentials (gated env without synthRoleArn / readOnlyRoleArn) whose configuration
    needs context lookups (e.g. an existing VPC) fails inside CDK with a generic exit 1. Say what to do."""
    if cred_role != "none" or not log_path:
        return
    try:
        text = Path(log_path).read_text(errors="replace")
    except OSError:
        return
    if LOOKUP_NEEDS_CREDENTIALS in text:
        raise NeedsHuman(f"cdk synth for {env} needs AWS context lookups, and {env} is approval-gated, so it "
                         f"synthesizes with no AWS credentials. Set envs/{env}/env.yaml synthRoleArn (a build-only "
                         f"role, e.g. one that may only use the CDK lookup role) or readOnlyRoleArn, or commit the "
                         f"lookup values in the fork's infra/cdk.context.json; then `vra resume`",
                         {"env": env, "synthCredentials": "none", "log": str(log_path)})


def vendor_cdk_cli(ctx, g, commit: str, bdir: Path, sc) -> dict:
    """Pack the exact aws-cdk CLI the lockfile pins into the bundle and check it against the lock integrity."""
    import base64
    import hashlib
    lock = cdk_cli_lock_at(g, commit)
    if not lock:
        return {"vendored": False, "reason": "infra/package-lock.json pins no aws-cdk CLI"}
    if ctx.dry_run:
        return {"dryRun": True, **lock}
    tools = bdir / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    p = ctx.shell(sc.log_path).run(["npm", "pack", f"aws-cdk@{lock['version']}", "--pack-destination", str(tools)],
                                   check=False, timeout=900)
    tgz = tools / f"aws-cdk-{lock['version']}.tgz"
    if p.returncode != 0 or not tgz.exists():
        return {"vendored": False, "version": lock["version"], "reason": f"npm pack failed (exit {p.returncode})"}
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(tgz.read_bytes()).digest()).decode()
    if lock.get("integrity") and integrity != lock["integrity"]:
        tgz.unlink(missing_ok=True)
        raise VraError(f"aws-cdk@{lock['version']} from the registry does not match the lockfile integrity "
                       f"({integrity[:24]}... != {lock['integrity'][:24]}...); check the npm mirror")
    return {"vendored": True, "version": lock["version"], "integrity": integrity,
            "tarball": tgz.relative_to(bdir).as_posix(), "sha256": util.sha256_file(tgz)}


VAMSCLI_SRC = "tools/VamsCLI"
VAMSCLI_DIR = "tools/vamscli"


def vendor_vamscli(ctx, src: Path, bdir: Path, sc) -> dict:
    """Build a VamsCLI wheel from the fork's own ``tools/VamsCLI`` at the build commit into the bundle (E2E-050
    B-050-18a). The deploy and test jobs install exactly this wheel, checked against the manifest's sha256, for the
    post-deploy smoke and the live-cli suite; never a VamsCLI from a package index."""
    import sys
    cli_src = src / VAMSCLI_SRC
    if not ((cli_src / "pyproject.toml").exists() or (cli_src / "setup.py").exists()):
        return {"vendored": False, "reason": f"{VAMSCLI_SRC} is not in the source of this build"}
    if ctx.dry_run:
        return {"dryRun": True}
    out = bdir / VAMSCLI_DIR
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    sh = ctx.shell(sc.log_path)
    # the engine's own environment may have no pip (`uv tool install`): a stdlib venv always does
    bvenv = ctx.scratch_dir / "vamscli-wheel-venv"
    py = bvenv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.exists():
        sh.run([sys.executable, "-m", "venv", str(bvenv)], timeout=300)
    p = sh.run([str(py), "-m", "pip", "wheel", "--no-deps", "--no-cache-dir", "--disable-pip-version-check",
                "-w", str(out), str(cli_src)], check=False, timeout=900)
    wheels = sorted(out.glob("vamscli-*.whl"))
    if p.returncode != 0 or len(wheels) != 1:
        raise VraError(f"building the VamsCLI wheel from {VAMSCLI_SRC} failed (exit {p.returncode}, "
                       f"{len(wheels)} wheel(s)); see {sc.log_path}")
    whl = wheels[0]
    return {"vendored": True, "wheel": whl.relative_to(bdir).as_posix(), "sha256": util.sha256_file(whl),
            "version": whl.name.split("-")[1]}


def build_environment() -> dict:
    """What built the bundle (recorded in the manifest; pin the image by digest in production CI)."""
    from vra import identity
    env = os.environ
    image = env.get("CODEBUILD_BUILD_IMAGE") or env.get("CI_JOB_IMAGE") or env.get("ImageOS")
    return {"ci": identity.ci_provider(), "image": image, "imageDigest": env.get("VRA_CI_IMAGE_DIGEST"),
            "pinnedByDigest": bool(image and "@sha256:" in image) or bool(env.get("VRA_CI_IMAGE_DIGEST"))}


def do_build(ctx, run, lane: str, ref: str, prefix: str = "", suites: list[str] | None = None,
             only_envs: list[str] | None = None, allow_missing_scanners: bool = False) -> dict:
    if lane not in ("release", "preview"):
        raise UsageError("--lane must be release or preview")
    proj = ctx.ledger.project
    from vra import core_patches, iam as iam_mod
    want_patches = core_patches.digest(core_patches.current(ctx.ledger.root))
    # IAM settings of every env in the build (mode, applied IT package + response, overlay files `vra iam apply`
    # writes): a bundle synthesized under other IAM settings is never reused (R50-2)
    want_iam = {e_: iam_mod.build_identity(ctx, e_) for e_ in sorted({e_ for e_, _s in
                                                                       synth_targets(ctx, lane, only_envs)})}

    def _resolve(sc):
        g = gitops.ensure_repo(ctx, sc.log_path)
        g.run("fetch", "origin", "--prune", "--tags")
        commit = gitops.resolve_commit(g, ref)
        version = vams_version_at(g, commit)
        if lane == "release":
            prefix_ = proj.get("tagPrefix", "")
            pat = f"{prefix_}v{version}-"
            existing = [t for t in g.out("tag", "--list", f"{pat}*").splitlines() if t]
            # with build.pushTags false a CI clone has none of the tags earlier builds made: the build ids the
            # shared home recorded count too, or two commits get the same id (E2E-050 B-050-20)
            known = {b: c for b, c in known_build_ids(ctx).items() if re.match(re.escape(pat) + r"\d+$", b)}
            tag = None
            for t in existing:
                if g.rev(f"refs/tags/{t}") == commit:
                    tag = t  # idempotent rebuild of an already tagged commit
                    break
            if tag is None:
                tag = next((b for b, c in sorted(known.items()) if c == commit), None)
            if tag is None:
                ns = [int(m.group(1)) for t in [*existing, *known]
                      if (m := re.match(re.escape(pat) + r"(\d+)$", t))]
                tag = f"{pat}{max(ns, default=0) + 1}"
            build_id = sanitize_build_id(tag)
        else:
            tag = None
            build_id = f"preview-v{version}-{commit[:12]}"
        # B-050-32: with a bundle store (CI, stateStore s3) a build id names one bundle forever. Reuse a stored
        # bundle of this commit that is still valid; otherwise take an id the store does not hold yet.
        store = None if ctx.dry_run else bundle_store(ctx)
        store_pick: dict = {}
        if store is not None:
            if lane == "release":
                known_all = {b: c for b, c in known_build_ids(ctx).items() if re.match(re.escape(pat) + r"\d+$", b)}
                taken = [t for t in [*existing, *known_all] if re.match(re.escape(pat) + r"\d+$", t)]
                mine = sorted({sanitize_build_id(t) for t in existing if g.rev(f"refs/tags/{t}") == commit}
                              | {b for b, c in known_all.items() if c == commit}, key=_build_seq, reverse=True)
                fresh = lambda n: f"{pat}{n}"  # noqa: E731
            else:
                base = build_id
                known_all = {b: c for b, c in known_build_ids(ctx).items() if b == base or b.startswith(base + "-r")}
                taken = list(known_all)
                mine = sorted(set(known_all) | {base}, key=_build_seq, reverse=True)
                fresh = lambda n: base if n <= 1 else f"{base}-r{n}"  # noqa: E731
            store_pick = pick_store_build_id(ctx, store, mine, taken, fresh, commit, want_patches, want_iam)
            if store_pick["buildId"] != build_id or store_pick.get("superseded"):
                build_id = store_pick["buildId"]
                tag = build_id if lane == "release" else None
        targets = [target_name(e, s) for e, s in synth_targets(ctx, lane, only_envs)]
        if not targets:
            raise ConfigError(f"no environments in lane {lane}")
        roll = version_roll_warnings(g, commit, version)
        if roll:
            run.state.setdefault("notes", []).extend(roll)
        return {"buildId": build_id, "tag": tag, "commit": commit, "vamsVersion": version, "lane": lane, "ref": ref,
                "cdkCliVersion": cdk_cli_version_at(g, commit), "targets": targets,
                "onTrunk": gitops.on_trunk(ctx, g, commit), "changeAuthors": change_authors(g, commit),
                "versionRoll": roll,
                **({"storeReuse": True} if store_pick.get("reuse") else {}),
                **({"supersedes": store_pick["superseded"]} if store_pick.get("superseded") else {})}

    info = run.step(f"{prefix}build.resolve", _resolve, "resolve build")
    build_id = info["buildId"]
    run.state["build"] = run.state.get("build") or build_id
    run.save()
    for note in ([f"bundle(s) {', '.join(info['supersedes'])} of {info['commit'][:12]} in the bundle store were "
                  f"synthesized with other IAM settings or core patches; this build is {build_id}"]
                 if info.get("supersedes") else []):
        if note not in run.state.setdefault("notes", []):
            run.state["notes"].append(note)
    bdir = build_dir(ctx, build_id)
    existing = bdir / "manifest.json"
    if info.get("storeReuse") and not existing.exists() and not ctx.dry_run:
        bundle_store(ctx).pull_prefix(f"artifacts/{build_id}/")   # B-050-32: CI holds no earlier bundles
    if existing.exists() and util.read_json(existing).get("commit") == info["commit"] and \
            util.read_json(existing).get("complete") and \
            core_patches.digest(util.read_json(existing).get("corePatches") or []) == want_patches:
        old = util.read_json(existing)
        if bundle_reusable(ctx, old, info["commit"], want_patches, want_iam):
            run.skip(f"{prefix}build.reuse", f"bundle {build_id} already complete for {info['commit'][:12]}")
            return old
        run.state.setdefault("notes", []).append(
            f"bundle {build_id} exists for {info['commit'][:12]} but was synthesized with other IAM settings; "
            f"rebuilding it (the IAM gate runs again)")
        if not ctx.dry_run:
            for stale in ("manifest.json", "manifest.sha256"):
                (bdir / stale).unlink(missing_ok=True)
            shutil.rmtree(bdir / "overlays", ignore_errors=True)

    src = ctx.scratch_dir / build_id / "src"

    def _source(sc):
        if ctx.dry_run:
            return {"dryRun": True, "src": str(src)}
        if src.exists():
            shutil.rmtree(src)
        g = gitops.Git(ctx, ctx.repo_dir, sc.log_path)
        gitops.archive_to(g, info["commit"], src)
        # ledger core patches (core-patches.md -> core-patches/*.patch) on the scratch copy, never on the fork
        patches = core_patches.apply_all(ctx.ledger.root, src, sc.log, where=f"build {build_id}")
        return {"src": str(src), "corePatches": patches}

    run.step(f"{prefix}build.source", _source, "scratch checkout")

    configured = proj.get("build", {}).get("unitSuites")
    # an explicit `build.unitSuites: []` means "no unit suites" (e.g. CI runs them elsewhere); only an
    # unset key falls back to the packaged defaults
    suite_names = suites if suites is not None else (configured if configured is not None
                                                    else tests_runner.default_build_suites())
    if suite_names:
        def _unit(sc):
            return tests_runner.run_unit_suites(ctx, run, src, suite_names, sc)
        run.step(f"{prefix}build.unit", _unit, "unit suites")
    else:
        run.skip(f"{prefix}build.unit", "no unit suites selected")

    web_built = {"done": False}

    def _ensure_node(sc, applied: list[str]):
        sh = ctx.shell(sc.log_path)
        web_touched = any(a.startswith("web/") for a in applied)
        if not (src / "web" / "dist").exists() or web_touched or not web_built["done"]:
            if not (src / "web" / "node_modules").exists():
                sh.run(["npm", "ci"], cwd=src / "web")
            sh.run(["npm", "run", "build.lowmem"], cwd=src / "web")
            web_built["done"] = not web_touched
        if not (src / "infra" / "node_modules").exists():
            sh.run(["npm", "ci"], cwd=src / "infra")

    from vra import iam
    gated_iam: set[str] = set()
    for env, slot in synth_targets(ctx, lane, only_envs):
        tname = target_name(env, slot)
        if env not in gated_iam and iam.needs_package(iam.settings(ctx.ledger.env(env))):
            gated_iam.add(env)
            # IT role package present, applied and matching env.yaml before minutes of synth (exit 10 otherwise)
            run.step(f"{prefix}build.iam.{env}", lambda sc, env=env: iam.gate(ctx, run, env, f"{prefix}build.iam.{env}"),
                     f"IAM package of {env}", revalidate=True)

        def _synth(sc, env=env, slot=slot, tname=tname):
            out = bdir / "cdk.out" / tname
            if ctx.dry_run:
                return {"dryRun": True, "target": tname, "out": str(out)}
            if out.exists():
                discard_dir(ctx, out, sc)
            out.parent.mkdir(parents=True, exist_ok=True)
            e = ctx.ledger.env(env)
            if not slot:
                ctx.ledger.stack_suffix(env)  # refuses when env.yaml and the overlay name different deployments
            with overlay_applied(ctx, src, env) as applied:
                slot_extra = slot_overlay(ctx, src, env, slot)
                _ensure_node(sc, applied)
                base = e["ab"]["slots"][slot] if slot else e.get("baseStackName")
                # names and placement come from the ledger, never from the fork's cdk.json or the host
                args = ["synth", "--all", "--quiet", "-o", str(out), "-c", "configFile=config.json",
                        "-c", f"stack-name={base}", "-c", f"region={e['region']}", "-c", f"account={e['account']}"]
                boundary = ctx.ledger.env(env).get("permissionsBoundaryName")
                if boundary:
                    # CDK applies it to every IAM role it synthesizes (required by the scoped execution policy).
                    # In the scratch infra/cdk.json, not as `-c`: the CDK CLI passes -c values as strings, and
                    # CDK reads this context key only as an object ({"name": ...}).
                    cdk_saved = cdk_context_set(src / "infra" / "cdk.json", CDK_BOUNDARY_CONTEXT, {"name": boundary})
                cred_role = "none"
                region_env = {"AWS_REGION": e["region"], "AWS_DEFAULT_REGION": e["region"],
                              "CDK_DEFAULT_REGION": e["region"], "CDK_DEFAULT_ACCOUNT": str(e["account"])}
                try:
                    creds, cred_role = synth_credentials(ctx, env)
                except Exception as exc:  # noqa: BLE001 - synth without lookups still works for most configs
                    creds = {**region_env, **NO_CREDENTIALS_ENV}  # never without the env's region and account
                    sc.log(f"no AWS credentials for synth lookups in {env}: {exc}")
                if cred_role in ("synth", "readonly") and not ctx.dry_run:
                    prove_synth_role(ctx, env, cred_role)
                sub_env, env_keys = synth_environment(ctx, env, {**region_env, **creds})
                sc.log(f"synth {tname}: AWS credentials = {cred_role}; environment keys: {', '.join(env_keys)}")
                iam_source = {"ref": info["ref"], "commit": info["commit"], "vamsVersion": info["vamsVersion"],
                              "build": build_id}
                try:
                    try:
                        ctx.shell(sc.log_path).run(["npx", "cdk", *args], cwd=src / "infra", env=sub_env)
                    finally:
                        if boundary and cdk_saved is not None:
                            (src / "infra" / "cdk.json").write_bytes(cdk_saved)
                        elif boundary:
                            (src / "infra" / "cdk.json").unlink(missing_ok=True)
                except VraError as exc:
                    _raise_if_denied(exc, env)
                    _raise_if_lookups_need_credentials(sc.log_path, env, cred_role)
                    if not slot:
                        # precreated roles: a role IT has not created fails synth; the report names it (exit 10)
                        iam.check_assembly(ctx, run, env, out, f"{prefix}build.synth.{tname}", iam_source,
                                           synth_failed=True)
                    raise
                # roles match the applied IT package; the boundary is really on every role (exit 10 / 20)
                iam_check = iam.check_assembly(ctx, run, env, out, f"{prefix}build.synth.{tname}", iam_source,
                                               boundary_only=bool(slot))
                snap = bdir / "overlays" / env
                if not snap.exists():
                    for rel in applied:
                        d = snap / rel
                        d.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(ctx.ledger.overlay_dir(env) / rel, d)
            hashes = util.tree_hashes(out)
            return {"target": tname, "env": env, "slot": slot, "files": len(hashes),
                    "sha256": util.combined_hash(hashes), "stacks": stacks_in_assembly(out), "overlay": applied,
                    "credentials": cred_role, "synthEnvKeys": env_keys, "slotOverlay": bool(slot_extra),
                    "baseStackName": base, "iam": iam_check}

        run.step(f"{prefix}build.synth.{tname}", _synth, f"synth {tname}")

    # Scanners fail closed: an enabled scanner whose tool is missing stops the build, unless the operator passed
    # --allow-missing-scanners. That waiver is recorded in the manifest and a waived build is refused by every
    # prod-class env (deploy gate).
    waived: list[str] = list(run.state.get("scannerWaivers") or [])

    def _waive(name: str, why: str) -> None:
        if name not in waived:
            waived.append(name)
            run.state["scannerWaivers"] = waived
            run.state.setdefault("notes", []).append(f"--allow-missing-scanners: {name} did not run ({why}); this "
                                                     f"build can never be deployed to a prod-class env")
            run.save()

    def _missing(name: str, why: str) -> None:
        if not allow_missing_scanners:
            raise NeedsHuman(f"{name} is enabled (vra.yaml build.{name}) but {why}; install it, or re-run with "
                             f"--allow-missing-scanners (recorded; such a build is never deployable to prod)")
        _waive(name, why)

    if proj.get("build", {}).get("ash"):
        def _ash(sc):
            if not ctx.dry_run and not shutil.which("ash"):
                _missing("ash", "the `ash` tool is not installed")
                return {"skipped": "ash not installed (waived)"}
            return tests_runner.run_unit_suites(ctx, run, src, ["ash"], sc, junit_subdir="ash")
        run.step(f"{prefix}build.ash", _ash, "ASH security scan")
    else:
        run.skip(f"{prefix}build.ash", "build.ash disabled")

    def _sbom(sc):
        res = generate_sbom(ctx, src, bdir / "sbom", sc)
        if res.get("failed"):
            raise VraError(f"SBOM generation failed for {res['failed']}")
        if res.get("missingTools"):
            _missing("sbom", f"{', '.join(res['missingTools'])} not installed")
        return res

    if proj.get("build", {}).get("sbom", True):
        run.step(f"{prefix}build.sbom", _sbom, "SBOM")
    else:
        run.skip(f"{prefix}build.sbom", "build.sbom disabled")

    def _cli(sc):
        g = gitops.Git(ctx, ctx.repo_dir, sc.log_path)
        return vendor_cdk_cli(ctx, g, info["commit"], bdir, sc)

    cli_info = run.step(f"{prefix}build.cdk-cli", _cli, "vendor the pinned CDK CLI")
    vamscli_info = run.step(f"{prefix}build.vamscli", lambda sc: vendor_vamscli(ctx, src, bdir, sc),
                            "VamsCLI wheel from the fork")

    def _bundle(sc):
        if ctx.dry_run:
            return {"dryRun": True, "buildId": build_id}
        g = gitops.Git(ctx, ctx.repo_dir, sc.log_path)
        mig = bdir / "migrations"
        if mig.exists():
            shutil.rmtree(mig)
        try:
            gitops.archive_to(g, info["commit"], bdir / "migrations-src", [MIGRATIONS_DIR])
            shutil.move(str(bdir / "migrations-src" / MIGRATIONS_DIR), str(mig))
            shutil.rmtree(bdir / "migrations-src", ignore_errors=True)
        except VraError:
            mig.mkdir(parents=True, exist_ok=True)
        targets = {}
        for t in info["targets"]:
            res = run.step_result(f"{prefix}build.synth.{t}") or {}
            targets[t] = {k: res.get(k) for k in ("env", "slot", "sha256", "files", "stacks", "overlay",
                                                  "synthEnvKeys", "baseStackName")}
        unit = run.step_result(f"{prefix}build.unit")
        sbom = run.step_result(f"{prefix}build.sbom")
        ash = run.step_result(f"{prefix}build.ash")
        manifest = {"schemaVersion": 1, "buildId": build_id, "lane": lane, "promotable": lane == "release",
                    "ref": ref, "commit": info["commit"], "tag": info["tag"], "vamsVersion": info["vamsVersion"],
                    "cdkCliVersion": info["cdkCliVersion"], "createdAt": util.now_iso(), "createdBy": ctx.initiator,
                    "runId": run.run_id, "targets": targets,
                    "migrationsSha256": util.combined_hash(util.tree_hashes(mig)),
                    "overlaysSha256": util.combined_hash(util.tree_hashes(bdir / "overlays")),
                    "unitSuites": (unit or {}).get("summary"), "sbom": sbom, "ash": (ash or {}).get("summary"),
                    "scanners": {"ash": bool(proj.get("build", {}).get("ash")),
                                 "sbom": bool(proj.get("build", {}).get("sbom", True)),
                                 "waived": list(run.state.get("scannerWaivers") or [])},
                    "cdkCli": cli_info if (cli_info or {}).get("vendored") else
                    {"version": info["cdkCliVersion"], "vendored": False, "reason": (cli_info or {}).get("reason")},
                    "vamscli": vamscli_info or {"vendored": False},
                    "buildEnvironment": build_environment(), "signing": signing_config(ctx).get("kmsKeyArn"),
                    "onTrunkAtBuild": info["onTrunk"], "changeAuthors": info.get("changeAuthors") or [],
                    "versionRoll": info.get("versionRoll") or [],
                    "iam": want_iam,
                    "corePatches": [{k: r[k] for k in ("file", "sha256", "status")}
                                    for r in (run.step_result(f"{prefix}build.source") or {}).get("corePatches") or []],
                    "complete": True}
        util.write_json(bdir / "files.sha256.json", util.tree_hashes(bdir, exclude=["manifest.json", "manifest.sha256",
                                                                                     "files.sha256.json"]))
        util.write_json(bdir / "manifest.json", manifest)
        msha = util.sha256_file(bdir / "manifest.json")
        (bdir / "manifest.sha256").write_text(f"{msha}  manifest.json\n")
        sig = sign_manifest(ctx, build_id, msha)
        sc.artifact(bdir / "manifest.json")
        shutil.rmtree(ctx.scratch_dir / build_id, ignore_errors=True)
        return {"buildId": build_id, "manifestSha256": msha, "signed": bool(sig)}

    bundle = run.step(f"{prefix}build.bundle", _bundle, "bundle + manifest")

    if lane == "release":
        def _tag(sc):
            g = gitops.Git(ctx, ctx.repo_dir, sc.log_path)
            tag = info["tag"]
            existing_sha = g.rev(f"refs/tags/{tag}")
            if existing_sha and existing_sha != info["commit"]:
                raise NeedsHuman(f"tag {tag} exists on a different commit {existing_sha[:12]}")
            signed = None
            if not existing_sha:
                msg = f"vra build {build_id}\nVAMS {info['vamsVersion']}\nmanifest sha256 {bundle.get('manifestSha256')}"
                if tag_signing_enabled(ctx):
                    signed = create_signed_tag(ctx, g, tag, info["commit"], msg, build_id)
                else:
                    flag = "-s" if proj.get("build", {}).get("signTags") else "-a"
                    g.run("tag", flag, tag, info["commit"], "-m", msg, mutating=True)
            pushed = False
            if proj.get("build", {}).get("pushTags", True) and not ctx.dry_run:
                g.push("origin", f"refs/tags/{tag}")
                pushed = True
            return {"tag": tag, "pushed": pushed, "signed": signed}
        run.step(f"{prefix}build.tag", _tag, "tag")
    else:
        run.skip(f"{prefix}build.tag", "preview builds are never tagged (never promotable)")

    def _upload(sc):
        if ctx.dry_run:
            return {"uploaded": False}
        out: dict = {}
        if ctx.store is None:
            ctx.home.publish(f"build {build_id}")
            out["home"] = ctx.home.sync.type
        store = bundle_store(ctx)
        if store is None:
            return {"uploaded": False, **out}
        # create-only: a build id names one bundle forever; a different bundle under the same id is refused.
        # B-050-33: refuse before writing anything when the store already holds another manifest for this id;
        # the per-file create-only check alone added this build's new files to the stored bundle.
        # (objects without a manifest are an interrupted upload: the per-file check below finishes or refuses it)
        held = store_manifest(store, build_id)
        if held and held != util.read_json(bdir / "manifest.json"):
            raise NeedsHuman(f"build {build_id} already exists in the bundle store s3://{store.bucket}/{store.prefix} "
                             f"with a different manifest; nothing was uploaded. Start a new build: vra gives it the "
                             f"next free build id")
        clashes = store.push_dir(bdir, create_only=True)
        if clashes:
            raise NeedsHuman(f"build {build_id} already exists in the bundle store s3://{store.bucket}/{store.prefix} "
                             f"with different content ({clashes[:5]}); another machine built this id from another "
                             f"commit or manifest")
        return {"uploaded": True, "store": f"s3://{store.bucket}/{store.prefix}", **out}

    run.step(f"{prefix}build.upload", _upload, "upload bundle")
    if ctx.dry_run:
        return {"dryRun": True, **info}
    return util.read_json(bdir / "manifest.json")


def known_build_ids(ctx) -> dict[str, str]:
    """Build id -> commit for every build the shared home knows: build runs (their resolve step) and bundles in
    ``artifacts/``. Unreadable entries are skipped; nothing is written."""
    out: dict[str, str] = {}
    runs = Path(ctx.state_dir) / "runs"
    for p in sorted(runs.glob("*/state.json")) if runs.exists() else []:
        try:
            s = util.read_json(p)
        except Exception:  # noqa: BLE001
            continue
        if s.get("workflow") != "build" or s.get("dryRun"):
            continue
        steps = s.get("steps") or []
        steps = steps if isinstance(steps, list) else [dict(id=k, **v) for k, v in steps.items()]
        res = next((x.get("result") for x in steps if str(x.get("id", "")).endswith("build.resolve")), None) or {}
        if res.get("buildId") and res.get("commit"):
            out.setdefault(res["buildId"], res["commit"])
    arts = Path(ctx.home.root) / "artifacts" if getattr(ctx, "home", None) is not None else None
    for m in sorted(arts.glob("*/manifest.json")) if arts and arts.exists() else []:
        try:
            d = util.read_json(m)
        except Exception:  # noqa: BLE001
            continue
        if d.get("buildId") and d.get("commit"):
            out.setdefault(d["buildId"], d["commit"])
    return out


def _build_seq(build_id: str) -> int:
    """The trailing number of a release id (`...-12`) or preview rebuild (`...-r2`); 1 for a first preview."""
    m = re.search(r"(?:-r)?(\d+)$", build_id)
    return int(m.group(1)) if m and (re.search(r"-r\d+$", build_id) or not build_id.startswith("preview-")) else 1


def bundle_reusable(ctx, old: dict, commit: str, want_patches: str, want_iam: dict) -> bool:
    """A complete bundle of ``commit`` with the same core patches and the IAM settings every env has now (R50-2;
    standard-only envs on both sides need no IAM identity match)."""
    from vra import core_patches, iam as iam_mod
    if old.get("commit") != commit or not old.get("complete") or \
            core_patches.digest(old.get("corePatches") or []) != want_patches:
        return False
    return (old.get("iam") or {}) == want_iam or not any(
        iam_mod.needs_package(iam_mod.settings(ctx.ledger.env(x)))
        or (old.get("iam") or {}).get(x, {}).get("modes", ["standard"]) != ["standard"] for x in want_iam)


def store_manifest(store, build_id: str) -> dict | None:
    """The manifest a bundle store holds for ``build_id``: None when the id is free (no object under it), {} when
    objects exist but no manifest (a partial or refused upload: the id is taken)."""
    keys = store.list_keys(f"artifacts/{build_id}/")
    if not keys:
        return None
    mk = store._key(f"artifacts/{build_id}/manifest.json")
    if mk not in keys:
        return {}
    try:
        return json.loads(store.get_bytes(mk))
    except Exception:  # noqa: BLE001 - an unreadable manifest takes the id but is never reused
        return {}


def pick_store_build_id(ctx, store, mine: list[str], taken: list[str], fresh, commit: str, want_patches: str,
                        want_iam: dict) -> dict:
    """B-050-32. ``mine``: ids already given to ``commit`` (newest first); ``taken``: every id of the series;
    ``fresh(n)``: the id with sequence ``n``. The newest stored bundle of the commit that is still reusable is
    reused; ids the store holds with other content are superseded; else the first id the store does not hold."""
    superseded = []
    for b in mine:
        m = store_manifest(store, b)
        if m is None:
            return {"buildId": b, "superseded": superseded}          # given to this commit, never uploaded
        if m and bundle_reusable(ctx, m, commit, want_patches, want_iam):
            return {"buildId": b, "reuse": True, "superseded": superseded}
        superseded.append(b)
    n = max([_build_seq(t) for t in taken], default=0) + 1
    for _ in range(100):
        b = fresh(n)
        if store_manifest(store, b) is None:
            return {"buildId": b, "superseded": superseded}
        n += 1
    raise NeedsHuman(f"no free build id in the bundle store after {fresh(n - 100)}..{fresh(n - 1)}")


def sanitize_build_id(tag: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", tag)


def stacks_in_assembly(out: Path) -> list[str]:
    mp = out / "manifest.json"
    if not mp.exists():
        return []
    try:
        m = util.read_json(mp)
    except Exception:  # noqa: BLE001
        return []
    names = []
    for art_id, art in (m.get("artifacts") or {}).items():
        if art.get("type") == "aws:cloudformation:stack":
            names.append((art.get("properties") or {}).get("stackName") or art.get("displayName") or art_id)
    return sorted(names)


def generate_sbom(ctx, src: Path, out: Path, sc) -> dict:
    """CycloneDX SBOMs for infra/, web/ (npm lockfiles) and backend/ (requirements). Reports ``missingTools``
    and ``failed`` instead of silently skipping, so the caller can fail closed."""
    res: dict = {"generated": [], "skipped": [], "failed": [], "missingTools": []}
    if ctx.dry_run:
        return {"dryRun": True}
    out.mkdir(parents=True, exist_ok=True)
    sh = ctx.shell(sc.log_path)
    npm_tool = shutil.which("cyclonedx-npm")
    py_tool = shutil.which("cyclonedx-py")
    npm_inputs = [sub for sub in ("infra", "web") if (src / sub / "package-lock.json").exists()]
    req = src / "backend" / "requirements.txt"
    if npm_inputs and not npm_tool:
        res["missingTools"].append("cyclonedx-npm")
    if req.exists() and not py_tool:
        res["missingTools"].append("cyclonedx-py")
    for sub in ("infra", "web"):
        if sub not in npm_inputs:
            res["skipped"].append(f"{sub} (no package-lock.json)")
        elif npm_tool:
            f = out / f"{sub}.cdx.json"
            p = sh.run([npm_tool, "--output-file", str(f), "--package-lock-only"], cwd=src / sub, check=False)
            (res["generated"] if p.returncode == 0 else res["failed"]).append(str(f.name))
    if req.exists() and py_tool:
        f = out / "backend.cdx.json"
        p = sh.run([py_tool, "requirements", str(req), "--of", "JSON", "-o", str(f)], check=False)
        (res["generated"] if p.returncode == 0 else res["failed"]).append(str(f.name))
    elif not req.exists():
        res["skipped"].append("backend (no requirements.txt)")
    res["hashes"] = util.tree_hashes(out)
    return res


def _raise_if_denied(exc: VraError, env: str) -> None:
    from vra import permissions
    from vra.errors import PermissionDenied
    text = getattr(exc, "output", "") or exc.message
    denials = permissions.classify_text(text, "cdk")
    if denials:
        raise PermissionDenied(f"synth for {env} was denied", denials, env=env) from exc


@workflow("build")
def wf_build(ctx, run, args):
    return do_build(ctx, run, args["lane"], args["ref"], suites=args.get("suites"), only_envs=args.get("envs"),
                    allow_missing_scanners=bool(args.get("allowMissingScanners")))
