# Update the solution

This page describes how to apply updates to an existing VAMS deployment, including standard in-place updates, A/B deployment strategies for major changes, and version-specific migration steps.

## Update methods

VAMS supports two update methods depending on the scope of changes being applied.

| Method              | Use case                                                                                                          | Downtime                                                 |
| ------------------- | ----------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| **In-place update** | Minor updates, bug fixes, feature additions within the same major version.                                        | Minimal (during AWS CloudFormation changeset execution). |
| **A/B deployment**  | Major version upgrades, KMS key changes, distribution type changes (Amazon CloudFront to ALB), region migrations. | Moderate (during data migration and DNS switchover).     |

## Pre-update checklist

Complete the following steps before applying any update.

:::danger[Always back up your data before updating]
VAMS uses `RemovalPolicy.RETAIN` on Amazon DynamoDB tables. An update that replaces a table therefore does **not** delete its data — AWS CloudFormation creates a new, empty table and orphans the old one, which keeps its original auto-generated name and continues to accrue storage charges. The application comes up with no data while the data still exists in the retained table, so the symptom looks like data loss even though nothing was deleted.

Back up before updating, and if an update replaces a table, locate the orphaned table (it is not listed in the updated stack's resources) and either migrate its contents into the new table or delete it once you no longer need it. See [Uninstall the solution](uninstall.md#step-3-delete-dynamodb-tables) for how VAMS tables are named and listed.
:::

1. **Review the changelog.** Read the [CHANGELOG.md](https://github.com/awslabs/visual-asset-management-system/blob/main/CHANGELOG.md) for breaking changes, required migration scripts, and known issues for the target version.

2. **Back up DynamoDB tables.** Export critical tables using AWS Backup or on-demand exports:

    ```bash
    # Export a table to Amazon S3 using point-in-time export
    aws dynamodb export-table-to-point-in-time \
        --table-arn arn:aws:dynamodb:<REGION>:<ACCOUNT_ID>:table/<TABLE_NAME> \
        --s3-bucket <BACKUP_BUCKET> \
        --s3-prefix vams-backup/$(date +%Y%m%d) \
        --export-format DYNAMODB_JSON
    ```

3. **Back up S3 buckets.** Sync asset buckets to a backup location:

    ```bash
    aws s3 sync s3://<ASSET_BUCKET> s3://<BACKUP_BUCKET>/asset-backup/ \
        --profile <PROFILE>
    ```

4. **Record current stack outputs.** Save existing CloudFormation outputs for reference and potential rollback:

    ```bash
    aws cloudformation describe-stacks \
        --stack-name <VAMS_STACK_NAME> \
        --query 'Stacks[0].Outputs' \
        --output json > stack-outputs-backup.json
    ```

5. **Test in a non-production environment.** Deploy the update to a test stack first and validate functionality before applying to production.

## In-place update

Use this method for minor updates and configuration changes within the same major version.

### Step 1: Pull the latest code

```bash
git fetch --all --tags
git checkout tags/<TARGET_VERSION>
```

### Step 2: Install dependencies

```bash
cd web && npm install && npm run build && cd ..
cd infra && npm install && cd ..
```

### Step 3: Update configuration

Review and update `infra/config/config.json` with any new configuration fields introduced in the target version. New configuration fields are typically backward-compatible and receive defaults, but review the changelog for required changes.

### Step 4: Deploy the update

```bash
cd infra
npx cdk deploy --all --require-approval never
```

AWS CDK creates a CloudFormation changeset and applies only the modified resources. Resources that have not changed are left untouched.

:::info[Changeset behavior]
CloudFormation changesets update, replace, or delete resources based on the type of change. Property updates that require replacement (such as changing a DynamoDB table's partition key) result in the old resource being deleted and a new one created. Review the `cdk diff` output before deploying to understand which resources will be affected.

```bash
npx cdk diff
```

:::

:::warning[Default roles and constraints are overwritten on every deployment]
The seeded authorization defaults are an exception to the "unchanged resources are left untouched" rule
above. Every deployment — in-place or A/B — writes them back to Amazon DynamoDB, replacing each record in
full and re-creating any that were deleted. This is intentional: read-only role constraints are added and
adjusted between releases, and re-seeding is how an existing deployment receives those changes without a
migration step.

The records rewritten on every deployment are:

-   The `admin` and `basicReadOnly` role definitions, including their `mfaRequired` setting.
-   The `admin` role assignments for `app.adminUserId` and the reserved `SYSTEM_USER` identity.
-   Every default constraint — those with an id beginning `initial_admin_` or `initial_basicro_`.
-   The user records for `app.adminUserId` (with `app.adminEmailAddress`) and `SYSTEM_USER`.

Because each record is replaced in full, **any hand-edit to a default role, one of its constraints, or a
seeded user record is lost on the next deployment**, including attributes added to it and an
`mfaRequired` value changed through the web interface. Customize permissions in a separate role carrying
its own constraints rather than by editing a default one; roles, user-role assignments, and constraints
created through the web interface, the API, or the CLI are not touched by a deployment. See
[Permissions model](../concepts/permissions-model.md) for the constraint structure.
:::

:::danger[Do not change `app.adminUserId` or `app.adminEmailAddress` after the first deployment]
The seeded Amazon Cognito administrator is an `AWS::Cognito::UserPoolUser` whose `Username`,
`UserPoolId`, `UserAttributes` and `DesiredDeliveryMediums` are all **Update-requires-Replacement**.
Editing either value therefore does not rename the account — AWS CloudFormation creates a new user and
deletes the old one. Which of two outcomes you get depends only on whether the new username already
exists in the pool:

- **The new username does not exist.** The deployment **succeeds** and the previous administrator
  identity is **deleted**. You can no longer sign in as it, and any `userRoles` rows still keyed to the
  old username grant nothing to nobody. This half is silent — nothing in the deployment output says an
  administrator was removed.
- **The new username already exists** (created by hand, or equal to another operator's account). The
  replacement's create step fails with `AlreadyExists`, the nested authorization stack fails, and the
  **whole core stack rolls back** — roughly 15 minutes, across every nested stack.

Treat both values as fixed for the life of the deployment. To change who administers VAMS, leave the
seeded account alone and grant the `admin` role to another user through the web interface, the API, or
the CLI — those assignments are not overwritten by a deployment.

If the values in your configuration file have already drifted from the deployed stack (for example a
configuration copied between environments), read the deployed value back before deploying and restore
it:

```bash
aws cognito-idp list-users --user-pool-id <pool-id> \
  --query "Users[].Username" --output text
```

This behaviour is not specific to any one release; it has always been how the resource is declared.
:::

:::warning[Built-in pipelines and workflows are re-registered from the CDK schema]
Each built-in pipeline ships a `vamsSchema` bundle — a pipeline definition, an optional workflow with its
triggers, and its templates — that the deployment uploads and registers through an AWS CloudFormation
custom resource. A hash of the bundle's files and of the deploy-time values injected into it is a property of that
resource, so the registration re-runs whenever a release revises the built-in or the resources it points
at change. The definitions are owned by the schema, not by the deployment's database, and a re-registration
replaces them.

A re-registration rewrites, for the ids the bundle names:

-   **The pipeline** — name, category, description, execution configuration and `systemConfig`, and it is
    written back as **enabled and unarchived**.
-   **The workflow** — name, category, description, referenced pipelines, sub-dashboard URL and
    `systemConfig`, also **enabled and unarchived**. Its AWS Step Functions state machine is regenerated.
-   **Each template the bundle ships** — name, description, configuration format and body, web form,
    custom-edit flag, input instructions, `systemConfig` overrides, the `isDefault` flag, and the tag
    schema when the bundle declares one. A shipped template that is the bundle's default reclaims that
    designation from whichever template held it.
-   **Each trigger the bundle declares**, matched by trigger type — its input-file filters, default
    templates, and `enabled` flag, which comes from `autoRegisterAutoTriggerOnFileUpload` when the
    deployment sets it.

A re-registration does **not** touch execution history or the outputs of past runs, pipelines and workflows
you created, templates and additional triggers you added under identifiers of your own, or the
`dateCreated` and `createdBy` provenance of the built-in's records; the rewritten records are attributed to
`SYSTEM_USER` as their modifier.

The consequence to plan around is that **a built-in disabled or archived in the web interface returns
enabled**, and that no line in the deployment output flags a replaced change — the custom resource reports
only what it registered. Turn a built-in off through the deployment configuration instead: setting its
`autoRegisterWithVAMS` to `false` removes the registration and archives the pipeline and workflow, and
`autoRegisterAutoTriggerOnFileUpload` controls whether its file-upload trigger fires. See
[Pipelines and workflows](../concepts/pipelines-and-workflows.md#global-pipelines-versus-database-specific-pipelines).
:::

### Step 5: Post-update verification

1. Confirm the stack deployed successfully in the AWS CloudFormation console.
2. Navigate to the VAMS web interface and verify login and basic operations.
3. Check Amazon CloudWatch Logs for Lambda function errors.
4. If Amazon OpenSearch Service is enabled and the update requires reindexing, set `app.openSearch.reindexOnCdkDeploy` to `true` in `config.json` and redeploy, or run the reindex utility manually.

## A/B deployment

Use A/B deployment when the update involves changes that cannot be safely applied through a CloudFormation changeset. This method deploys a parallel VAMS stack, migrates data, and then decommissions the original stack.

### When to use A/B deployment

-   Major version upgrades with breaking DynamoDB schema changes.
-   Changing the KMS CMK encryption key.
-   Switching distribution type between Amazon CloudFront and Application Load Balancer (ALB).
-   Migrating the deployment to a different AWS Region within the same account.

### A/B deployment steps

1. **Deploy Stack B.** Use a different `baseStackName` in your `infra/config/config.json` for the new deployment.

2. **Redirect traffic.** Update DNS records to point to a temporary maintenance page.

3. **Deploy the new stack.**

    ```bash
    cd infra
    npx cdk deploy --all --require-approval never
    ```

4. **Migrate DynamoDB data.** Use the A/B migration scripts provided in `infra/deploymentDataMigration/`:

    ```bash
    cd infra/deploymentDataMigration
    pip install boto3
    python tools/VAMSDataMigration.py config/<YOUR_MIGRATION_CONFIG>.json
    ```

5. **Migrate S3 data.** Sync asset buckets from Stack A to Stack B:

    ```bash
    aws s3 sync s3://<STACK_A_ASSET_BUCKET> s3://<STACK_B_ASSET_BUCKET>
    aws s3 sync s3://<STACK_A_AUXILIARY_BUCKET> s3://<STACK_B_AUXILIARY_BUCKET>
    ```

6. **Migrate users.** If using Amazon Cognito, manually recreate users in the new user pool. Password resets may be required.

7. **Validate Stack B.** Test all VAMS functionality with the migrated data.

8. **Switch DNS.** Update DNS records to point to Stack B endpoints.

9. **Decommission Stack A.** After confirming Stack B is stable, destroy Stack A following the [uninstall procedure](uninstall.md).

:::warning[ALB deployment consideration]
When using the ALB configuration, the web application S3 bucket is named after the domain. This creates a naming conflict during A/B deployment. You must delete the web app bucket from Stack A before deploying Stack B with the same domain, then restore the bucket contents after deployment.
:::

## Version-specific migration instructions

Each major version upgrade may require data migration scripts to transform DynamoDB schemas or reindex Amazon OpenSearch Service. The following sections document required migrations for each version path.

### v2.2 to v2.3

**Breaking changes:**

-   API Gateway authorizers replaced with custom Lambda authorizers.
-   AWS Batch Fargate CDK construct naming changed for pipeline stacks.
-   Amazon OpenSearch Service indexes replaced with new dual-index schema (assets and files).

**Required migration steps:**

1. Deploy the v2.3 CDK stack.
2. Run the OpenSearch reindex script to populate the new indexes:

    ```bash
    cd infra/deploymentDataMigration/v2.2_to_v2.3/upgrade
    ```

3. Optionally disable and re-enable batch pipelines if experiencing CDK deployment errors with Amazon Elastic Container Service (Amazon ECS) Fargate constructs.

:::note
If Lambda functions behind a VPC were broken in v2.2, this version restores VPC support. However, MFA for roles is not supported when all Lambda functions are behind a VPC with Amazon Cognito enabled.
:::

### v2.3 to v2.4

**Breaking changes:**

-   Permission constraints migrated to a dedicated DynamoDB table (no longer shared with auth entities).
-   Metadata and metadata schema DynamoDB tables replaced with new tables supporting multi-entity types.
-   Amazon OpenSearch Service index schemas changed for `MD_` and `AB_` fields (now flat objects).

**Required migration steps:**

1.  Deploy the v2.4 CDK stack. Default admin and read-only constraints are re-created automatically.

2.  Navigate to the migration scripts directory:

    ```bash
    cd infra/deploymentDataMigration/v2.3_to_v2.4/upgrade
    ```

3.  Copy and configure the migration configuration file:

    ```bash
    cp v2.3_to_v2.4_migration_config.json my_migration_config.json
    ```

4.  Update the configuration file with your DynamoDB table names. Retrieve table names from CloudFormation outputs:

    ```bash
    aws cloudformation describe-stacks --stack-name <VAMS_STACK_NAME> \
        --query 'Stacks[0].Outputs[?contains(OutputKey, `Table`)].{Key:OutputKey,Value:OutputValue}' \
        --output table
    ```

5.  Run the migration:

    **Linux / macOS**

    ```bash
    chmod +x run_migration.sh
    ./run_migration.sh my_migration_config.json
    ```

    **Windows**

    ```powershell
    .\run_migration.ps1 my_migration_config.json
    ```

6.  The migration performs the following operations:
    -   Migrates metadata from the old table to the new multi-entity metadata tables.
    -   Migrates metadata schemas to the new schema table with support for multiple entity types.
    -   Migrates permission constraints from the auth entities table to the dedicated constraints table.
    -   Reindexes Amazon OpenSearch Service with the new field schemas.

### v2.4 to v2.5

**Breaking changes:**

-   Asset version DynamoDB tables restructured with `databaseId`-prefixed composite keys to prevent cross-database collisions.
-   Website overhauled with Vite build framework, AWS Amplify v6, and dark/light theme support (may cause merge conflicts for forked repositories).

**Required migration steps:**

1.  Deploy the v2.5 CDK stack. The new V2 tables are created alongside the existing V1 tables.

2.  Navigate to the migration scripts directory:

    ```bash
    cd infra/deploymentDataMigration/v2.4_to_v2.5/upgrade
    ```

3.  Copy and configure the migration configuration file:

    ```bash
    cp v2.4_to_v2.5_migration_config.json my_migration_config.json
    ```

4.  Update the configuration file with your DynamoDB table names. The migration requires these tables:

    | Table                                   | Purpose                                               |
    | --------------------------------------- | ----------------------------------------------------- |
    | `AssetStorageTable`                     | Lookup source for `assetId` to `databaseId` mapping.  |
    | `AssetVersionsStorageTable`             | V1 source for asset versions.                         |
    | `AssetVersionsStorageTableV2`           | V2 destination for asset versions.                    |
    | `AssetFileVersionsStorageTable`         | V1 source for asset file versions.                    |
    | `AssetFileVersionsStorageTableV2`       | V2 destination for asset file versions.               |
    | `AssetFileMetadataVersionsStorageTable` | In-place backfill for new `databaseId:assetId` field. |

5.  Run the migration:

    **Linux / macOS**

    ```bash
    chmod +x run_migration.sh
    ./run_migration.sh my_migration_config.json
    ```

    **Windows**

    ```powershell
    .\run_migration.ps1 my_migration_config.json
    ```

6.  The migration performs five phases:
    -   **Phase 1:** Builds a lookup cache by scanning the asset storage table for `assetId` to `databaseId` mappings.
    -   **Phase 2:** Migrates asset versions from V1 to V2 with transformed key schema (`assetId` becomes `databaseId:assetId`).
    -   **Phase 3:** Migrates asset file versions from V1 to V2 with transformed key schema.
    -   **Phase 4:** Backfills the `databaseId:assetId` field on existing asset file metadata version records for the new Global Secondary Index (GSI).
    -   **Phase 5:** Verifies record counts and key structure integrity between V1 and V2 tables.

:::tip[IAM permissions for migration]
The migration requires `dynamodb:Scan` on source tables, `dynamodb:BatchWriteItem` on V2 destination tables, and `dynamodb:UpdateItem` on the metadata versions table. See the [v2.4 to v2.5 migration README](https://github.com/awslabs/visual-asset-management-system/blob/main/infra/deploymentDataMigration/v2.4_to_v2.5/upgrade/v2.4_to_v2.5_migration_README.md) for the full IAM policy.
:::

### v2.5 to v2.6

:::info[Custom pipelines need code changes as well]
The migration steps below move stored pipeline and workflow **definitions** onto the new data model. They
cannot update the **code** of a pipeline you wrote yourself: v2.6 delivers inputs through a manifest
rather than on the payload, expects asynchronous pipelines to return a task token, and expects a pipeline
to register its sub-processes and logs so aborts and log retrieval work. See
[Migrating custom pipelines from v2.5 to v2.6](../pipelines/migrating-pipelines-v25-to-v26.md) for the
porting order and checklist. Deployments that run only VAMS built-in pipelines need nothing beyond the
steps here.
:::

**Breaking changes:**

-   The backend API moves from API Gateway HTTP API (v2) to REST API (v1), served under a stage path (default `/api`). The API Gateway identifier and invoke URL change on deployment. **Any client registered directly against the old API Gateway endpoint URL must be re-setup against the new endpoint** — re-run `vamscli setup` for the CLI, and update any external integrations or scripts that stored the API base URL. Clients that reach the API through the CloudFront or ALB front (the web application, and CLIs configured with the front's `/api` URL) continue to work without change. See [API Gateway REST API endpoint change](#api-gateway-rest-api-endpoint-change).
-   **Externally registered pipelines do not run unchanged.** The workflow, pipeline, and execution overhaul changes three things a pipeline depends on: it reads its inputs from a resolved manifest rather than from the invocation payload, an asynchronous pipeline returns a Step Functions task token for the workflow to advance past it, and it registers its sub-processes and log locations so abort and log retrieval reach them. Registration itself also moves — a definition is declared in a file-based `vamsSchema` bundle imported through the schema importer, and a pipeline is referenced by composite `pipelineDatabaseId:pipelineId`. The migration steps below reshape stored **definitions**; they cannot change a pipeline's **code**. Port every externally maintained pipeline with [Migrating custom pipelines from v2.5 to v2.6](../pipelines/migrating-pipelines-v25-to-v26.md). Deployments running only VAMS built-in pipelines need nothing beyond the steps here.
    -   **Three API routes are removed, and any direct API client must be repointed.** `PUT /pipelines` (create a pipeline) is replaced by `POST /database/{databaseId}/pipelines`; `PUT /workflows` (create a workflow) by `POST /database/{databaseId}/workflows`; and `POST /database/{databaseId}/assets/{assetId}/workflows/{workflowId}` (run a workflow against one asset) by `POST /workflows/{workflowDatabaseId}/{workflowId}/execute`, which is asset-less and takes an input-file array plus an output-target asset instead of a path-bound asset. The bare `/pipelines` and `/workflows` paths serve `GET` only. A removed route is absent from the API's OpenAPI spec, so a call to it is rejected by the authorizer with a **`403`** rather than a `404` — this affects scripts, CI jobs, and home-built clients even when the deployment runs no custom pipeline code.
-   **Role constraints that reference the `pipeline` criteria field `pipelineType` are no longer enforced as written.** `pipelineType` is not part of the `pipeline` constraint field set in v2.6, and a criterion naming a field VAMS does not recognize is dropped when the permission policy is compiled. For an already-stored constraint the drop is silent: stored constraints are not re-validated, so no error and no warning reaches the administrator. Because a constraint's criteria are combined with AND, dropping the criterion from an **allow** rule removes a restriction and **widens** the pipelines the role can reach. Every stored constraint must be audited and re-authored against `category` before upgrading; no migration step rewrites constraints. See [Permission constraint audit for `pipelineType`](#permission-constraint-audit-for-pipelinetype).
-   `app.pipelines.usePreviewPcPotreeViewer.sqsAutoRunOnAssetModified` is removed, along with the identically named key under `app.pipelines.useSplatToolbox`. Configuration validation ignores an unrecognized key rather than rejecting it, so a stale entry left in `config.json` raises no error at synth or deploy, and the automatic re-run of the Potree point cloud conversion on asset modification simply stops. There is no replacement under the v2.6 trigger model: `fileUpload` is the only trigger type, so a workflow fires when a matching file is uploaded and not when an existing asset is edited.
-   New OpenSearch index names: `vams-assets-v3` and `vams-files-v3`. The new mapping adds a `geo_MD_location` field of type `geo_shape` that powers the new geospatial search filter and map view. The previous v2 indexes are abandoned and remain in OpenSearch until you delete them manually.
-   Provisioned OpenSearch domains are upgraded from engine version 2.7 to 3.5. Serverless collections are reworked separately (see below).
-   OpenSearch Serverless collections are reshaped onto a next-generation collection group with new `app.openSearch.useServerless` settings (`nextGen`, `allowPublic`, `enableStandbyReplicas`, and configurable OCU capacity). The collection cannot be updated in place — it must be removed and re-created, then reindexed. See [OpenSearch Serverless next-gen upgrade](#opensearch-serverless-next-gen-upgrade).
-   **`app.openSearch.useServerless.allowPublic` is new and defaults to `true`, which fails configuration validation on a fully VPC-isolated deployment.** A `config.json` carrying no `allowPublic` key resolves to a public collection, and a deployment with both `app.useGlobalVpc.enabled` and `app.useGlobalVpc.useForAllLambdas` set to `true` then throws at `cdk synth`, because an all-Lambdas-in-VPC deployment cannot reach a public collection. Set `allowPublic` to `false` — that is the setting which reproduces the v2.5 private-collection behavior for this topology, where the collection was placed behind a VPC endpoint automatically.
-   The VPC is no longer enabled automatically. If a feature that requires a VPC (ALB, OpenSearch Provisioned, or any container-based pipeline) is enabled while `app.useGlobalVpc.enabled` is `false`, the deployment now fails configuration validation with an error that lists the offending features, rather than silently turning the VPC on. See [VPC is now required for certain features](#vpc-is-now-required-for-certain-features).
-   Provisioned OpenSearch `availabilityZoneCount` now defaults to `2`, and the VPC is built with exactly that many Availability Zones. Earlier releases always built the VPC across 3 Availability Zones for provisioned OpenSearch even though the domain only used 2, so on upgrade the previously-unused third AZ subnet is removed (a VPC downgrade). See [OpenSearch Provisioned Availability Zone count downgrade](#opensearch-provisioned-availability-zone-count-downgrade).
-   **AWS WAF changes from count-only monitoring to enforcement on a deployment with `app.useWaf` enabled.** v2.5 ran the AWS Common Rule Set in `count` mode, recording matches without rejecting them. The three rule groups now declared in `infra/config/policy/wafPolicyConfig.json` — Common Rule Set, Known Bad Inputs, and Amazon IP Reputation List — are in block mode. A request that previously only incremented a counter is answered **`403` by AWS WAF before it reaches the authorizer or any Lambda function**, so it produces no VAMS log entry to correlate with the upgrade. Two Common Rule Set rules are already overridden back to `count` because VAMS traffic trips them: `SizeRestrictions_BODY` for multi-part upload bodies, and `SizeRestrictions_QUERYSTRING` for the presigned URL the SuperSplat viewer passes in its `?load=` parameter. Review the AWS WAF blocked-request metrics after upgrading; set `"block": false` on a group in that file to return it to monitor mode, or remove the file to restore count-only behavior.
-   GPU pipeline AWS Batch compute environments move to the Amazon Linux 2023 NVIDIA-accelerated AMI (`ECS_AL2023_NVIDIA`). AWS Batch blocks creation of new Amazon ECS compute environments that use Batch-provided Amazon Linux 2 AMIs, so earlier image types fail on a new deployment. This affects the Gaussian Splat Toolbox, NVIDIA Cosmos (Predict, Reason, Transfer), Cosmos 3, GR00T, and Isaac Lab pipelines. **Each affected GPU compute environment is replaced on upgrade**, so drain or wait for in-flight GPU pipeline jobs before deploying. All supported GPU instance families (G5, G6, G6E, P4DE, P5, P5E) work with this AMI; the `P3` and `G3` families are not supported by it.

**Required migration steps:**

1.  Deploy the v2.6 CDK stack. The schema-deploy custom resource creates the empty v3 indexes; the v2 indexes are left in place but unreferenced.

2.  Navigate to the migration scripts directory:

    ```bash
    cd infra/deploymentDataMigration/v2.5_to_v2.6/upgrade
    ```

3.  Copy and configure the migration configuration file:

    ```bash
    cp v2.5_to_v2.6_migration_config.json my_migration_config.json
    ```

4.  Set `resource_names_ssm_param_prefix` in the config to the value of the CloudFormation output `ResourceNamesSSMParamPrefixOutput` from your stack, and set `aws_region` (and `aws_profile` if needed):

    ```bash
    aws cloudformation describe-stacks --stack-name your-vams-stack \
      --query 'Stacks[0].Outputs[?OutputKey==`ResourceNamesSSMParamPrefixOutput`].OutputValue' \
      --output text
    ```

    The reindexer Lambda function name is then resolved automatically from the deployment's SSM Parameter Store resource-name parameters (requires `ssm:GetParametersByPath` on the prefix). To skip or override the lookup, set `reindexer_function_name` explicitly to the value of the CloudFormation output `ReindexerFunctionNameOutput` instead.

5.  Do a dry run first. Each step reports the rows it would write without writing them.

    **Linux / macOS**

    ```bash
    chmod +x run_migration.sh
    ./run_migration.sh my_migration_config.json --dry-run
    ```

    **Windows**

    ```powershell
    .\run_migration.ps1 -ConfigFile my_migration_config.json -DryRun
    ```

    A dry run reports zero rows for any step whose source rows are absent or already migrated, so a zero-row result on its own is not evidence that the step is configured correctly. Read the per-step counts against what the deployment actually holds.

6.  Run the migration:

    **Linux / macOS**

    ```bash
    ./run_migration.sh my_migration_config.json
    ```

    **Windows**

    ```powershell
    .\run_migration.ps1 -ConfigFile my_migration_config.json
    ```

7.  The migration runs seven independent steps in the order below. `--steps` selects a single step; the default (`all`) runs every one.

    | Step                            | `--steps` value               | What it does                                                                                                                                                                                                        |
    | ------------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
    | OpenSearch reindex              | `reindex`                     | Invokes the deployed reindexer Lambda, which re-publishes every asset record so the asset indexer writes into `vams-assets-v3` (including the new `geo_MD_location` field), lists every asset bucket and re-publishes file events into `vams-files-v3`, and returns aggregate success/failure counts. |
    | Asset history backfill          | `assetHistory`                | Backfills the new asset history table from existing asset and version records — a `create` record from each asset's v0 version, plus `archive`/`unarchive` records inferred from the asset's archive fields.        |
    | Workflow executions overhaul    | `workflowExecutions`          | Reshapes legacy workflow execution rows into the V2 workflow-keyed tables (main record, workflow inputs, per-pipeline execution records, and input files). The V1 table is never modified.                          |
    | Auxiliary preview relocation    | `auxPreviewRelocation`        | Moves auxiliary-bucket preview and viewer objects to the database-scoped per-file layout by copying each object to its new key and then deleting the old one. Previews are unavailable between the deploy and this step. |
    | Pipeline + workflow definitions | `pipelineWorkflowDefinitions` | Migrates user-database pipeline and workflow definitions from the V1 tables to the V2 tables, preserving each pipeline's parameters as a `migrated-default` template. Shipped `GLOBAL` built-ins are skipped.        |
    | Global-list partition backfill  | `globalListBackfill`          | Stamps the `allListPartition` attribute on V2 pipeline, workflow, and execution rows that predate it, so the cross-database "all pipelines / workflows / executions" lists return them.                             |
    | Tags namespacing                | `tagsNamespacing`             | Copies every legacy tag and tag type into the V2 composite-key tables under the `GLOBAL` partition. Asset tag lists are unchanged.                                                                                  |

:::danger[Run the pipeline and workflow definition step once per upgrade]
`pipelineWorkflowDefinitions` is the one step that is not safe to repeat. Its V2 rows are keyed by the same `(databaseId, pipelineId/workflowId)` as the V1 source, and the overwrite is **unconditional**: a second run replaces each migrated pipeline, workflow, and `migrated-default` template with the V1-derived record, discarding every edit made since the first run — renames, archive flags, and template bodies included. Nothing reports the loss.

Run it once as part of the upgrade. If a later run is needed to pick up definitions added afterwards, restrict it to the specific rows you intend to reset, and expect to re-apply any edits to rows it touches. The other six steps are safe to repeat: `reindex` rewrites documents from the live source records, `assetHistory` and `workflowExecutions` use deterministic record IDs and overwrite with the same values, `auxPreviewRelocation` skips objects already in the new layout, and `globalListBackfill` and `tagsNamespacing` write under a condition expression that skips rows already present.
:::

:::note[`--clear-indexes` defaults to false]
The v3 indexes are empty after the v2.6 CDK deploy, so the first migration run never needs to clear them. Pass `--clear-indexes` only if a previous run partially populated v3 and you want to start clean.
:::

:::warning[The reindex is what backfills the record-type discriminator — do not skip it]
v2.6 renames the OpenSearch record-type discriminator to `str_rectype` and sets it on every document
write. Because it is set on WRITE, a document indexed before the upgrade does not acquire it just by
deploying: `app.openSearch.reindexOnCdkDeploy` is `false` by default and a deployment does not replay
indexing. Any search that filters on the discriminator therefore returns nothing for pre-upgrade
content until the reindex has run.

The `reindex` step above is the backfill. It repopulates both v3 indexes from DynamoDB and Amazon S3
through the current indexers, so every live asset and file document is rewritten with the field. No
separate command is needed — it is part of the default run, and can be run alone:

```bash
python v2.5_to_v2.6_migration.py --config my_migration_config.json --steps reindex
```

Two things to expect afterwards:

- **The first search immediately after a large reindex can return a 500.** OpenSearch Serverless is
  still settling; retry after about 30 seconds. It is not a failed migration.
- **Documents whose source no longer exists are not rewritten.** A reindex repopulates from live
  DynamoDB and S3 records, so a stale document left by an asset or file deleted earlier keeps its
  pre-upgrade shape indefinitely. Those documents are unreachable through any live-entity read. To
  clear them out as well, re-run the step with `--clear-indexes`, which empties v3 before
  repopulating.
:::

:::note[Tag namespacing migration]
v2.6 adds per-database tag namespacing, backed by the new composite-key `TagStorageTableV2` and `TagTypeStorageTableV2` DynamoDB tables (the former single-key `TagStorageTable`/`TagTypeStorageTable` are retained as legacy migration sources). The default migration run above includes the `tagsNamespacing` step, which copies every existing tag and tag type into the new tables under the `GLOBAL` partition, so all previously existing tags become GLOBAL tags. Asset tag lists are unchanged. The step is idempotent (already-copied rows are skipped on re-run) and can be run on its own:

```bash
python v2.5_to_v2.6_migration.py --config my_migration_config.json --steps tagsNamespacing
```
:::

:::warning[Provisioned OpenSearch 3.5 upgrade]
The v2.6 CDK switches `OPENSEARCH_VERSION` to `OPENSEARCH_3_5`. This applies only to provisioned deployments (`app.openSearch.useProvisioned.enabled = true`); serverless collections are unaffected. Amazon OpenSearch Service supports in-place version upgrades, but a major-version jump on a long-running domain can occasionally fail or exceed the CloudFormation custom-resource timeout.

If `cdk deploy` fails on the OpenSearch domain version upgrade, recover by deploying first with OpenSearch disabled and then re-enabling it:

1. Set `app.openSearch.useProvisioned.enabled = false` (and `useServerless.enabled = false`) in `infra/config/config.json`.
2. Run `cdk deploy --all --require-approval never` to delete the existing 2.7 domain.
3. Restore the original `useProvisioned` configuration in `config.json`.
4. Run `cdk deploy --all --require-approval never` to create a fresh 3.5 domain with the empty v3 indexes.
5. Run this migration to repopulate the v3 indexes from source data.

This recovery path discards only the OpenSearch indexes; all source data lives in DynamoDB and S3, and the reindex restores the full search corpus. For details and other troubleshooting steps, see the [v2.5 to v2.6 migration README](https://github.com/awslabs/visual-asset-management-system/blob/main/infra/deploymentDataMigration/v2.5_to_v2.6/upgrade/v2.5_to_v2.6_migration_README.md).
:::

#### OpenSearch Serverless next-gen upgrade

v2.6 reworks the OpenSearch Serverless collection and adds new `app.openSearch.useServerless` settings:

| Setting                                                               | Default                                        | Behavior                                                                                                                                                                                                                                                                                              |
| --------------------------------------------------------------------- | ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `nextGen`                                                             | `true` (commercial), `false` (GovCloud and EU) | Sets the collection group generation to `NEXTGEN` (`true`) or `CLASSIC` (`false`). The collection is placed in a collection group either way; `NEXTGEN` adds scale-to-zero support.                                                                                                                   |
| `allowPublic`                                                         | `true`                                         | When `false`, the collection is reachable only through a VPC endpoint and requires `app.useGlobalVpc.enabled` to be `true` (built across 2 Availability Zones). Only the OpenSearch-facing Lambda functions are placed in the VPC, so `app.useGlobalVpc.useForAllLambdas` does not need to be `true`. |
| `enableStandbyReplicas`                                               | tracks `nextGen`                               | Enables Serverless standby replicas. Required for `NEXTGEN` (must be `true` when `nextGen` is `true`); optional for `CLASSIC`. Defaults to the value of `nextGen`.                                                                                                                                    |
| `minIndexingOcu` / `maxIndexingOcu` / `minSearchOcu` / `maxSearchOcu` | `2` / `16` / `2` / `16`                        | OpenSearch Compute Unit (OCU) capacity bounds. Each must be one of `0`, `2`, `4`, `8`, `16`, or any multiple of `16`. A minimum of `0` (scale-to-zero) requires `nextGen = true`.                                                                                                                     |

:::warning[Serverless collections cannot be updated in place]
Previously, a Serverless collection was updated by the CDK changeset like any other resource. In v2.6 the collection is placed in a collection group, the `allowPublic` network-policy change is applied, and the group generation is set, all of which reshape the collection, so an existing Serverless deployment **cannot** be updated in place. Remove the collection and re-create it, then reindex:

1. Set `app.openSearch.useServerless.enabled = false` in `infra/config/config.json` and deploy. This removes the existing collection.

    ```bash
    npx cdk deploy --all --require-approval never
    ```

2. Set `app.openSearch.useServerless.enabled = true` again with the desired settings (`nextGen`, `allowPublic`, and OCU values) and deploy. This creates the new collection group and collection.

    ```bash
    npx cdk deploy --all --require-approval never
    ```

3. Reindex to repopulate the new collection — run the reindex utility (`infra/deploymentDataMigration/v2.5_to_v2.6/upgrade` or `tools/reindex_utility.py`), or set `app.openSearch.reindexOnCdkDeploy = true` for one deployment and then set it back to `false`.

Tearing down the collection deletes the indexes, but all source data lives in Amazon DynamoDB and Amazon S3, so the reindex restores the full search corpus. **GovCloud and EU Sovereign Cloud deployments must keep `nextGen = false`.**
:::

:::note[Reindexing a private collection]
A private collection (`allowPublic = false`) is reachable only from inside the VPC and not from a local machine, so the reindex utility's `direct` mode cannot reach it. For a private collection, reindex through the deployed Lambda (`lambda` mode) or set `app.openSearch.reindexOnCdkDeploy = true`.
:::

#### VPC is now required for certain features

In earlier releases, enabling a feature that needs a VPC while `app.useGlobalVpc.enabled` was `false` silently turned the VPC on. In v2.6 this is a configuration error instead: the deployment fails and lists the features that require a VPC. The VPC-requiring features are ALB (`useAlb`), OpenSearch Provisioned (`openSearch.useProvisioned`), and the container-based pipelines (Potree viewer, 3D preview thumbnail, GenAI labeling, Gaussian splatting, RapidPipeline ECS/EKS, ModelOps, Isaac Lab, NVIDIA Cosmos, NVIDIA Gr00t).

If your existing `config.json` relied on the old implicit behavior, update it before deploying v2.6: set `app.useGlobalVpc.enabled` to `true` (the value the deployment was effectively using all along), or disable the listed features. No infrastructure changes result from setting the flag to the value that was already in effect — this is a configuration-file correction only.

#### OpenSearch Provisioned Availability Zone count downgrade

In v2.6, provisioned OpenSearch adds `app.openSearch.useProvisioned.availabilityZoneCount` (default `2`), and the VPC is built with exactly that many Availability Zones. Earlier releases always built the VPC across **3** Availability Zones for provisioned OpenSearch, even though the OpenSearch domain itself only ever used **2** of them — the third AZ's subnet was created but unused. v2.6 now provisions 2 Availability Zones by default (or 3 only when you set `availabilityZoneCount` to `3`) and uses them consistently.

On upgrade, this is a **VPC downgrade**: the previously-unused third Availability Zone's subnet is removed. Removing a subnet can fail in AWS CloudFormation when the subnet still holds elastic network interfaces — in this case the shared interface VPC endpoints placed across the isolated subnets, and the VPC-attached Lambda (Hyperplane) ENIs created when `app.useGlobalVpc.useForAllLambdas` is `true`. The OpenSearch domain itself does not change AZ count (it was already on 2), so the failure is specifically about deleting the orphaned third-AZ subnet.

You have two options:

-   **Keep the existing 3-AZ VPC layout (no change):** set `app.openSearch.useProvisioned.availabilityZoneCount` to `3` in `config.json` before deploying v2.6. The VPC is unchanged and the upgrade proceeds normally.
-   **Move to the 2-AZ layout (default):** because the third AZ's subnet must be deleted, follow the staged drain-and-redeploy procedure so the elastic network interfaces release first. Turn off the VPC and all VPC-associated components, deploy to release the ENIs, manually clear any orphaned ENIs / subnets / VPC resources that still fail to delete, then redeploy with the 2-AZ settings and reindex. The full step-by-step is documented in [Subnet or VPC Resource Deletion Failures](../troubleshooting/common-issues.md) — set `availabilityZoneCount` to `2` when you re-enable OpenSearch in that procedure. No asset data is lost: search data is rebuilt from the authoritative DynamoDB tables and S3 buckets by the reindex.

:::warning[Plan the Availability Zone choice before first v2.6 deploy]
Decide on `availabilityZoneCount` (`2` or `3`) before upgrading. Setting it to `3` preserves the existing VPC with no teardown. Accepting the default of `2` removes a subnet and may require the manual VPC teardown above if elastic network interfaces have not finished detaching.
:::

#### API Gateway REST API endpoint change

In v2.6 the backend API is an API Gateway REST API (v1) served under the fixed stage path `/api`, replacing the previous HTTP API (v2). On deployment the API Gateway identifier and invoke URL change.

The `app.api` configuration block is also restructured in v2.6: the per-implementation settings move under a new `app.api.apiGatewayRest` sub-block, and a new `app.api.apiType` field (fixed to `"APIGATEWAY_REST"`) selects the API implementation. Configuration validation (`getConfig()`) applies that restructuring automatically — `app.api.apiType` is defaulted, and any flat `globalRateLimit`, `globalBurstLimit`, `endpointType`, `apiGatewayTimeoutTime`, and `externalRegionalAPIGatewayVPCEId` values are carried into `app.api.apiGatewayRest`, the last of them as `optionalExternalPrivateApigVPCEId`, which applies only to a `PRIVATE` endpoint. An existing `config.json` therefore deploys unedited (see the [API configuration reference](configuration-reference.md#api-configuration-appapi)). Custom APIs added in a fork outside this project are the exception: those settings are unknown to the configuration loader, so move them under the new `app.api` shape by hand. Declaring `app.api.apiGatewayRest` yourself replaces the carry-over, so a hand-written block must list every field it needs. The REST API stage name is not a configuration option — it is the fixed value `api`.

The web application is unaffected: it reads the API base URL at runtime from `/api/amplify-config`, and the CloudFront `/api/*` behavior (or ALB redirect) absorbs the stage path so browser URLs remain `/api/*`.

The change affects any client that was configured directly against the **old** API Gateway endpoint URL:

-   **VAMS CLI:** re-run `vamscli setup` and provide the new API URL. When pointing the CLI at the deployment's front (CloudFront/ALB) the base URL is unchanged; when pointing it directly at the execute-api endpoint, pass the bare endpoint URL (`https://{rest-api-id}.execute-api.{region}.amazonaws.com`) — the CLI appends the `/api` stage path automatically.
-   **External integrations and scripts:** update any stored API base URL to the new endpoint.

The deployment exposes the new endpoint as the CloudFormation output `APIGatewayEndpointOutput`. IP allow-list enforcement continues to work for both fronted and direct callers — the authorizer resolves the true client IP from the front's forwarded headers when present, and from the direct connection otherwise — so existing direct integrations keep working once re-pointed at the new URL.

#### Execution visibility

In v2.6 every execution read path applies one permission rule: `GET` on the execution's workflow, plus the operation's action on **every** asset the run read — each input file's asset and each asset named as a metadata source. Earlier releases accepted access to any one of a run's assets for the listing while requiring all of them elsewhere, so a listing could offer a row whose details then returned `403`. One rule across the list, the details, the logs, the abort, and the re-run removes that inconsistency.

The narrower side of this is worth checking against existing roles before upgrading. A role scoped to a subset of databases loses list visibility of runs that span databases outside its scope, and loses the ability to re-run them, even when it can read some of the assets involved. Runs whose assets all sit inside the role's scope behave as before. To restore the earlier breadth for a role, widen its `asset` and `database` GET constraints to cover the databases those runs span.

An execution has assets only when it read or wrote one, and both sides carry the check: a run is authorized on every asset it read **and** on the asset it wrote to. A results-only run writes no files and has no asset at all, leaving workflow `GET` as its whole gate.

The output-asset half narrows access for a deployment that already ran workflows writing into a database outside the reader's scope. A role that could previously list, open, re-run, and abort such a run because it held `GET` on the run's input assets now needs `GET` on the destination asset as well (and `POST` on it to abort or permanently delete, matching the `POST` the launch already required). Review roles that read one database and write to another, and widen their `asset` and `database` constraints to cover the destination where the earlier breadth is still wanted.

Deleting an asset does not delete the executions that ran against it. An asset that has been **permanently deleted** is authorized on the database it lived in, under the same action — a database is never removed, since deleting one archives the record — so the history of runs against a deleted asset stays reachable by whoever can read that database.

**Archiving** an asset is not a deletion and does not change how its executions are authorized. An archived asset's record is retained, so it is still authorized on its own attributes (name, type, tags) exactly as it was before archiving, and any asset-level constraint that applied to it continues to apply.

#### Permission constraint audit for `pipelineType`

In v2.6 the `pipeline` objectType offers the criteria fields `databaseId`, `pipelineId`, `pipelineExecutionType`, `category`, and `name`. `pipelineType` is not among them: the value it held moves to `category`, and the migration copies each migrated pipeline's `pipelineType` value into that field. Built-in pipelines carry descriptive category labels instead, such as `Conversion` and `GenAI`.

When the permission policy is compiled, a criterion whose field is not a recognized constraint field is dropped. For a constraint that is already stored the drop is **silent**: stored constraints are not re-validated, the skip is recorded at informational log level only, and no authorization response reports it. The constraint therefore continues to read as authored while enforcing something different from what it states:

-   Criteria within a rule are combined with AND. Dropping one from an **allow** rule removes a restriction, so the role reaches pipelines the constraint was written to exclude. **This widens access.**
-   A rule whose only criterion was `pipelineType` compiles to no criteria at all and is not emitted, so an allow rule of that shape grants nothing and a deny rule of that shape blocks nothing.

Creating or updating a constraint through the API rejects an unrecognized field, naming it and listing the fields allowed for the objectType, so re-saving one of these constraints fails until the criterion is re-authored. That error is the only signal VAMS gives, and it appears only when someone edits the constraint.

Audit every role constraint that uses the `pipeline` objectType before upgrading, and re-author each `pipelineType` criterion against `category`, using the category value the target pipelines actually carry. Stored constraints are left exactly as authored — no migration step rewrites them — so this audit is the only thing that restores the intended scope. See [Permissions model](../concepts/permissions-model.md) for the constraint criteria structure.

#### Bucket listing route scoped to administrators

`GET /buckets` returns the whole asset-bucket registry — every bucket's name and prefix — and there is
no `bucket` object type, so the listing cannot be filtered per role at Tier 2. The route grant is the
only control available, and in v2.6 it is an administrator grant: the `database-admin` template and the
seeded default administrator role carry it, while the `database-user`, `database-readonly`, and
`global-readonly` templates and the seeded `basicReadOnly` role do not.

The seeded default constraints are rewritten on every deployment, so the `basicReadOnly` role loses the
grant when the update runs. **Constraints already stored in the deployment are not reconciled**: a role
authored by hand or from an earlier copy of a template keeps its `/buckets` grant until that constraint
is re-authored or the updated template is re-imported. Nothing reports the difference, so review the
`api` constraints of any non-administrator role that was built from a template and remove the
`/buckets` criterion to match the shipped scope.

For a role that keeps the route withheld, the only affected surface is the default-bucket selector on
the database create and edit form, which loads no options. Every other database operation continues to
work, and a default bucket can still be set by supplying a known `defaultBucketId` on
`POST /database` or `PUT /database/{databaseId}`.

#### Switching `endpointType` between `PRIVATE` and `REGIONAL`

Changing `app.api.apiGatewayRest.endpointType` on an existing deployment is supported and requires no manual steps. A `PRIVATE` endpoint carries an API Gateway resource policy that only permits invocation through the execute-api VPC interface endpoint (an `aws:SourceVpce` condition); a `REGIONAL` endpoint uses a public allow-all resource policy. VAMS writes the correct resource policy for the configured endpoint type on every deployment, so a `PRIVATE` → `REGIONAL` switch overwrites the VPC-restricted policy with the public one, and a `REGIONAL` → `PRIVATE` switch re-applies the restriction.

This explicit-policy behavior exists because Amazon API Gateway does not clear a previously-set resource policy when an update stops supplying one. If a stale `PRIVATE` resource policy is ever left on a now-`REGIONAL` API (for example, after an out-of-band change to the API), every public request — including the browser CORS preflight — is denied at the resource-policy layer with `403 AccessDeniedException` ("no resource-based policy allows the execute-api:Invoke action"). Because that denial happens before any CORS headers are applied, the browser surfaces it as a missing `Access-Control-Allow-Origin` / failed-preflight error rather than an authorization failure. Re-running the VAMS deployment re-asserts the correct policy for the configured `endpointType` and resolves it.

## Breaking changes checklist

Use this checklist to determine if additional actions are needed after updating.

| Change type                            | Versions affected                          | Action required                                                                                                                         |
| -------------------------------------- | ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| DynamoDB table schema change           | v2.3 to v2.4, v2.4 to v2.5                 | Run version-specific migration scripts.                                                                                                 |
| Amazon OpenSearch Service reindex      | v2.2 to v2.3, v2.3 to v2.4, v2.5 to v2.6   | Run reindex script or set `reindexOnCdkDeploy: true`.                                                                                   |
| OpenSearch engine version upgrade      | v2.5 to v2.6 (provisioned only, 2.7 → 3.5) | Redeploy with OpenSearch disabled then re-enabled if the in-place upgrade fails.                                                        |
| OpenSearch Serverless next-gen reshape | v2.5 to v2.6 (serverless only)             | Disable Serverless, deploy, re-enable with new settings, deploy, then reindex. Keep `nextGen: false` on GovCloud/EU.                    |
| VPC no longer auto-enabled             | v2.5 to v2.6                               | Set `app.useGlobalVpc.enabled: true` (or disable VPC-requiring features) if validation fails.                                           |
| OpenSearch AZ count VPC downgrade      | v2.5 to v2.6 (provisioned only)            | Set `availabilityZoneCount: 3` to keep the existing VPC, or follow the drain-and-redeploy teardown to move to 2 AZs.                    |
| Externally registered pipelines        | v2.5 to v2.6                               | Port each pipeline's input reads, task-token return, and sub-process registration, and declare its definition in a `vamsSchema` bundle. |
| Three API routes removed               | v2.5 to v2.6                               | Repoint direct API clients: `PUT /pipelines` → `POST /database/{databaseId}/pipelines`, `PUT /workflows` → `POST /database/{databaseId}/workflows`, `POST /database/{databaseId}/assets/{assetId}/workflows/{workflowId}` → `POST /workflows/{workflowDatabaseId}/{workflowId}/execute`. A removed route answers `403`, not `404`. |
| AWS WAF count mode → block mode        | v2.5 to v2.6 (`app.useWaf` enabled)        | Review AWS WAF blocked-request metrics after upgrading. Set `"block": false` on a rule group in `infra/config/policy/wafPolicyConfig.json` to return it to monitor mode. Blocked requests produce no VAMS log entry. |
| OpenSearch Serverless `allowPublic`    | v2.5 to v2.6 (serverless only)             | Set `app.openSearch.useServerless.allowPublic: false` when `app.useGlobalVpc.useForAllLambdas` is `true`; the new default of `true` fails `cdk synth` on that topology. |
| API Gateway REST API endpoint change   | v2.5 to v2.6                               | Re-run `vamscli setup`; update any client or script that stored the API Gateway invoke URL.                                             |
| Pipeline constraint field audit        | v2.5 to v2.6                               | Re-author any role constraint criterion that uses `pipelineType` against `category`; no script rewrites constraints.                    |
| Bucket listing route scoped to admins  | v2.5 to v2.6                               | Remove the `/buckets` `api` criterion from non-administrator roles built from an earlier template; stored constraints are not reconciled. |
| Permission constraint migration        | v2.3 to v2.4, v2.4 to v2.5                 | Run constraint migration script if custom constraints exist.                                                                            |
| API Gateway authorizer change          | v2.2 to v2.3                               | Reset authorizer cache after deployment.                                                                                                |
| Pipeline CDK construct rename          | v2.2 to v2.3                               | Deploy without pipelines, then redeploy with pipelines enabled.                                                                         |
| Website framework change               | v2.4 to v2.5                               | Clear `node_modules` and reinstall: `cd web && rm -rf node_modules && npm install`.                                                     |

## Rollback guidance

If an update causes issues, the rollback approach depends on the update method used.

### In-place update rollback

1. Check out the previous version tag:

    ```bash
    git checkout tags/<PREVIOUS_VERSION>
    cd web && npm install && npm run build && cd ..
    cd infra && npm install
    npx cdk deploy --all --require-approval never
    ```

2. If DynamoDB tables were replaced during the update, look for the orphaned tables first. Because the tables use a `RETAIN` removal policy, a replacement leaves the original table (and its data) in the account under its old name, outside the stack. Migrate from the orphaned table where the data is still current, and restore from the backups taken in the pre-update checklist only where it is not.

3. If Amazon OpenSearch Service indexes were modified, trigger a reindex from DynamoDB data.

:::warning[Redeployment alone does not restore a replaced table]
A rollback deployment creates its own tables; it does not reattach the ones a replacement orphaned. Reconnecting the data is a manual step in every case, so maintain backups before updating and record the stack's table names as part of the pre-update checklist.
:::

### A/B deployment rollback

1. Switch DNS records back to Stack A endpoints.
2. Destroy Stack B using `cdk destroy --all`.
3. Verify Stack A is functioning correctly.

## Related resources

-   [Deploy the solution](deploy-the-solution.md)
-   [Uninstall the solution](uninstall.md)
-   [Configuration reference](configuration-reference.md)
