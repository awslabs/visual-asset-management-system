# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Git host adapters behind one interface: open (or find) a pull/merge request.

github -> `gh`, gitlab -> `glab`, bitbucket -> REST 2.0 (token from Secrets Manager by NAME),
codecommit -> boto3, local -> a JSON file under the work dir (air-gapped / tests).
No adapter ever pushes; pushing is done by gitops with plain (never forced) pushes.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from vra import util
from vra.errors import UsageError


class GitHost:
    type = "base"

    def __init__(self, ctx, cfg: dict):
        self.ctx = ctx
        self.cfg = cfg

    def find_pr(self, head: str, base: str) -> dict | None:
        raise NotImplementedError

    def create_pr(self, head: str, base: str, title: str, body: str) -> dict:
        raise NotImplementedError

    def ensure_pr(self, head: str, base: str, title: str, body: str) -> dict:
        found = self.find_pr(head, base)
        if found:
            return {**found, "existing": True}
        if self.ctx.dry_run:
            return {"dryRun": True, "head": head, "base": base, "title": title, "host": self.type}
        return {**self.create_pr(head, base, title, body), "existing": False}


class GitHubHost(GitHost):
    type = "github"

    def _sh(self):
        return self.ctx.shell()

    def find_pr(self, head, base):
        p = self._sh().run(["gh", "pr", "list", "--repo", self.cfg["repo"], "--head", head, "--base", base,
                            "--state", "open", "--json", "url,number"], mutating=False, check=False)
        if p.returncode != 0 or not p.stdout.strip():
            return None
        items = json.loads(p.stdout)
        return {"url": items[0]["url"], "id": items[0]["number"], "host": self.type} if items else None

    def create_pr(self, head, base, title, body):
        bf = self.ctx.work_dir / "tmp" / f"pr-body-{util.sha256_bytes(head.encode())[:8]}.md"
        util.atomic_write(bf, body)
        p = self._sh().run(["gh", "pr", "create", "--repo", self.cfg["repo"], "--head", head, "--base", base,
                            "--title", title, "--body-file", str(bf)])
        return {"url": p.stdout.strip().splitlines()[-1] if p.stdout.strip() else None, "host": self.type}


class GitLabHost(GitHost):
    type = "gitlab"

    def find_pr(self, head, base):
        p = self.ctx.shell().run(["glab", "mr", "list", "--repo", self.cfg["repo"], "--source-branch", head,
                                  "--target-branch", base, "--output", "json"], mutating=False, check=False)
        if p.returncode != 0 or not p.stdout.strip():
            return None
        items = json.loads(p.stdout)
        return {"url": items[0].get("web_url"), "id": items[0].get("iid"), "host": self.type} if items else None

    def create_pr(self, head, base, title, body):
        p = self.ctx.shell().run(["glab", "mr", "create", "--repo", self.cfg["repo"], "--source-branch", head,
                                  "--target-branch", base, "--title", title, "--description", body, "--yes"])
        return {"url": p.stdout.strip().splitlines()[-1] if p.stdout.strip() else None, "host": self.type}


class BitbucketHost(GitHost):
    type = "bitbucket"

    def _token(self) -> str:
        name = self.cfg.get("tokenSecret")
        if not name:
            raise UsageError("githost.tokenSecret (Secrets Manager secret name) is required for bitbucket")
        sm = self.ctx.aws.tools_client("secretsmanager", self.cfg.get("region"))
        return sm.get_secret_value(SecretId=name)["SecretString"].strip()

    def _api(self, method: str, path: str, payload: dict | None = None) -> Any:
        base = (self.cfg.get("apiUrl") or "https://api.bitbucket.org/2.0").rstrip("/")
        if not base.startswith("https://"):
            raise UsageError("githost.apiUrl must be an https:// URL")
        req = urllib.request.Request(f"{base}{path}", method=method,
                                     data=json.dumps(payload).encode() if payload is not None else None,
                                     headers={"Authorization": f"Bearer {self._token()}",
                                              "Content-Type": "application/json"})
        # the URL is checked for https:// above
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 # nosec B310 - https enforced above
            return json.loads(resp.read().decode() or "{}")

    def find_pr(self, head, base):
        q = urllib.parse.quote(f'source.branch.name="{head}" AND destination.branch.name="{base}" AND state="OPEN"')
        data = self._api("GET", f"/repositories/{self.cfg['repo']}/pullrequests?q={q}")
        vals = data.get("values", [])
        return {"url": vals[0]["links"]["html"]["href"], "id": vals[0]["id"], "host": self.type} if vals else None

    def create_pr(self, head, base, title, body):
        data = self._api("POST", f"/repositories/{self.cfg['repo']}/pullrequests",
                         {"title": title, "description": body, "source": {"branch": {"name": head}},
                          "destination": {"branch": {"name": base}}, "close_source_branch": False})
        return {"url": data.get("links", {}).get("html", {}).get("href"), "id": data.get("id"), "host": self.type}


class CodeCommitHost(GitHost):
    type = "codecommit"

    def _client(self):
        return self.ctx.aws.tools_client("codecommit", self.cfg.get("region"))

    def find_pr(self, head, base):
        c = self._client()
        repo = self.cfg["repositoryName"]
        token = None
        while True:
            kw = {"repositoryName": repo, "pullRequestStatus": "OPEN"}
            if token:
                kw["nextToken"] = token
            resp = c.list_pull_requests(**kw)
            for pid in resp.get("pullRequestIds", []):
                pr = c.get_pull_request(pullRequestId=pid)["pullRequest"]
                for t in pr.get("pullRequestTargets", []):
                    if t.get("sourceReference", "").endswith(head) and t.get("destinationReference", "").endswith(base):
                        return {"id": pid, "url": None, "host": self.type}
            token = resp.get("nextToken")
            if not token:
                return None

    def create_pr(self, head, base, title, body):
        resp = self._client().create_pull_request(
            title=title[:150], description=body[:10240],
            targets=[{"repositoryName": self.cfg["repositoryName"], "sourceReference": head,
                      "destinationReference": base}])
        return {"id": resp["pullRequest"]["pullRequestId"], "url": None, "host": self.type}


class LocalHost(GitHost):
    """Writes PR requests to ``<work>/prs/<head>.json`` (for air-gapped installs and tests)."""

    type = "local"

    def _path(self, head: str) -> Path:
        return self.ctx.work_dir / "prs" / (head.replace("/", "__") + ".json")

    def find_pr(self, head, base):
        p = self._path(head)
        if p.exists():
            d = util.read_json(p)
            if d.get("base") == base and d.get("state") == "open":
                return {"id": d["id"], "url": str(p), "host": self.type}
        return None

    def create_pr(self, head, base, title, body):
        p = self._path(head)
        rec = {"id": head, "head": head, "base": base, "title": title, "body": body, "state": "open",
               "createdAt": util.now_iso()}
        util.write_json(p, rec)
        return {"id": head, "url": str(p), "host": self.type}


ADAPTERS = {"github": GitHubHost, "gitlab": GitLabHost, "bitbucket": BitbucketHost, "codecommit": CodeCommitHost,
            "local": LocalHost}


def make(ctx) -> GitHost:
    if ctx.githost_factory:
        return ctx.githost_factory(ctx)
    cfg = ctx.ledger.project.get("githost", {"type": "local"})
    cls = ADAPTERS.get(cfg.get("type", "local"))
    if cls is None:
        raise UsageError(f"unknown githost type {cfg.get('type')}")
    return cls(ctx, cfg)
