# VAMS Release Automation

VAMS Release Automation keeps a customer fork of Visual Asset Management System (VAMS) current with
upstream releases and moves each change through the customer's environments with tests, backups,
approvals and rollback. It has three parts:

-   **The engine (`vra`)**, a Python command-line tool. Every step that changes state (merge, build,
    deploy, migrate, back up, restore, copy data, promote) is a deterministic, resumable command with
    a dry-run mode and stable exit codes. Approval gates, separation of duties and human-only actions
    are enforced in the engine.
-   **The MCP server (`vra mcp`)**, the engine as tools for AI agents, with read, write and
    approval-gated tiers.
-   **The front-ends**: the agent skill with adapters for Codex, Claude Code, Kiro and Cursor, and CI/CD
    templates: an AWS CodePipeline app that `vra pipeline` renders from the ledger and deploys, GitHub
    Actions samples for GitHub Enterprise Cloud and Server, GitLab CI and generic CI. They drive the
    engine and never run a deploy command themselves. `automation.mode` selects the tier: `agent`,
    `hybrid`, `pipeline` or `pipeline+actions`.

For accounts where IT creates or bounds every IAM role, `vra iam plan` writes the complete list of roles
and policies VAMS needs, a person wires IT's answer into the configuration with `vra iam apply`, and every
build stops while the roles differ from what IT provided. The package version is independent of VAMS
(`pyproject.toml`, `engine/vra/__init__.py`, `cicd/pipeline/package.json`).

Account ids, Regions, domains, approvers, configuration and state live in a **shared home** folder that
the customer owns and synchronizes between machines, never in this directory.

## Documentation

The user and developer guides are part of the VAMS documentation site:

-   [Release automation](../../documentation/docusaurus-site/docs/deployment/release-automation/index.md):
    concepts, setup, the shared home, each workflow, agent systems, automation tiers, AWS CodePipeline,
    GitHub Actions, IAM for strict environments, configuration and command reference, troubleshooting,
    and the developer guide
-   [Productionizing VAMS](../../documentation/docusaurus-site/docs/deployment/productionizing/index.md):
    backups, replication, disaster recovery, IAM governance and environment strategy for any VAMS
    deployment

## Quick start

```bash
cd tools/VamsReleaseAutomation
pip install -e '.[mcp]'         # Python 3.12 or later; [mcp] adds the MCP server
vra setup                       # create the shared home, or: vra setup --join <path-or-git-url>
vra status --all
```

## Development

```bash
pip install -e '.[dev]'
make check                      # offline self-check: tests, parse, ledger validate, docs, agent policy, links, neutrality
```

The component development guide is [`CLAUDE.md`](CLAUDE.md). [`FILES.md`](FILES.md) lists every file
in this directory with its purpose.
