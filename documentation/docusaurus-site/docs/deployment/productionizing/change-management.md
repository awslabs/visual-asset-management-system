---
title: "Change management"
sidebar_position: 12
description: "A change process for VAMS production: one tested artifact promoted through environments, a reviewed preview of each change, a checkpoint before it, and a rollback plan."
---

# Change management

This page describes a change process for VAMS production: one tested artifact promoted through
environments, a reviewed preview of each change, a checkpoint before every change, and a planned rollback.

## Principles

-   **Build once, promote the same artifact.** VAMS reads its configuration at synth time, so synthesize the
    cloud assembly for every environment from one commit in one build, and deploy those assemblies without
    rebuilding. Production then runs exactly the code and dependencies that were tested.
-   **Keep configuration in version control.** Store each environment's `infra/config/config.json` and custom
    configuration in a private repository, reviewed like code. Keep secret values in AWS Secrets Manager.
-   **Separate the person who changes from the person who approves.** Require an approval from a named
    approver who did not initiate the change before deploying to test or production.
-   **Deploy only through automation.** Give people read-only access to production and route every change
    through the deployment role, so the change record is complete.

## Before every change

| Step                                                                                                                            | Purpose                                                                                         |
| ------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Read the release notes and [Update the solution](../../deployment/update-the-solution.md) for every version in the upgrade path | Find migrations, configuration changes and A/B requirements                                     |
| Compare your configuration with the new configuration template                                                                  | Pick up new options and their defaults; remove options that no longer exist                     |
| Run `npx cdk diff` and review it                                                                                                | Catch replacements of stateful resources (tables, buckets, user pools, keys, search domains)    |
| Run the version's data migration in dry-run mode                                                                                | Confirm what the migration will change                                                          |
| Take a checkpoint                                                                                                               | On-demand backups of every table and a record of S3 object versions (see [Backups](backups.md)) |
| Confirm the rollback plan                                                                                                       | Know which parts of the change can be undone by redeploying and which need a restore            |

## Rollback

| Change                       | Rollback                                                                                                                                         |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Code or configuration only   | Redeploy the previous artifact                                                                                                                   |
| Data migration ran           | Restore the checkpoint, then redeploy the previous artifact                                                                                      |
| Amazon Cognito schema change | Not reversible in place: custom attributes cannot be changed or removed once created. Plan these changes in advance and prefer an A/B deployment |
| Resource replacement         | Restore data from the checkpoint into the replacement or prefer an A/B deployment                                                                |

An AWS CloudFormation update that fails rolls back on its own. A rollback that itself fails leaves the stack in
`UPDATE_ROLLBACK_FAILED`; resolve the cause and continue the rollback before attempting another deployment.

## In-place or A/B

Most updates deploy in place. Use an A/B deployment, where a second stack is deployed with a different
`baseStackName`, loaded with data and verified before DNS moves, when the update guide calls for it, when a
change would replace a stateful resource, or when you switch the web distribution between Amazon CloudFront
and an Application Load Balancer. See [Update the solution](../../deployment/update-the-solution.md).

## Change windows and emergencies

Schedule production changes in an agreed window, with the approver and an operator available until
verification completes. For an emergency fix, shorten the review but keep the artifact, the checkpoint and the
approval: deploy the fix through the same pipeline, to a lower environment first if at all possible.

## Contributing fixes upstream

Generic fixes made in a fork are best contributed back to the VAMS project, so they are not carried as local
patches through every future update. Follow the project's contributing guidelines, and report security issues
privately as its security policy describes.

## Related pages

-   [Release automation](../release-automation/index.md)
-   [Environment strategy](environment-strategy.md)
-   [Backups](backups.md)
