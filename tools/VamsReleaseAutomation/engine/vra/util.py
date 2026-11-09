# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Small shared helpers: subprocess with logging + redaction, file IO, hashing, time."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from vra.errors import CommandFailed

# Values that must never reach a log: AWS keys, session tokens, bearer tokens, passwords.
_SECRET_KEYS = (r"aws_secret_access_key|aws_session_token|secretaccesskey|sessiontoken|password|passwd|secret_value|"
                r"secretstring|client_secret|clientsecret|api[_-]?key|x-api-key|access_token|refresh_token|id_token|"
                r"idtoken|accesstoken|refreshtoken|token")
_REDACT_PATTERNS = [
    (re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"), "***"),
    # JSON / quoted forms: "password": "x", 'IdToken': 'eyJ..'
    (re.compile(rf"(?i)([\"']?\b(?:{_SECRET_KEYS})\b[\"']?\s*[:=]\s*)\"(?:[^\"\\]|\\.)*\""), r'\1"***"'),
    (re.compile(rf"(?i)([\"']?\b(?:{_SECRET_KEYS})\b[\"']?\s*[:=]\s*)'[^']*'"), r"\1'***'"),
    # bare key=value / key: value
    (re.compile(rf"(?i)\b({_SECRET_KEYS})(\s*[=:]\s*)(?![\"'*])(\S+)"), r"\1\2***"),
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic)\s+)\S+"), r"\1***"),
    (re.compile(r"(?i)\b((?:set-)?cookie:\s*)[^\r\n]+"), r"\1***"),
    # JWTs (Cognito id/access tokens) wherever they appear
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "***.jwt.***"),
    (re.compile(r"https://hooks\.slack\.com/\S+"), "https://hooks.slack.com/***"),
    (re.compile(r"https://[a-z0-9.-]*webhook\.office\.com/\S+"), "https://***.webhook.office.com/***"),
]
_LITERAL_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    """Values known to be secret at run time (e.g. the live-test password read from Secrets Manager) are
    redacted literally from every log, JUnit file and report, whatever format they appear in."""
    if value and len(value) >= 4:
        _LITERAL_SECRETS.add(value)


def redact(text: str) -> str:
    out = text
    for s in sorted(_LITERAL_SECRETS, key=len, reverse=True):
        out = out.replace(s, "***")
    for pat, repl in _REDACT_PATTERNS:
        out = pat.sub(repl, out)
    return out


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso_from_epoch(value: Any) -> str:
    try:
        return _dt.datetime.fromtimestamp(float(value), _dt.timezone.utc).replace(microsecond=0) \
            .isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return str(value)


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_hashes(root: Path, exclude: Iterable[str] = ()) -> dict[str, str]:
    """Deterministic {relative posix path: sha256} for every file under ``root``."""
    excl = set(exclude)
    result: dict[str, str] = {}
    if not root.exists():
        return result
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            rel = p.relative_to(root).as_posix()
            if any(rel == e or rel.startswith(e + "/") for e in excl):
                continue
            result[rel] = sha256_file(p)
    return result


def combined_hash(hashes: Mapping[str, str]) -> str:
    h = hashlib.sha256()
    for k in sorted(hashes):
        h.update(f"{k}\0{hashes[k]}\n".encode())
    return h.hexdigest()


def read_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class _NoAliasDumper(yaml.SafeDumper):
    """SafeDumper that writes a repeated object in full each time instead of as an anchor and alias.

    CloudFormation rejects YAML aliases ("YAML aliases are not allowed in CloudFormation templates"), and a
    reader of a ledger file should not have to resolve them either.
    """

    def ignore_aliases(self, data: Any) -> bool:
        return True


def dump_yaml(data: Any, **kwargs: Any) -> str:
    """``yaml.safe_dump`` without anchors or aliases; every engine-written YAML goes through this."""
    kwargs.setdefault("sort_keys", False)
    return yaml.dump(data, Dumper=_NoAliasDumper, **kwargs)


def write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, dump_yaml(data, default_flow_style=False))


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, indent=2, sort_keys=False, default=str) + "\n")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


CLEAN_ENV = "__VRA_CLEAN_ENV__"


class Shell:
    """Runs subprocesses. Logs (redacted) to a log file; never uses a shell string.

    Tests can subclass/replace to record calls instead of running them.
    """

    def __init__(self, log_path: Path | None = None, dry_run: bool = False):
        self.log_path = log_path
        self.dry_run = dry_run

    def with_log(self, log_path: Path | None) -> "Shell":
        return type(self)(log_path=log_path, dry_run=self.dry_run)

    def _log(self, text: str) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(redact(text))
            if not text.endswith("\n"):
                fh.write("\n")

    def run(self, argv: Sequence[str], cwd: Path | str | None = None, env: Mapping[str, str] | None = None,
            check: bool = True, input_text: str | None = None, mutating: bool = True,
            timeout: int | None = None) -> subprocess.CompletedProcess:
        """Run ``argv``. ``mutating=False`` commands still run under --dry-run (reads)."""
        argv = [str(a) for a in argv]
        printable = " ".join(shlex.quote(a) for a in argv)
        self._log(f"$ {printable}  (cwd={cwd or os.getcwd()})")
        if self.dry_run and mutating:
            self._log("[dry-run] not executed")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        env = dict(env or {})
        # CLEAN_ENV: the caller built the complete environment (cdk synth); nothing is inherited from this process
        full_env = {} if env.pop(CLEAN_ENV, None) else dict(os.environ)
        full_env.update(env)
        proc = subprocess.run(argv, cwd=str(cwd) if cwd else None, env=full_env, input=input_text,
                              capture_output=True, text=True, timeout=timeout)
        self._log(proc.stdout or "")
        if proc.stderr:
            self._log(proc.stderr)
        self._log(f"[exit {proc.returncode}]")
        if check and proc.returncode != 0:
            raise CommandFailed(f"command failed ({proc.returncode}): {redact(printable)}",
                                proc.returncode, (proc.stdout or "") + (proc.stderr or ""))
        return proc


def deep_merge(base: Any, override: Any) -> Any:
    """Recursively merge dicts; ``override`` wins on leaves and lists."""
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for k, v in override.items():
            out[k] = deep_merge(base[k], v) if k in base else v
        return out
    return override


def key_paths(obj: Any, prefix: str = "") -> set[str]:
    """Dotted key paths of every leaf/dict key in a nested dict (lists are leaves)."""
    paths: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            paths.add(p)
            paths |= key_paths(v, p)
    return paths


def get_path(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def version_tuple(v: str) -> tuple[int, ...]:
    m = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", v.strip())
    if not m:
        raise ValueError(f"not a version: {v!r}")
    return tuple(int(x) if x else 0 for x in m.groups())


def minor(v: str) -> str:
    t = version_tuple(v)
    return f"{t[0]}.{t[1]}"
