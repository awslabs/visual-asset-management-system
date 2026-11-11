---
title: "Developer guide: testing and release"
sidebar_position: 26
description: "How to test release automation offline, what the package check covers, and how the tool is versioned and released with VAMS."
---

# Developer guide: testing and release

## Set up

```bash
cd tools/VamsReleaseAutomation
python3.13 -m venv .venv && . .venv/bin/activate     # Python 3.12 is the runtime; 3.13 for development
pip install -e '.[dev]'
make check
```

## The package check

`make check` (`scripts/check.sh`) runs offline: no AWS calls and no network. AWS clients point at an
unreachable endpoint with test credentials, so a test that forgets a stub fails instead of calling AWS.

| Step             | What it checks                                                                                                                                                                                                                                                       |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Engine tests     | `engine/tests` (pytest)                                                                                                                                                                                                                                              |
| Guard tests      | `adapters/shared/test_vra_guard.py`: every bypass spelling and event shape                                                                                                                                                                                           |
| Script tests     | `scripts/tests`, including the agent policy check                                                                                                                                                                                                                    |
| IAM and CI tests | `iam/tests`, `cicd/tests`                                                                                                                                                                                                                                            |
| Parse            | Every YAML and JSON file                                                                                                                                                                                                                                             |
| Ledger template  | `vra ledger validate` on `ledger-template/` and its AWS GovCloud (US) variant                                                                                                                                                                                        |
| Engine copies    | The engine's copies of the IAM manifests and the path policy equal the reviewed files                                                                                                                                                                                |
| Doc commands     | Every `vra ...` command and flag in the package and documentation Markdown exists in the CLI                                                                                                                                                                         |
| Agent policy     | The human-only set is the same in the engine, the skill, the MCP server, the guard, the Kiro agents, the Claude Code settings and the Cursor CLI permissions; the Cursor hooks run the guard fail-closed; every workflow step the guard blocks is handed to a person |
| Kiro agents      | `kiro-cli agent validate` and the universal format (only when `kiro-cli` is installed)                                                                                                                                                                               |
| FILES.md         | The file index is current (`make index` regenerates it)                                                                                                                                                                                                              |
| Links            | Relative Markdown links and GitHub source links resolve; with `VAMS_REPO=<VAMS clone>` also links into other VAMS documentation pages                                                                                                                                |
| Neutrality       | Placeholder account ids only, no hard-coded `arn:aws:` in code and templates, inclusive language; `VRA_NEUTRAL_TERMS_FILE` adds private terms                                                                                                                        |

Format the package's Markdown, YAML and JSON with the VAMS root prettier configuration (`npm run
prettier-check` at the repository root); the CI job runs it. Outside `make check`, run the CDK app's
suite (`cd cicd/pipeline && npm ci && npm run typecheck && npm test`, which includes cdk-nag) and
actionlint over `cicd/github-actions/*.yml` when you change them.

## Writing tests

-   Every behaviour change gets a regression test that fails on the old code.
-   Use the fixtures in `engine/tests/conftest.py`: a fake upstream and fork (local bare git repositories),
    a ledger, a fake AWS factory (botocore Stubber and moto), and a fake shell that runs git for real and
    simulates npm, npx and cdk.
-   Two-machine behaviour is tested with two homes cloned from one local bare remote
    (`engine/tests/test_shared_home.py`).
-   Agent refusals are tested with `VRA_AGENT=1` and `VRA_MCP=1` per human-only action
    (`engine/tests/test_mcp_and_human_only.py`).
-   Name tests after the behaviour they pin, not after a change or ticket. Mark a test that pins one past
    edit with `@pytest.mark.temporary` so it is removed later.
-   Live checks against AWS are not part of the package check; run them on a sandbox account with the
    restore drill and a full watch cycle before a release that changes AWS calls.

## Versioning and release

The tool ships inside VAMS at `tools/VamsReleaseAutomation` and is versioned independently of VAMS. Its
version is in three files that move together: `project.version` in `pyproject.toml`, `__version__` in
`engine/vra/__init__.py` and `version` in `cicd/pipeline/package.json` (and its lockfile); a test fails when
they differ. Change it in the same pull request as the change it versions.

-   Record user-visible changes in the VAMS root `CHANGELOG.md` and
    `documentation/docusaurus-site/docs/additional/revisions.md` under the target VAMS release, with the
    `**Release Automation**` component prefix.
-   A change to a ledger key, a history or run-state field, an exit code or a human-only action is
    breaking for operators: describe the migration in the changelog entry and, when state moves, give the
    command that moves it.
-   Pin exact versions for runtime dependencies in `pyproject.toml` and keep npm dependencies of the CI
    pipeline at least seven days old (`cicd/pipeline/.npmrc`).
-   Forks receive the tool with each VAMS release: the default path policy resolves
    `tools/VamsReleaseAutomation/**` as `upstream`. A fork that pins the engine for gated actions sets
    `vra.yaml` `engineVersion` and changes it deliberately.

## Related pages

-   [Developer guide: architecture](developer-architecture.md)
-   [Developer guide: extending](developer-extending.md)
