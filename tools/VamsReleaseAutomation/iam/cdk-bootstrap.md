# CDK bootstrap for VAMS environments

VAMS deploys through the standard CDK bootstrap roles (`infra/config/config.ts` builds the
synthesizer from `app.iamRoleConfig`; see `infra/config/policy/iamRoleConfig.json` for custom
bootstrap role names). Every env account (and every region VAMS deploys into) is bootstrapped
once, by an account administrator, before the first `vra deploy`.

The key decision is what you pass to `--cloudformation-execution-policies`. That policy is what
CloudFormation uses to create every VAMS resource. The CDK default is `AdministratorAccess`.
This package ships a scoped alternative: `iam/cloudformation/vra-cdk-execution-policy.yaml`.

## 1. Pick a trust model

| Model                            | Who calls the CDK roles                                                                           | Bootstrap `--trust`                            | When to use                                                                                                                     |
| -------------------------------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| A. Deploy role hop (recommended) | Tools account / CI assumes `vra-deploy-<env>` in the env account, which assumes the `cdk-*` roles | none (bootstrap roles trust their own account) | Strict environments: a single, reviewable entry role per env with its own boundary, session length and preflight surface        |
| B. Direct CDK trust              | Tools account / CI assumes the `cdk-*` roles directly                                             | `--trust <tools account>`                      | Classic CDK Pipelines layout; fewer roles, but every principal in the trusted account that can call `sts:AssumeRole` can deploy |

Model A keeps `vra preflight` meaningful (it simulates `vra-deploy-<env>`) and lets you attach a
permissions boundary. Model B is shown below because many landing zones standardize on it. You can
start with B and move to A without re-bootstrapping (add the deploy role, drop `--trust`).

## 2. Deploy the scoped execution policy

Run in each env account, in the VAMS region. Replace placeholders.

```bash
# Commercial, CloudFront deployment (the CloudFront WAF stack lives in us-east-1)
aws cloudformation deploy \
  --stack-name vra-cdk-execution-policy \
  --template-file iam/cloudformation/vra-cdk-execution-policy.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides AllowedRegions=us-east-1,us-west-2

# GovCloud (ALB deployment, no CloudFront)
aws cloudformation deploy \
  --stack-name vra-cdk-execution-policy \
  --template-file iam/cloudformation/vra-cdk-execution-policy.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides AllowedRegions=us-gov-west-1 \
  --region us-gov-west-1
```

## 3. Bootstrap

Derive the partition from the caller instead of hardcoding `arn:aws:`:

```bash
PARTITION=$(aws sts get-caller-identity --query Arn --output text | cut -d: -f2)
ACCOUNT=222222222222          # env account (placeholder)
REGION=us-gov-west-1          # VAMS region
TOOLS=111111111111            # tools / CI account (placeholder)
POLICY="arn:${PARTITION}:iam::${ACCOUNT}:policy/vra-cdk-execution"
```

Model A (deploy role hop, no `--trust`):

```bash
npx cdk bootstrap "aws://${ACCOUNT}/${REGION}" \
  --cloudformation-execution-policies "${POLICY}"
```

Model B (direct trust of the tools account):

```bash
npx cdk bootstrap "aws://${ACCOUNT}/${REGION}" \
  --trust "${TOOLS}" \
  --trust-for-lookup "${TOOLS}" \
  --cloudformation-execution-policies "${POLICY}"
```

Optional hardening flags (all versions of CDK v2 that VAMS pins support them). With the scoped execution
policy, deploy it with its default boundary first and pass the boundary NAME to the env's
`permissionsBoundaryName` (see "The permissions boundary is mandatory" below):

```bash
  --bootstrap-kms-key-id <key arn>             # CMK for the CDK assets bucket
  --custom-permissions-boundary <policy name>  # boundary on the bootstrap roles themselves
  --qualifier <10 chars>                       # isolate VAMS from other CDK apps in the account
  --tags vra:managed=true
```

If you use a custom qualifier, set it in `infra/config/policy/iamRoleConfig.json`
(`bootstrap.qualifier`) through the env overlay, and enable `app.iamRoleConfig.useCustomBootstrapRoles`.

Commercial CloudFront deployments also bootstrap `us-east-1` (the CloudFront WAF stack), with the
same flags. GovCloud and EU Sovereign VAMS templates use the ALB and do not need a second region.

Then deploy `iam/cloudformation/vra-deploy-role.yaml` (model A) in the env account.

### Source identity

The test and prod deploy roles require `sts:SourceIdentity` by default (`RequireSourceIdentity=auto`,
`EnvTier=test|prod`; dev roles: `EnvTier=dev`, optional), and `vra` sets it to the run initiator on gated
environments (`setSourceIdentity` defaults to `true` there). CloudTrail in the env account then shows who
started every deploy call, through every chained role.

AWS requires `sts:SetSourceIdentity` in the trust policy of each role a session with a source identity chains
into. The stock CDK bootstrap template does not grant it, so `cdk deploy` from such a session is denied at its
first `AssumeRole` of a bootstrap role. Bootstrap the env accounts of gated environments with the patched
template (every region VAMS deploys into, including `us-east-1` for CloudFront deployments):

```bash
npx cdk bootstrap --show-template > bootstrap-template.yaml
python3 iam/scripts/patch-bootstrap-template.py bootstrap-template.yaml > bootstrap-template.vra.yaml
npx cdk bootstrap "aws://${ACCOUNT}/${REGION}" --template bootstrap-template.vra.yaml \
  --cloudformation-execution-policies "${POLICY}"
```

The script copies every `sts:TagSession` trust statement of the file-publishing, image-publishing, lookup and
deploy roles as an `sts:SetSourceIdentity` statement with the same principal and condition; the
CloudFormation execution role is assumed by the service and is unchanged. Re-run it whenever you upgrade the
CDK CLI's bootstrap template. `vra deploy` checks the four roles' trust (`iam:GetRole`, step
`deploy.<env>.source-identity-trust`) and stops with `20` before changing anything if one of them lacks it.
If your landing zone owns the bootstrap roles, ask for the same trust statements. Opting out for an
environment: `setSourceIdentity: false` in its `env.yaml` and `RequireSourceIdentity=false` on its deploy role
(`ledger validate` warns for gated environments).

## 4. Trade-offs of the scoped execution policy

|                                                | `AdministratorAccess` (CDK default) | `vra-cdk-execution` (this package)                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ---------------------------------------------- | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Deploy reliability                             | Never fails on permissions          | Fails when a new VAMS feature uses a service not on the list; `vra` classifies the CloudFormation event and writes an IT request with the missing action                                                                                                                                                                                                                                                                                                                                 |
| Blast radius of a malicious or broken template | Whole account                       | Listed services in allowed regions; no IAM users, access keys, Organizations, account settings; cannot modify `cdk-*` / `vra-*` roles                                                                                                                                                                                                                                                                                                                                                    |
| Privilege escalation                           | A template can create an admin role | Every role CloudFormation creates must carry the permissions boundary (created by the stack as `vra-cdk-execution-role-boundary`, or your `RequiredPermissionsBoundaryArn`), which denies IAM changes, audit-trail and key destruction to VAMS roles. The execution policy itself, the boundary and every `vra-*` / `cdk-*` policy cannot be rewritten (`CreatePolicyVersion` denied); `iam:PassRole` is limited by `iam:PassedToService`; AWS-managed admin policies cannot be attached |
| Maintenance                                    | None                                | Re-check on each VAMS minor release (`vra ledger upgrade-config` reports new config keys; new pipelines often mean new services)                                                                                                                                                                                                                                                                                                                                                         |
| Auditor view                                   | Usually flagged                     | Explainable statement by statement                                                                                                                                                                                                                                                                                                                                                                                                                                                       |

Things the scoped policy deliberately still allows, because VAMS needs them: key management on the keys
VAMS stacks create (`kms:PutKeyPolicy` / `ScheduleKeyDeletion` only where the key's
`aws:cloudformation:stack-name` tag starts with `<ConfigName>-`), `ec2:*` (VPC, endpoints, security groups),
`iam:PassRole` to the services VAMS wires roles to (Lambda, Step Functions, ECS tasks, Batch, EC2, EKS, API
Gateway, EventBridge, CodeBuild, OpenSearch, Cognito user and identity pools, CloudTrail, VPC flow logs,
Bedrock AgentCore), and the CloudTrail actions for VAMS's own trail
(`app.addStackCloudTrailLogs`; trails not named `<ConfigName>-*` cannot be stopped, deleted or changed).
Tighten further only with a tested deploy in a sandbox.

### The permissions boundary is mandatory

`vra-cdk-execution-policy.yaml` refuses `iam:CreateRole` without the boundary (escape hatch
`AllowRolesWithoutBoundary=true`, for a first evaluation only). CDK applies a boundary to every role it
synthesizes when the context `@aws-cdk/core:permissionsBoundary` is set; `vra build` sets it from the env's
`permissionsBoundaryName` in the ledger (output `RoleBoundaryName` of the stack). Set it for every env of an
account that uses this policy.

Check before you rely on it: VAMS's own `IamRoleTransform` aspect (`infra/lib/aspects/iam-role-transform.aspect.ts`,
applied by `core-stack.ts` when the deployment passes `IamRoleNamePrefix` / `PermissionBoundaryArn`)
overrides `PermissionsBoundary` with `AWS::NoValue` in both of its branches. With that aspect active,
CreateRole is denied by this policy and the deploy stops with an IT request instead of creating unbounded
roles (fail closed). Run one sandbox deploy to confirm which applies to your configuration.

Service list source: `iam/vams-services.json`, extracted from every `infra/lib` and `infra/bin` file of a
VAMS ref by `iam/scripts/extract_vams_services.py`. It counts every `aws-cdk-lib` module VAMS uses under any
config option (Cognito user and identity pools, OpenSearch Serverless or provisioned, Location, WAF, VPC
endpoints and flow logs, Batch / ECS / EKS pipelines, Bedrock and Bedrock AgentCore, EventBridge, Step
Functions, CloudTrail, ALB / ACM / Route 53, CodeBuild / CodeArtifact) and maps each module to the IAM
service prefixes CloudFormation calls and to the services its roles are passed to. Deadline Cloud pipelines
only grant `deadline:*` to VAMS roles at run time; CloudFormation creates no Deadline Cloud resource, so the
execution policy does not list it. `iam/tests/test_execution_policy_services.py` fails when the policy and
the extraction disagree in either direction. Re-derive it on each VAMS upgrade (read-only):

```bash
python3 iam/scripts/extract_vams_services.py <vams-clone> --ref origin/main --check   # exit 1 on a difference
python3 iam/scripts/extract_vams_services.py <vams-clone> --ref origin/main           # rewrite the JSON
```

A module the script does not know stops it with the module name; add the module's IAM prefixes and passed
services to `MODULES`, then update the policy until the test passes.

### Regions

`AllowedRegions` must list every region a VAMS stack of this account lives in, not only the VAMS region:

-   `us-east-1` for the CloudFront WAF stack (`{name}-waf-{base}`) whenever `useCloudFront` and `useWaf` are on.
-   The region of every external asset bucket (`app.assetBuckets.externalAssetBuckets`): VAMS creates one
    `{name}-xregion-{base}-{bucketRegion}` stack (with its own KMS key when CMK encryption is on) per bucket region.

Bootstrap each of those regions as well (same flags).

## 5. When the landing zone owns bootstrap

Some organizations pre-create bootstrap roles. Point VAMS at them through the env overlay
`overlay/infra/config/policy/iamRoleConfig.json` (`bootstrap.deployRoleArn`,
`cloudFormationExecutionRoleArn`, `fileAssetPublishingRoleArn`, ...) and
`app.iamRoleConfig.useCustomBootstrapRoles: true`.

What vra models today, and what it does not:

| VAMS setting (`iamRoleConfig.json`)                          | vra                                                                                                                                                                                                                                                         |
| ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Default bootstrap roles, one qualifier                       | Supported. Set the qualifier once in `vra.yaml` `cdk.qualifier`                                                                                                                                                                                             |
| A different qualifier per account or env                     | Not modelled: the deploy role's `CdkBootstrapRolesAssume` statement and `iam/actions/deploy.yaml` match `cdk-*-<kind>-role-<account>-*` (any qualifier), but the source-identity trust check reads the project qualifier only. Use one qualifier per ledger |
| Custom bootstrap role names (`bootstrap.deployRoleArn` etc.) | Not modelled: edit `CdkBootstrapRolesAssume` in `vra-deploy-role.yaml` and `iam/actions/deploy.yaml` to the custom role ARNs, and expect the source-identity trust check to report the stock names                                                          |
| `useCliCredentialsSynthesizer` (no bootstrap roles)          | Not supported. The deploy role would need every permission of the CloudFormation execution policy itself; keep the bootstrap roles                                                                                                                          |

The first symptom of an unmodelled setting is a denied `sts:AssumeRole` at deploy time; `vra` writes an IT
request for it. Run `vra preflight --workflow deploy` after any change to these files.

## 6. One account is one trust boundary

Several VAMS stacks can share an account (unique `ConfigName`, `BaseStackName` and region per env). They also
share the account's CDK bootstrap roles and CloudFormation execution policy, and every `vra-deploy-<env>` role
in the account can assume those bootstrap roles. A deploy role can therefore change any VAMS stack in its
account, whatever its own resource scoping says. Put envs that must be isolated from each other (prod and
anything else, tenants of different customers) in separate accounts, or bootstrap them with separate
qualifiers and restrict each deploy role's `CdkBootstrapRolesAssume` to its own qualifier.
