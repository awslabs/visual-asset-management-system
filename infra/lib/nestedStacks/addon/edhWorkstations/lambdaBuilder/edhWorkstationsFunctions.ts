/*
 * EDH Workstations add-on Lambda function builders.
 *
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import { Construct } from "constructs";
import * as cdk from "aws-cdk-lib";
import { Duration } from "aws-cdk-lib";
import {
    suppressCdkNagErrorsByGrantReadWrite,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    setupSecurityAndLoggingEnvironmentAndPermissions,
    suppressCdkNagLambda,
} from "../../../../helper/security";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import {
    API_GATEWAY_DEFAULT_TIMEOUT_SECONDS,
    LAMBDA_PYTHON_RUNTIME,
} from "../../../../../config/config";
import * as Config from "../../../../../config/config";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { storageResources } from "../../../storage/storageBuilder-nestedStack";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import { NagSuppressions } from "cdk-nag";
import { IAMArn, Service } from "../../../../helper/service-helper";

// Timeout of every Lambda attached to an `/addon/edh/*` route. API Gateway answers 504 at its
// integration timeout, whose floor is this constant, so a longer function timeout would only keep a
// request the caller has already lost running against the EDH cluster.
export const EDH_API_LAMBDA_TIMEOUT = Duration.seconds(API_GATEWAY_DEFAULT_TIMEOUT_SECONDS);

// The tags the AMI recipe service and registrar write on an AMI and its snapshots, as
// `common/edhImageBuilder.py` spells them: the recipe, its content hash and the deployment prefix, plus
// EDH's per-cluster visibility tag. The IAM conditions on AMI tagging and deletion name these keys.
const EDH_AMI_DEPLOYMENT_TAG = "vams:deployment";
const EDH_AMI_VAMS_TAG_KEYS = [
    "vams:edhAmiRecipe",
    "vams:edhAmiRecipeHash",
    EDH_AMI_DEPLOYMENT_TAG,
];
const EDH_VISIBILITY_TAG_PREFIX = "edh:visibility:";

/** VPC placement shared by every add-on Lambda: isolated subnets behind the add-on security group. */
export interface EdhLambdaNetwork {
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    securityGroup: ec2.ISecurityGroup;
}

/** Operator-managed secrets the handlers read: the EDH root key and an optional CA bundle (PEM). */
export interface EdhLambdaSecrets {
    rootKeySecret: secretsmanager.ISecret;
    caBundleSecret?: secretsmanager.ISecret;
}

/** The add-on's DynamoDB tables, created by the EDH Workstations nested stack. */
export interface EdhTables {
    sessionTable: dynamodb.ITable;
    amiRecipeTable: dynamodb.ITable;
}

export interface BuildEdhLambdaProps {
    scope: Construct;
    name: string;
    timeout: Duration;
    lambdaCommonBaseLayer: LayerVersion;
    storageResources: storageResources;
    tables: EdhTables;
    config: Config.Config;
    network: EdhLambdaNetwork;
    secrets: EdhLambdaSecrets;
    /** Session-table access; a handler that never opens the table opts out. */
    sessionTable?: "readWrite" | "none";
    /** AMI recipe-table access; the build Lambdas write it, the launch catalog only reads it. */
    amiRecipeTable?: "readWrite" | "read" | "none";
}

export function buildCommonEdhLambda(props: BuildEdhLambdaProps): lambda.Function {
    const {
        scope,
        name,
        timeout,
        lambdaCommonBaseLayer,
        storageResources,
        tables,
        config,
        network,
        secrets,
        sessionTable = "readWrite",
        amiRecipeTable = "none",
    } = props;
    const edh = config.app.addons.useEdhWorkstations;

    // The handlers reach the EDH ALB over the VPC, so the add-on itself places them in it; the
    // deployment-wide flag is honoured as well.
    const inVpc =
        edh.enabled ||
        (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas);

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../../../../backend/backend`)),
        handler: `handlers.addon.edh.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: timeout,
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc: inVpc ? network.vpc : undefined,
        vpcSubnets: inVpc ? { subnets: network.subnets } : undefined,
        securityGroups: inVpc ? [network.securityGroup] : undefined,
        environment: {
            // EDH cluster configuration
            EDH_API_ENDPOINT: edh.edhApiEndpoint,
            EDH_ADMIN_KEY_SECRET_ARN: secrets.rootKeySecret.secretArn,
            EDH_CA_BUNDLE_SECRET_ARN: secrets.caBundleSecret
                ? secrets.caBundleSecret.secretArn
                : "",
            EDH_API_TLS_SERVER_NAME: edh.edhApiTlsServerName ?? "",
            EDH_CLUSTER_ID: edh.edhClusterId,
            EDH_AUTO_PROVISION_USERS: edh.autoProvisionUsers ? "true" : "false",
            EDH_ALLOWED_INSTANCE_TYPES: JSON.stringify(edh.edhAllowedInstanceTypes),
            EDH_VDI_DEFAULT_PROJECT: edh.vdi.defaultProject,
            EDH_VDI_PROFILE_ID: String(edh.vdi.virtualDesktopProfileId),
            EDH_AMI_BUILDS_ENABLED: edh.amiBuilds.enabled ? "true" : "false",
        },
    });

    // Session rows: idempotent create, ownership check and lifecycle state.
    if (sessionTable === "readWrite") {
        tables.sessionTable.grantReadWriteData(fun);
    }

    // AMI recipe rows: the recipe definition, its build history and the AMI ids of each build.
    if (amiRecipeTable === "readWrite") {
        tables.amiRecipeTable.grantReadWriteData(fun);
    } else if (amiRecipeTable === "read") {
        tables.amiRecipeTable.grantReadData(fun);
    }

    // Secrets Manager read on the operator-managed secrets.
    secrets.rootKeySecret.grantRead(fun);
    secrets.caBundleSecret?.grantRead(fun);

    // Required security calls
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    suppressCdkNagLambda(fun);
    return fun;
}

/**
 * Lets a function read EC2 capacity reservations live: the launch checks and the catalog of the
 * workstation service, the launch worker's re-read before its one retry, and the admin registry. EC2
 * offers no resource-level scope for a Describe call, so the grant names `*`; it is the one EC2
 * action in these roles' own policies, and no add-on role may create, modify or cancel a reservation.
 */
export function grantDescribeCapacityReservations(fun: lambda.Function): void {
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["ec2:DescribeCapacityReservations"],
            resources: ["*"],
        })
    );
    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: "ec2:DescribeCapacityReservations accepts no resource ARN or resource-level condition, so the read of the capacity reservations an administrator registers names every resource; it is read-only, and the role holds no EC2 write.",
                appliesTo: [{ regex: "/^Resource::\\*$/g" }],
            },
        ],
        true
    );
}

/** Backs every `/addon/edh/workstations*` and `/addon/edh/catalog/*` route. */
export function buildEdhWorkstationServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    tables: EdhTables,
    config: Config.Config,
    network: EdhLambdaNetwork,
    secrets: EdhLambdaSecrets
): lambda.Function {
    const fun = buildCommonEdhLambda({
        scope,
        name: "edhWorkstationService",
        timeout: EDH_API_LAMBDA_TIMEOUT,
        lambdaCommonBaseLayer,
        storageResources,
        tables,
        config,
        network,
        secrets,
        // The launch catalog reads which images recipe builds registered; only with AMI builds on.
        amiRecipeTable: config.app.addons.useEdhWorkstations.amiBuilds.enabled ? "read" : "none",
    });
    // The capacity reservation catalog and the launch checks read each reservation live.
    grantDescribeCapacityReservations(fun);
    return fun;
}

// The launch worker runs the EDH calls of one launch (the directory user, the desktop create, the
// read-back of the session id) after the API request has been answered, under the client's batch
// profile, and the start of a stopped desktop under its start profile: EDH answers a start only once its
// own capacity work is done, so the start reads for up to 240 s, and a start whose answer is lost is
// followed by reads of the desktop's state every 15 s for what is left of the start's window. Its
// timeout covers the start budget, those reads and the row and credential work around them.
export const EDH_LAUNCH_WORKER_TIMEOUT = Duration.minutes(6);

// Lambda's asynchronous retries wait one minute, then two, between attempts; the event must outlive
// three attempts that each run to the worker's timeout, plus a margin for the time an event waits
// in Lambda's queue before its first attempt, or the third is discarded before it runs.
const EDH_LAUNCH_WORKER_ATTEMPTS = 3;
const EDH_LAUNCH_WORKER_RETRY_PAUSE = Duration.minutes(3);
const EDH_LAUNCH_WORKER_AGE_MARGIN = Duration.minutes(2);
export const EDH_LAUNCH_WORKER_MAX_EVENT_AGE = Duration.minutes(
    EDH_LAUNCH_WORKER_TIMEOUT.toMinutes() * EDH_LAUNCH_WORKER_ATTEMPTS +
        EDH_LAUNCH_WORKER_RETRY_PAUSE.toMinutes() +
        EDH_LAUNCH_WORKER_AGE_MARGIN.toMinutes()
);

/** Runs one workstation launch asynchronously on behalf of the workstation service. */
export function buildEdhLaunchWorkerFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    tables: EdhTables,
    config: Config.Config,
    network: EdhLambdaNetwork,
    secrets: EdhLambdaSecrets
): lambda.Function {
    const fun = buildCommonEdhLambda({
        scope,
        name: "edhLaunchWorker",
        timeout: EDH_LAUNCH_WORKER_TIMEOUT,
        lambdaCommonBaseLayer,
        storageResources,
        tables,
        config,
        network,
        secrets,
    });
    // A transient EDH failure leaves the row CREATING and raises, so the event is retried; an event
    // dropped past the age leaves the row to the session reconciler's stale-launch sweep.
    fun.configureAsyncInvoke({
        retryAttempts: EDH_LAUNCH_WORKER_ATTEMPTS - 1,
        maxEventAge: EDH_LAUNCH_WORKER_MAX_EVENT_AGE,
    });
    // A create EDH refuses for a reservation-bound launch is retried once as an on-demand launch,
    // and only when the reservation, read again, is full and its registry row allows the fallback.
    grantDescribeCapacityReservations(fun);
    // A start EDH refuses names the Availability Zone of the stopped desktop's instance, the one zone it can
    // start in, read from EC2.
    grantDescribeInstances(fun);
    return fun;
}

/**
 * Lets the launch worker read a stopped desktop's instance for the Availability Zone a refused start
 * names. `ec2:DescribeInstances` accepts no resource ARN or resource-level condition, so the grant
 * names `*`; it is read-only, and the role holds no EC2 write.
 */
export function grantDescribeInstances(fun: lambda.Function): void {
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["ec2:DescribeInstances"],
            resources: ["*"],
        })
    );
    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: "ec2:DescribeInstances accepts no resource ARN or resource-level condition, so the read of a stopped workstation's instance, for the Availability Zone a refused start names, names every resource; it is read-only, and the role holds no EC2 write.",
                appliesTo: [{ regex: "/^Resource::\\*$/g" }],
            },
        ],
        true
    );
}

export interface EdhSessionBootstrapResources {
    apiKeyServiceFunction: lambda.IFunction;
    /** The session-secrets CMK; present only when sessionBootstrap is enabled. */
    sessionSecretsKey?: kms.IKey;
}

export interface SessionBootstrapGrantOptions {
    /**
     * Grant the credential-revocation set — the apiKeyService invoke and the Secrets Manager actions
     * that overwrite and remove a session secret — whether or not session bootstrap is enabled, so
     * the function can still take back the keys and secrets a deployment minted before turning the
     * feature off. The minting grants and the environment values follow the feature regardless.
     */
    revokeWithoutBootstrap?: boolean;
}

// The Secrets Manager actions of a revoke: the value is overwritten, then the container removed.
const SESSION_SECRET_REVOKE_ACTIONS = [
    "secretsmanager:PutSecretValue",
    "secretsmanager:DeleteSecret",
];
// The actions a mint adds: the container is created and tagged, and read back on a name collision.
const SESSION_SECRET_MINT_ACTIONS = [
    "secretsmanager:CreateSecret",
    "secretsmanager:DescribeSecret",
    "secretsmanager:TagResource",
];

// The web URL a workstation's CLI is set up against: the deployment's static host when it has one,
// otherwise an `ssm:` pointer to the parameter the web stack writes once the distribution exists,
// which the handler reads at runtime.
function vamsBaseUrl(config: Config.Config): string {
    if (config.app.useAlb.enabled && config.app.useAlb.domainHost) {
        return `https://${config.app.useAlb.domainHost}`;
    }
    const custom = config.app.useCloudFront.customDomain;
    if (
        config.app.useCloudFront.enabled &&
        custom.enabled &&
        custom.domainHost &&
        custom.domainHost !== "UNDEFINED"
    ) {
        return `https://${custom.domainHost}`;
    }
    return `ssm:${config.webUrlDeploymentSSMParam}`;
}

export function applySessionBootstrapEnvironmentAndPermissions(
    fun: lambda.Function,
    config: Config.Config,
    resources: EdhSessionBootstrapResources,
    options: SessionBootstrapGrantOptions = {}
): void {
    const bootstrap = config.app.addons.useEdhWorkstations.sessionBootstrap;
    const mint = bootstrap.enabled;
    const revoke = mint || options.revokeWithoutBootstrap === true;
    fun.addEnvironment("EDH_SESSION_BOOTSTRAP_ENABLED", bootstrap.enabled ? "true" : "false");
    fun.addEnvironment("EDH_SESSION_SECRET_PREFIX", bootstrap.secretNamePrefix);
    fun.addEnvironment("EDH_SESSION_KEY_TTL_MINUTES", String(bootstrap.sessionKeyTtlMinutes));
    fun.addEnvironment(
        "EDH_WORKSTATION_INSTANCE_ROLE_ARNS",
        JSON.stringify(bootstrap.workstationInstanceRoleArns)
    );
    fun.addEnvironment(
        "EDH_SESSION_SECRET_KMS_KEY_ARN",
        resources.sessionSecretsKey ? resources.sessionSecretsKey.keyArn : ""
    );
    fun.addEnvironment(
        "API_KEY_SERVICE_LAMBDA_FUNCTION_NAME",
        resources.apiKeyServiceFunction.functionName
    );
    fun.addEnvironment("VAMS_BASE_URL", vamsBaseUrl(config));

    const suppressions: { id: string; reason: string; appliesTo: { regex: string }[] }[] = [];

    // The variables above are read at import whether or not the feature is on. The minting grants
    // follow the feature; the revocation grants do too unless the caller keeps them, so a deployment
    // without session bootstrap holds no path to the API key service except from the function that
    // cleans up what an earlier configuration left behind.
    if (revoke) {
        resources.apiKeyServiceFunction.grantInvoke(fun);
        suppressions.push({
            id: "AwsSolutions-IAM5",
            reason: "lambda:InvokeFunction is granted on the API key service function and its version-qualified form; the qualifier wildcard names the one function the session bootstrap cross-calls.",
            appliesTo: [{ regex: "/^Resource::<.*>:\\*$/g" }],
        });
    }
    if (mint) {
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["ssm:GetParameter"],
                resources: [IAMArn(config.webUrlDeploymentSSMParam.replace(/^\/+/, "")).ssm],
            })
        );
    }

    if (revoke) {
        // One secret per launch, named <prefix><bootstrapId> at request time, so the resource can
        // only be addressed by the prefix.
        const sessionSecretArns = [
            Service("SECRETSMANAGER").ARN(`secret:${bootstrap.secretNamePrefix}*`),
        ];
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: [
                    ...(mint ? SESSION_SECRET_MINT_ACTIONS : []),
                    ...SESSION_SECRET_REVOKE_ACTIONS,
                ],
                resources: sessionSecretArns,
            })
        );
        if (mint) {
            // PutResourcePolicy is granted only for calls that ask Secrets Manager to reject a public
            // policy, so a session secret can never be opened to principals outside its instance
            // binding.
            fun.addToRolePolicy(
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["secretsmanager:PutResourcePolicy"],
                    resources: sessionSecretArns,
                    conditions: { Bool: { "secretsmanager:BlockPublicPolicy": "true" } },
                })
            );
        }
        // Every value written under the prefix, by a mint or a revoke, is encrypted under the
        // session-secrets key, which exists only while the feature is on.
        resources.sessionSecretsKey?.grantEncryptDecrypt(fun);
        suppressions.push({
            id: "AwsSolutions-IAM5",
            reason: "Session secrets are created at request time, one per EDH workstation launch, under the deployment's configured name prefix; the wildcard is bounded to that prefix and to the lifecycle actions of those secrets.",
            appliesTo: [
                {
                    regex: "/^Resource::arn:[a-z-]+:secretsmanager:[a-z0-9-]+:\\d{12}:secret:.*\\*$/g",
                },
            ],
        });
    }
    if (suppressions.length > 0) {
        NagSuppressions.addResourceSuppressions(fun, suppressions, true);
    }
}

// Scheduled sweep over the session table; a batch-profile worker, so it gets the 15-minute
// timeout rather than the API-facing one.
export function buildEdhSessionReconcilerFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    tables: EdhTables,
    config: Config.Config,
    network: EdhLambdaNetwork,
    secrets: EdhLambdaSecrets
): lambda.Function {
    return buildCommonEdhLambda({
        scope,
        name: "edhSessionReconciler",
        timeout: Duration.minutes(15),
        lambdaCommonBaseLayer,
        storageResources,
        tables,
        config,
        network,
        secrets,
    });
}

/** Backs every `/addon/edh/admin/*` route. */
export function buildEdhAdminServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    tables: EdhTables,
    config: Config.Config,
    network: EdhLambdaNetwork,
    secrets: EdhLambdaSecrets
): lambda.Function {
    const fun = buildCommonEdhLambda({
        scope,
        name: "edhAdminService",
        timeout: EDH_API_LAMBDA_TIMEOUT,
        lambdaCommonBaseLayer,
        storageResources,
        tables,
        config,
        network,
        secrets,
    });
    // The capacity reservation registry checks a reservation exists on register and lists each live.
    grantDescribeCapacityReservations(fun);
    return fun;
}

// Deploy-time catalog registration. Walks the predefined lists with the batch client profile, so it
// gets the Lambda maximum rather than the API-facing timeout.
export function buildEdhCatalogRegistrationFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    tables: EdhTables,
    config: Config.Config,
    network: EdhLambdaNetwork,
    secrets: EdhLambdaSecrets
): lambda.Function {
    return buildCommonEdhLambda({
        scope,
        name: "edhCatalogRegistration",
        timeout: Duration.minutes(15),
        lambdaCommonBaseLayer,
        storageResources,
        tables,
        config,
        network,
        secrets,
        // The registrar writes the session table solely to insert the reserved
        // lease-settings row once at deploy, through a conditional put, and never
        // touches session rows.
        sessionTable: "readWrite",
    });
}

/**
 * Backs every `/addon/edh/containers/*` route; the one add-on Lambda that reads the EKS switch. It
 * proxies EDH and opens no session row.
 */
export function buildEdhContainerServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    tables: EdhTables,
    config: Config.Config,
    network: EdhLambdaNetwork,
    secrets: EdhLambdaSecrets
): lambda.Function {
    const fun = buildCommonEdhLambda({
        scope,
        name: "edhContainerService",
        timeout: EDH_API_LAMBDA_TIMEOUT,
        lambdaCommonBaseLayer,
        storageResources,
        tables,
        config,
        network,
        secrets,
        sessionTable: "none",
    });
    fun.addEnvironment(
        "EDH_CONTAINERS_EKS_ENABLED",
        config.app.addons.useEdhWorkstations.containers.eksEnabled ? "true" : "false"
    );
    return fun;
}

export interface EdhAmiBuildResources {
    /** `<config.name>-<baseStackName>-edh`, lower-cased: the name prefix and IAM scope of every Image Builder object. */
    prefix: string;
    subnetId: string;
    securityGroup: ec2.ISecurityGroup;
    instanceProfileName: string;
    instanceRole: iam.IRole;
    /** Artefacts bucket the recipe bundles and asset trees are uploaded to. */
    artefactsBucketName: string;
}

/** Backs every `/addon/edh/admin/ami-recipes*` route; opens no session row. */
export function buildEdhAmiRecipeServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    tables: EdhTables,
    config: Config.Config,
    network: EdhLambdaNetwork,
    secrets: EdhLambdaSecrets
): lambda.Function {
    return buildCommonEdhLambda({
        scope,
        name: "edhAmiRecipeService",
        timeout: EDH_API_LAMBDA_TIMEOUT,
        lambdaCommonBaseLayer,
        storageResources,
        tables,
        config,
        network,
        secrets,
        sessionTable: "none",
        amiRecipeTable: "readWrite",
    });
}

/** Completes AMI builds from the Image Builder state event and the 30-minute sweep. */
export function buildEdhAmiBuildRegistrarFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    tables: EdhTables,
    config: Config.Config,
    network: EdhLambdaNetwork,
    secrets: EdhLambdaSecrets
): lambda.Function {
    return buildCommonEdhLambda({
        scope,
        name: "edhAmiBuildRegistrar",
        timeout: Duration.minutes(15),
        lambdaCommonBaseLayer,
        storageResources,
        tables,
        config,
        network,
        secrets,
        sessionTable: "none",
        amiRecipeTable: "readWrite",
    });
}

/**
 * The build environment and the Image Builder, EC2, SSM and IAM grants a Lambda that materializes,
 * starts, completes or deletes AMI builds needs. Every Image Builder grant is scoped to objects named
 * with the deployment prefix; AMI and snapshot deletion to resources tagged by a recipe. `cancelsBuilds`
 * adds the cancel the sweep issues for a build still running past the timeout.
 */
export function applyEdhAmiBuildEnvironmentAndPermissions(
    fn: lambda.Function,
    config: Config.Config,
    resources: EdhAmiBuildResources,
    options: { cancelsBuilds?: boolean; deletes?: boolean } = {}
): void {
    const stack = cdk.Stack.of(fn);
    const edh = config.app.addons.useEdhWorkstations;
    fn.addEnvironment("EDH_AMI_BUILD_PREFIX", resources.prefix);
    fn.addEnvironment("EDH_AMI_BUILD_SUBNET_ID", resources.subnetId);
    fn.addEnvironment("EDH_AMI_BUILD_SECURITY_GROUP_ID", resources.securityGroup.securityGroupId);
    fn.addEnvironment("EDH_AMI_BUILD_INSTANCE_PROFILE_NAME", resources.instanceProfileName);
    fn.addEnvironment("EDH_AMI_BUILD_ACCOUNT_ID", stack.account);
    fn.addEnvironment("EDH_AMI_BUILD_PARTITION", stack.partition);
    fn.addEnvironment("EDH_AMI_BUILD_TIMEOUT_MINUTES", String(edh.amiBuilds.buildTimeoutMinutes));
    fn.addEnvironment("EDH_AMI_KEEP_VERSIONS_DEFAULT", String(edh.amiBuilds.keepVersionsDefault));
    fn.addEnvironment("EDH_AMI_BUILD_ARTEFACTS_BUCKET", resources.artefactsBucketName);
    // `${DEPLOY_sessionSecretPrefix}` in a component document. getConfig() defaults the prefix whether
    // or not session bootstrap is on.
    fn.addEnvironment("EDH_SESSION_SECRET_PREFIX", edh.sessionBootstrap.secretNamePrefix);

    const imageBuilderArn = (resource: string, account: string = stack.account) =>
        stack.formatArn({
            service: "imagebuilder",
            account,
            resource,
            resourceName: `${resources.prefix}-*`,
            arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME,
        });
    const ec2Arn = (resource: string) =>
        stack.formatArn({
            service: "ec2",
            account: "",
            resource,
            resourceName: "*",
            arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME,
        });

    fn.addToRolePolicy(
        new iam.PolicyStatement({
            actions: [
                "imagebuilder:CreateComponent",
                "imagebuilder:GetComponent",
                "imagebuilder:CreateImageRecipe",
                "imagebuilder:GetImageRecipe",
                "imagebuilder:CreateInfrastructureConfiguration",
                "imagebuilder:GetInfrastructureConfiguration",
                "imagebuilder:UpdateInfrastructureConfiguration",
                "imagebuilder:CreateDistributionConfiguration",
                "imagebuilder:GetDistributionConfiguration",
                "imagebuilder:UpdateDistributionConfiguration",
                "imagebuilder:CreateImagePipeline",
                "imagebuilder:GetImagePipeline",
                "imagebuilder:UpdateImagePipeline",
                "imagebuilder:StartImagePipelineExecution",
                "imagebuilder:GetImage",
                "imagebuilder:ListComponentBuildVersions",
                "imagebuilder:ListImagePipelineImages",
                "imagebuilder:TagResource",
            ],
            resources: [
                "component",
                "image-recipe",
                "infrastructure-configuration",
                "distribution-configuration",
                "image-pipeline",
                "image",
            ].map((r) => imageBuilderArn(r)),
        })
    );
    // The deletes belong to the Lambdas that retire, prune and remove recipes; the deploy-time catalog
    // registration seeds recipes and starts builds only, so it is built without them.
    const deletes = options.deletes !== false;
    if (deletes) {
        fn.addToRolePolicy(
            new iam.PolicyStatement({
                actions: [
                    "imagebuilder:DeleteComponent",
                    "imagebuilder:DeleteImageRecipe",
                    "imagebuilder:DeleteInfrastructureConfiguration",
                    "imagebuilder:DeleteDistributionConfiguration",
                    "imagebuilder:DeleteImagePipeline",
                    "imagebuilder:DeleteImage",
                ],
                resources: [
                    "component",
                    "image-recipe",
                    "infrastructure-configuration",
                    "distribution-configuration",
                    "image-pipeline",
                    "image",
                ].map((r) => imageBuilderArn(r)),
            })
        );
    }
    if (options.cancelsBuilds) {
        fn.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["imagebuilder:CancelImageCreation"],
                resources: [imageBuilderArn("image")],
            })
        );
    }
    // Managed (AWS-owned) components a recipe references.
    fn.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["imagebuilder:GetComponent"],
            resources: [
                stack.formatArn({
                    service: "imagebuilder",
                    account: "aws",
                    resource: "component",
                    resourceName: "*",
                    arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME,
                }),
            ],
        })
    );
    // List and describe calls take no resource.
    fn.addToRolePolicy(
        new iam.PolicyStatement({
            actions: [
                "imagebuilder:ListComponents",
                "imagebuilder:ListImageRecipes",
                "imagebuilder:ListInfrastructureConfigurations",
                "imagebuilder:ListDistributionConfigurations",
                "imagebuilder:ListImagePipelines",
                "imagebuilder:ListImages",
                "ec2:DescribeImages",
                "ec2:DescribeInstances",
                "ec2:DescribeInstanceTypes",
                "ec2:DescribeSnapshots",
            ],
            resources: ["*"],
        })
    );
    // AMI and snapshot tagging and deletion are bound to this deployment's `vams:deployment` tag value,
    // so the grants never reach another deployment's images. The tag write is limited to the keys VAMS
    // writes; an AMI already carries the deployment tag from its distribution configuration, while a
    // snapshot is first tagged by the build's completion.
    const amiTagRequest = {
        StringEquals: { [`aws:RequestTag/${EDH_AMI_DEPLOYMENT_TAG}`]: resources.prefix },
        "ForAllValues:StringEquals": {
            "aws:TagKeys": [
                ...EDH_AMI_VAMS_TAG_KEYS,
                `${EDH_VISIBILITY_TAG_PREFIX}${edh.edhClusterId}`,
            ],
        },
    };
    fn.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["ec2:CreateTags"],
            resources: [ec2Arn("image")],
            conditions: {
                ...amiTagRequest,
                StringEquals: {
                    ...amiTagRequest.StringEquals,
                    [`aws:ResourceTag/${EDH_AMI_DEPLOYMENT_TAG}`]: resources.prefix,
                },
            },
        })
    );
    fn.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["ec2:CreateTags"],
            resources: [ec2Arn("snapshot")],
            conditions: amiTagRequest,
        })
    );
    if (deletes) {
        fn.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["ec2:DeregisterImage", "ec2:DeleteSnapshot"],
                resources: [ec2Arn("image"), ec2Arn("snapshot")],
                conditions: {
                    StringEquals: {
                        [`aws:ResourceTag/${EDH_AMI_DEPLOYMENT_TAG}`]: resources.prefix,
                    },
                },
            })
        );
    }
    // Public AMI parameters (`ssm:/aws/service/...`), which carry no account in their ARN.
    fn.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["ssm:GetParameter"],
            resources: [
                stack.formatArn({
                    service: "ssm",
                    account: "",
                    resource: "parameter",
                    resourceName: "aws/service/*",
                    arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME,
                }),
            ],
        })
    );
    fn.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["iam:PassRole"],
            resources: [resources.instanceRole.roleArn],
            conditions: { StringEquals: { "iam:PassedToService": Service("EC2").PrincipalString } },
        })
    );
    // Image Builder creates its service-linked role on first use.
    fn.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["iam:CreateServiceLinkedRole"],
            resources: [
                stack.formatArn({
                    service: "iam",
                    region: "",
                    resource: "role",
                    resourceName: "aws-service-role/imagebuilder.amazonaws.com/*",
                    arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME,
                }),
            ],
            conditions: { StringEquals: { "iam:AWSServiceName": "imagebuilder.amazonaws.com" } },
        })
    );

    NagSuppressions.addResourceSuppressions(
        fn,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: "Image Builder objects are created at runtime from recipe content and named with the deployment prefix, so the grant is scoped to that prefix; list and describe calls take no resource ARN; AMI and snapshot tagging and deletion are conditioned on this deployment's vams:deployment tag value (a snapshot's first tag write is conditioned on the request carrying it); public AMI parameters live under /aws/service; the service-linked role name is fixed by Image Builder.",
                appliesTo: [
                    {
                        regex: "/^Resource::arn:.*:imagebuilder:.*:.*:(component|image-recipe|image-pipeline|image|infrastructure-configuration|distribution-configuration)\\/.*\\*$/g",
                    },
                    {
                        regex: "/^Resource::arn:.*:ec2:.*::(image|snapshot)\\/\\*$/g",
                    },
                    {
                        regex: "/^Resource::arn:.*:ssm:.*::parameter\\/aws\\/service\\/\\*$/g",
                    },
                    {
                        regex: "/^Resource::arn:.*:iam::.*:role\\/aws-service-role\\/imagebuilder\\.amazonaws\\.com\\/\\*$/g",
                    },
                    { regex: "/^Resource::\\*$/g" },
                ],
            },
        ],
        true
    );
}
