---
title: "Automation tiers"
sidebar_position: 16
description: "Choose how much of the release lifecycle runs unattended: agent, hybrid, pipeline or pipeline+actions, what runs where in each, and how to move between them."
---

# Automation tiers

Release automation runs the same engine, gates and ledger whether a person types `vra` commands, an AI
agent calls the MCP server, AWS CodePipeline runs the release, or GitHub Actions checks every branch.
`automation.mode` in `vra.yaml` records which of these drives each part of the lifecycle. The tiers form
a ladder; you can start at the bottom and move up without changing environments, approvals or history.

| `automation.mode`  | Who drives                                                                                                      | Typical organization                                 |
| ------------------ | --------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `agent` (default)  | An operator or an AI agent runs `vra` on a workstation or a scheduled host (cron through the agent's CLI)       | No CI/CD for VAMS yet                                |
| `hybrid`           | CI runs the watch, the build and the ungated dev environments; people or agents promote to gated environments   | Some CI, releases still run by people                |
| `pipeline`         | CodePipeline end to end: source, scheduled sync, build, dev deploy and test, manual approvals, test, production | AWS-native continuous delivery                       |
| `pipeline+actions` | GitHub Actions for pull request checks, security scans and branch tests; CodePipeline for every deploy          | GitHub Enterprise organizations with AWS deployments |

The engine enforces every gate in every tier. A tier only decides which machine runs a command and who
presses the approval button.

## What runs where

| Activity                                | `agent`                                                         | `hybrid`                             | `pipeline`                                                         | `pipeline+actions`                                                       |
| --------------------------------------- | --------------------------------------------------------------- | ------------------------------------ | ------------------------------------------------------------------ | ------------------------------------------------------------------------ |
| Watch upstream, open sync pull requests | `vra watch` from cron (`vra-cron.sh`)                           | Scheduled CI job (`sync-merge`)      | Amazon EventBridge Scheduler and the `vra-watch` CodeBuild project | Same as `pipeline`, or `upstream-release.yml`                            |
| Unit suites, lint and scans of a branch | `vra build` (unit suites, ASH, SBOM)                            | CI of the fork                       | The Build stage                                                    | `pr-checks.yml` and `security-scan.yml` on every branch and pull request |
| Build the release bundle                | `vra build`                                                     | CI build job or the release pipeline | The Build stage                                                    | The Build stage                                                          |
| Deploy and test dev                     | `vra deploy`, `vra test` (or `vra watch` for `autoDeploy` envs) | Pipeline stages for the ungated envs | Pipeline stages                                                    | Pipeline stages, started by `hand-off-to-codepipeline.yml`               |
| Promote to gated envs                   | `vra promote` with an approval record                           | `vra promote` by a person or agent   | Manual approval actions, then the Promote actions                  | Manual approval actions                                                  |
| Human-only actions                      | A person at a terminal                                          | A person at a terminal               | A person at a terminal, or the operator templates                  | A person, or `vra-operator.yml`                                          |
| IT role packages                        | `vra iam plan`, a person runs `vra iam apply`                   | Same                                 | Same; blocked actions are retried after the apply                  | Same                                                                     |

## Choosing a tier

-   Start with **`agent`** when VAMS has no CI/CD yet or when one team runs a few environments. Everything
    runs from one shared home, and the agent stops at every human gate.
-   Use **`hybrid`** when you want dev to follow upstream unattended but keep releases to test and
    production in people's hands. The rendered pipeline has stages only for the environments reachable
    from a root without an approval (`scope: dev`; `vra ledger validate` refuses any other scope).
-   Use **`pipeline`** when deployments must run inside your AWS accounts on AWS CodeBuild, with
    CodePipeline manual approvals for each gated environment.
-   Use **`pipeline+actions`** when your developers work in GitHub Enterprise and want checks on every pull
    request, while deployments stay in AWS. The fork's push does not start the release pipeline; the
    hand-off workflow starts it for each trunk commit that passed both check workflows (and whose ASH
    scan has no unsuppressed findings at or above `vars.VRA_ASH_FAIL_ON`, default `high`).

Pick one owner for dev deploys. In the pipeline tiers the scheduled job runs `sync-merge`: it merges new
upstream releases and development commits and opens the sync pull request, but never deploys; merged trunk
commits start the release pipeline. `vra ledger validate` refuses
`watch.command: watch` in `automation.pipeline` and warns about an environment with `autoDeploy: true` that the pipeline deploys,
so a `vra watch` cron and the pipeline never both deploy dev.

## Configuring a tier

```yaml
# ledger/vra.yaml
automation:
    mode: pipeline
    approvalEnv: prod # approvers of prod authorize a human-only `vra pipeline deploy` without a terminal
    pipeline:
        prefix: vra
        toolsAccount: "111111111111"
        region: us-east-1
        sources:
            fork:
                type: codeconnections
                provider: GitHub
                repo: example-org/visual-asset-management-system
                branch: main
            vra:
                type: s3
                objectKey: vra/vams-release-automation.zip
        home:
            repoUrl: https://github.com/example-org/vams-release-home.git
            ref: main
            tokenSecretName: vra/github-home-token
        notifications:
            topicArn: arn:aws:sns:us-east-1:111111111111:vra-notifications
```

`automation.pipeline` is the configuration of the `cicd/pipeline` AWS CDK app without its topology:
`vra pipeline plan` renders the environments, accounts, approvals and required suites from
`vra ledger graph`, so the pipeline cannot disagree with the ledger. `pipeline+actions` usually also sets
`actionsHandoff` in the pipeline block (the role the GitHub hand-off assumes); the GitHub Actions side is
configured in its workflow files and repository variables. All keys are in the [configuration reference](configuration-reference.md#automation).

Then plan and deploy the pipeline into the tools account:

```bash
vra ledger validate
vra pipeline plan
vra pipeline deploy --plan <plan-id>
vra pipeline status
```

The first deploy, any IAM change and any removed approval are
[human-only](approvals-and-gates.md#human-only-actions) (`pipeline-deploy`): a person confirms at a
terminal, or an approver of `approvalEnv` writes a record with subject `human:pipeline-deploy`. The
engine compares the plan with the deployed stack again at deploy time. Agents may run `vra pipeline plan`,
`vra pipeline status` and `vra pipeline sync-ledger` (MCP tools `pipeline_plan`, `pipeline_status`,
`pipeline_sync_ledger`); every adapter asks a person before `vra pipeline deploy`, `vra pipeline
push-source` and `sync-ledger --deploy` (MCP `pipeline_deploy`, `pipeline_push_source`). After a topology
change in the ledger, `vra pipeline sync-ledger` plans again and exits `20` until the new plan is
deployed.

## Moving between tiers

-   **Up.** Add the `automation` block, set `autoDeploy: false` on the environments the pipeline deploys,
    remove the `vra watch` cron entry, then run `vra pipeline plan` and `vra pipeline deploy`. Runs,
    history, approvals and checkpoints stay where they are; the pipeline jobs use the same shared home.
-   **Down.** Set `automation.mode: agent` (a remaining `pipeline` block is ignored with a warning) and
    disable the pipeline's source triggers or delete the stack. Operators and agents continue with the
    same runs.

## Related pages

-   [CI/CD and AWS CodePipeline](codepipeline.md)
-   [GitHub Actions](github-actions.md)
-   [Agent systems](agent-systems.md)
-   [Workflows](workflows.md)
