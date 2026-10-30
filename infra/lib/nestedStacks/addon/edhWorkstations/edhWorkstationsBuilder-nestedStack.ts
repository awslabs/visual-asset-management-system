/*
 * EDH Workstations add-on nested stack.
 *
 * Builds the Lambdas that proxy VAMS requests to an EDH cluster's REST API from the VAMS VPC and
 * registers their routes into the shared RouteRegistry.
 *
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as eventsTargets from "aws-cdk-lib/aws-events-targets";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cr from "aws-cdk-lib/custom-resources";
import * as crypto from "crypto";
import { Service } from "../../../helper/service-helper";
import { RouteRegistry, attachFunctionToApi } from "../../apiLambda/apiRouteRegistry";
import * as Config from "../../../../config/config";
import { ADDON_RESOURCE_PARAM_KEYS } from "../../../../common/addonResourceParamKeys";
import { addonDynamoDbTableDefaults, publishAddonResourceName } from "../addonStackHelpers";
import {
    EdhLambdaNetwork,
    EdhLambdaSecrets,
    EdhSessionBootstrapResources,
    EdhTables,
    applyEdhAmiBuildEnvironmentAndPermissions,
    applySessionBootstrapEnvironmentAndPermissions,
    buildEdhAdminServiceFunction,
    buildEdhAmiBuildRegistrarFunction,
    buildEdhAmiRecipeServiceFunction,
    buildEdhCatalogRegistrationFunction,
    buildEdhContainerServiceFunction,
    buildEdhLaunchWorkerFunction,
    buildEdhSessionReconcilerFunction,
    buildEdhWorkstationServiceFunction,
} from "./lambdaBuilder/edhWorkstationsFunctions";
import {
    EDH_AMI_RECIPES_ARTEFACTS_NAG_RESOURCE,
    EDH_AMI_RECIPES_ARTEFACTS_PREFIX,
    EdhAmiBuildConstruct,
} from "./constructs/edhAmiBuild-construct";
import { EdhImageBuildConstruct } from "./constructs/edhImageBuild-construct";
import { NagSuppressions } from "cdk-nag";

import { NAG_REASON_LAMBDA_BASIC_EXECUTION } from "../../../helper/security";

export interface EdhWorkstationsBuilderNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    /** Subnets with egress; the AMI build instance runs here by default. */
    privateSubnets: ec2.ISubnet[];
    isolatedSubnets: ec2.ISubnet[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    registry: RouteRegistry;
    apiKeyServiceFunction: lambda.IFunction;
}

const defaultProps: Partial<EdhWorkstationsBuilderNestedStackProps> = {};

/** SSM key suffix, under `resourceNamesSSMParamPrefix`, publishing the add-on Lambda security group id. */
export const EDH_ADDON_LAMBDA_SECURITY_GROUP_PARAM_KEY = "securityGroups/edhAddonLambda";

export class EdhWorkstationsBuilderNestedStack extends NestedStack {
    /** Attached to every add-on Lambda; the group an EDH ALB ingress rule names. */
    public readonly lambdaSecurityGroup: ec2.SecurityGroup;

    constructor(parent: Construct, name: string, props: EdhWorkstationsBuilderNestedStackProps) {
        super(parent, name);
        props = { ...defaultProps, ...props };

        const edhConfig = props.config.app.addons.useEdhWorkstations;

        // One security group for every add-on Lambda, so the EDH ALB's ingress rule can name a single
        // group. Egress is HTTPS only: every AWS endpoint the handlers call is on 443, and the EDH ALB
        // is on 443 or on the port the configured endpoint names.
        this.lambdaSecurityGroup = new ec2.SecurityGroup(this, "EdhAddonLambdaSecurityGroup", {
            vpc: props.vpc,
            allowAllOutbound: false,
            description: "EDH Workstations add-on Lambda security group",
        });
        this.lambdaSecurityGroup.addEgressRule(
            ec2.Peer.anyIpv4(),
            ec2.Port.tcp(443),
            "Allow HTTPS to AWS service endpoints and to the EDH ALB on 443"
        );
        const edhPort = Number(new URL(edhConfig.edhApiEndpoint).port || 443);
        if (edhPort !== 443) {
            this.lambdaSecurityGroup.addEgressRule(
                ec2.Peer.anyIpv4(),
                ec2.Port.tcp(edhPort),
                "Allow HTTPS to the EDH ALB on its configured port"
            );
        }

        // Operator-managed secrets, imported by complete ARN: the EDH root key, and optionally the PEM
        // bundle of a private CA that signs the EDH ALB certificate. Importing rather than creating
        // keeps an administrative credential out of every VAMS template and custom resource.
        const rootKeySecret = secretsmanager.Secret.fromSecretCompleteArn(
            this,
            "EdhRootKeySecret",
            edhConfig.edhAdminApiKeySecretArn
        );
        const caBundleSecret =
            edhConfig.edhApiCaBundleSecretArn && edhConfig.edhApiCaBundleSecretArn.length > 0
                ? secretsmanager.Secret.fromSecretCompleteArn(
                      this,
                      "EdhApiCaBundleSecret",
                      edhConfig.edhApiCaBundleSecretArn
                  )
                : undefined;

        const network: EdhLambdaNetwork = {
            vpc: props.vpc,
            subnets: props.isolatedSubnets,
            securityGroup: this.lambdaSecurityGroup,
        };
        const secrets: EdhLambdaSecrets = { rootKeySecret, caBundleSecret };

        // Session-secret encryption key. Per-session secrets hold a VAMS API key and are read by
        // EDH-owned instance roles, so the key is dedicated to that purpose: the workstation roles
        // get kms:Decrypt for calls made through Secrets Manager against secrets under the configured
        // prefix, and nothing else is encrypted under it.
        const sessionBootstrapConfig = edhConfig.sessionBootstrap;
        let sessionSecretsKey: kms.Key | undefined;
        if (sessionBootstrapConfig.enabled) {
            sessionSecretsKey = new kms.Key(this, "EdhSessionSecretsKey", {
                description: "VAMS EDH workstation session secrets",
                enableKeyRotation: true,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            });
            sessionSecretsKey.addToResourcePolicy(
                new iam.PolicyStatement({
                    sid: "WorkstationInstanceRolesDecryptViaSecretsManager",
                    effect: iam.Effect.ALLOW,
                    principals: sessionBootstrapConfig.workstationInstanceRoleArns.map(
                        (arn: string) => new iam.ArnPrincipal(arn)
                    ),
                    actions: ["kms:Decrypt", "kms:DescribeKey"],
                    resources: ["*"],
                    conditions: {
                        StringEquals: {
                            "kms:ViaService": Service("SECRETSMANAGER", false).Endpoint,
                        },
                        ArnLike: {
                            "kms:EncryptionContext:SecretARN": Service("SECRETSMANAGER").ARN(
                                `secret:${sessionBootstrapConfig.secretNamePrefix}*`
                            ),
                        },
                    },
                })
            );
        }
        const sessionBootstrapResources: EdhSessionBootstrapResources = {
            apiKeyServiceFunction: props.apiKeyServiceFunction,
            sessionSecretsKey,
        };

        // The add-on tables take the properties the core tables share. The storage key is imported by
        // ARN, so a grant on a table adds to the grantee's policy and never to the storage key policy.
        const tableKmsKey = props.storageResources.encryption.kmsKey
            ? kms.Key.fromKeyArn(
                  this,
                  "EdhTablesKmsKeyRef",
                  props.storageResources.encryption.kmsKey.keyArn
              )
            : undefined;
        const tableDefaults = addonDynamoDbTableDefaults(tableKmsKey);

        // EDH workstation sessions — one row per launch, keyed by the client-generated
        // bootstrapId so a repeated create resolves to the same row. sessionUuidIndex looks
        // a row up by its EDH session id; userIdIndex lists one user's sessions by
        // createdAt. No stream (the Garnet indexers route stream events by table-name
        // substring and must never see this table). A terminal row whose credential is
        // gone carries expiresAt (epoch seconds), on which DynamoDB TTL removes it.
        const sessionTable = new dynamodb.Table(this, "EdhSessionStorageTable", {
            ...tableDefaults,
            partitionKey: {
                name: "bootstrapId",
                type: dynamodb.AttributeType.STRING,
            },
            timeToLiveAttribute: "expiresAt",
        });
        sessionTable.addGlobalSecondaryIndex({
            indexName: "sessionUuidIndex",
            partitionKey: {
                name: "sessionId",
                type: dynamodb.AttributeType.STRING,
            },
            projectionType: dynamodb.ProjectionType.ALL,
        });
        sessionTable.addGlobalSecondaryIndex({
            indexName: "userIdIndex",
            partitionKey: {
                name: "userId",
                type: dynamodb.AttributeType.STRING,
            },
            sortKey: {
                name: "createdAt",
                type: dynamodb.AttributeType.STRING,
            },
            projectionType: dynamodb.ProjectionType.ALL,
        });

        // EDH AMI recipes and their builds: one partition per recipe (pk RECIPE#<recipeId>), the recipe
        // row under sk RECIPE and one row per build under sk BUILD#<version>. No stream, no TTL.
        const amiRecipeTable = new dynamodb.Table(this, "EdhAmiRecipeStorageTable", {
            ...tableDefaults,
            partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
            sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
        });
        amiRecipeTable.addGlobalSecondaryIndex({
            indexName: "imageBuildArnIndex",
            partitionKey: { name: "imageBuildVersionArn", type: dynamodb.AttributeType.STRING },
            projectionType: dynamodb.ProjectionType.ALL,
        });
        const tables: EdhTables = { sessionTable, amiRecipeTable };

        // The session and AMI recipe table names, published under the add-on resource-name keys.
        // The handlers resolve them from SSM when they load, and the catalog registration runs during
        // the deploy, so every Lambda listed in tableNameReaders is created after both parameters.
        const tableNameParameters = [
            publishAddonResourceName(
                this,
                props.config,
                ADDON_RESOURCE_PARAM_KEYS.dynamoTables.edhSessionStorage,
                sessionTable.tableName
            ),
            publishAddonResourceName(
                this,
                props.config,
                ADDON_RESOURCE_PARAM_KEYS.dynamoTables.edhAmiRecipeStorage,
                amiRecipeTable.tableName
            ),
        ];
        const tableNameReaders: lambda.Function[] = [];

        // Workstation service Lambda — backs the /addon/edh/workstations* and /addon/edh/catalog/* routes.
        const workstationServiceFunction = buildEdhWorkstationServiceFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            tables,
            props.config,
            network,
            secrets
        );

        // Launch worker Lambda — runs the EDH calls of a launch the workstation service hands over.
        const launchWorkerFunction = buildEdhLaunchWorkerFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            tables,
            props.config,
            network,
            secrets
        );
        tableNameReaders.push(workstationServiceFunction, launchWorkerFunction);
        workstationServiceFunction.addEnvironment(
            "EDH_LAUNCH_WORKER_FUNCTION_NAME",
            launchWorkerFunction.functionName
        );
        launchWorkerFunction.grantInvoke(workstationServiceFunction);
        // The service mints the session credential; the worker binds its secret to the desktop EDH
        // placed it on. Both carry the bootstrap wiring.
        applySessionBootstrapEnvironmentAndPermissions(
            workstationServiceFunction,
            props.config,
            sessionBootstrapResources
        );
        applySessionBootstrapEnvironmentAndPermissions(
            launchWorkerFunction,
            props.config,
            sessionBootstrapResources
        );

        // Session reconciler Lambda — the scheduled sweep that settles rows for desktops EDH no longer
        // lists and for launches that never completed, and revokes the credentials of desktops that
        // are no longer running. It keeps the revocation grants with session bootstrap off, so the
        // keys and secrets a deployment minted before turning the feature off are still taken back.
        const reconcilerFunction = buildEdhSessionReconcilerFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            tables,
            props.config,
            network,
            secrets
        );
        tableNameReaders.push(reconcilerFunction);
        applySessionBootstrapEnvironmentAndPermissions(
            reconcilerFunction,
            props.config,
            sessionBootstrapResources,
            { revokeWithoutBootstrap: true }
        );
        new events.Rule(this, "EdhSessionReconcilerSchedule", {
            description:
                "Reconciles EDH workstation session rows with EDH and revokes the credentials of desktops that are no longer running",
            schedule: events.Schedule.rate(cdk.Duration.minutes(15)),
            targets: [new eventsTargets.LambdaFunction(reconcilerFunction)],
        });

        // One registration per method+path pair; the strings are the EDH_ROUTES of addonApiRoutes.py.
        const workstationRoutes: Array<{ routePath: string; methods: apigwv2.HttpMethod[] }> = [
            {
                routePath: "/addon/edh/workstations",
                methods: [apigwv2.HttpMethod.GET, apigwv2.HttpMethod.POST],
            },
            {
                routePath: "/addon/edh/workstations/{sessionId}",
                methods: [apigwv2.HttpMethod.GET, apigwv2.HttpMethod.DELETE],
            },
            {
                routePath: "/addon/edh/workstations/{sessionId}/stop",
                methods: [apigwv2.HttpMethod.POST],
            },
            {
                routePath: "/addon/edh/workstations/{sessionId}/start",
                methods: [apigwv2.HttpMethod.POST],
            },
            {
                routePath: "/addon/edh/workstations/{sessionId}/renew",
                methods: [apigwv2.HttpMethod.POST],
            },
            {
                routePath: "/addon/edh/workstations/{sessionId}/connect",
                methods: [apigwv2.HttpMethod.GET],
            },
            {
                routePath: "/addon/edh/workstations/{sessionId}/os-sign-in",
                methods: [apigwv2.HttpMethod.GET],
            },
            { routePath: "/addon/edh/catalog/images", methods: [apigwv2.HttpMethod.GET] },
            { routePath: "/addon/edh/catalog/instance-types", methods: [apigwv2.HttpMethod.GET] },
            { routePath: "/addon/edh/catalog/lease", methods: [apigwv2.HttpMethod.GET] },
            {
                routePath: "/addon/edh/catalog/capacity-reservations",
                methods: [apigwv2.HttpMethod.GET],
            },
        ];
        for (const route of workstationRoutes) {
            for (const method of route.methods) {
                attachFunctionToApi(this, workstationServiceFunction, {
                    routePath: route.routePath,
                    method,
                    registry: props.registry,
                });
            }
        }

        // Admin routes: one Lambda serves the image catalog administration, the cluster-wide
        // instance views and actions, capacity and health, the lease settings and the capacity
        // reservation registry. One registration per method+path pair; the strings are the admin
        // members of EDH_ROUTES in addonApiRoutes.py.
        const adminServiceFunction = buildEdhAdminServiceFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            tables,
            props.config,
            network,
            secrets
        );
        tableNameReaders.push(adminServiceFunction);
        // Admin stop and deactivate revoke the workstation's session credential inline, through the
        // same helper the user-facing paths use, so the admin Lambda carries the bootstrap wiring too.
        applySessionBootstrapEnvironmentAndPermissions(
            adminServiceFunction,
            props.config,
            sessionBootstrapResources
        );
        const adminRoutes: Array<{ routePath: string; method: apigwv2.HttpMethod }> = [
            { routePath: "/addon/edh/admin/images", method: apigwv2.HttpMethod.GET },
            { routePath: "/addon/edh/admin/images/vdi", method: apigwv2.HttpMethod.POST },
            { routePath: "/addon/edh/admin/images/simulation", method: apigwv2.HttpMethod.POST },
            {
                routePath: "/addon/edh/admin/images/vdi/{imageId}",
                method: apigwv2.HttpMethod.DELETE,
            },
            {
                routePath: "/addon/edh/admin/images/simulation/{imageId}",
                method: apigwv2.HttpMethod.DELETE,
            },
            { routePath: "/addon/edh/admin/instances", method: apigwv2.HttpMethod.GET },
            { routePath: "/addon/edh/admin/instances/stale", method: apigwv2.HttpMethod.GET },
            {
                routePath: "/addon/edh/admin/instances/{sessionId}/stop",
                method: apigwv2.HttpMethod.POST,
            },
            {
                routePath: "/addon/edh/admin/instances/{sessionId}/deactivate",
                method: apigwv2.HttpMethod.POST,
            },
            { routePath: "/addon/edh/admin/capacity", method: apigwv2.HttpMethod.GET },
            { routePath: "/addon/edh/admin/health", method: apigwv2.HttpMethod.GET },
            { routePath: "/addon/edh/admin/settings", method: apigwv2.HttpMethod.GET },
            { routePath: "/addon/edh/admin/settings", method: apigwv2.HttpMethod.PUT },
            // The capacity reservation registry: list and register, then update and unregister one.
            {
                routePath: "/addon/edh/admin/capacity-reservations",
                method: apigwv2.HttpMethod.GET,
            },
            {
                routePath: "/addon/edh/admin/capacity-reservations",
                method: apigwv2.HttpMethod.POST,
            },
            {
                routePath: "/addon/edh/admin/capacity-reservations/{capacityReservationId}",
                method: apigwv2.HttpMethod.PUT,
            },
            {
                routePath: "/addon/edh/admin/capacity-reservations/{capacityReservationId}",
                method: apigwv2.HttpMethod.DELETE,
            },
        ];
        for (const route of adminRoutes) {
            attachFunctionToApi(this, adminServiceFunction, {
                routePath: route.routePath,
                method: route.method,
                registry: props.registry,
            });
        }

        // Container jobs. The service Lambda, its routes and the image builds exist only when the
        // operator turns the containers feature on; the catalog registration below carries the
        // resolved job-definition list either way (empty while the feature is off).
        const containers = edhConfig.containers;
        const imageBuildTriggers: cdk.CustomResource[] = [];
        const imageUriByName = new Map<string, string>();
        const containerJobDefinitions: Array<Record<string, unknown>> = [];
        if (containers.enabled) {
            const containerServiceFunction = buildEdhContainerServiceFunction(
                this,
                props.lambdaCommonBaseLayer,
                props.storageResources,
                tables,
                props.config,
                network,
                secrets
            );
            // One registration per method+path pair; the strings are the container members of
            // EDH_ROUTES in addonApiRoutes.py.
            const containerRoutes: Array<{ routePath: string; method: apigwv2.HttpMethod }> = [
                { routePath: "/addon/edh/containers/catalog", method: apigwv2.HttpMethod.GET },
                { routePath: "/addon/edh/containers/jobs", method: apigwv2.HttpMethod.GET },
                { routePath: "/addon/edh/containers/jobs", method: apigwv2.HttpMethod.POST },
                {
                    routePath: "/addon/edh/containers/jobs/{jobId}",
                    method: apigwv2.HttpMethod.DELETE,
                },
            ];
            for (const route of containerRoutes) {
                attachFunctionToApi(this, containerServiceFunction, {
                    routePath: route.routePath,
                    method: route.method,
                    registry: props.registry,
                });
            }

            // One repository + CodeBuild project per declared image. The output names the project an
            // operator watches while the first build runs; CloudFormation keeps only the alphanumeric
            // characters of an output id, so two names that differ only by hyphens would collide.
            const outputKeys = new Set<string>();
            for (const image of containers.buildAndPushImages) {
                const build = new EdhImageBuildConstruct(this, `ImageBuild-${image.name}`, {
                    config: props.config,
                    imageName: image.name,
                    dockerfileDir: image.dockerfileDir,
                    platform: image.platform,
                    edhClusterId: edhConfig.edhClusterId,
                    imagePullRoleArns: containers.imagePullRoleArns,
                    kmsKey: props.storageResources.encryption.kmsKey,
                });
                imageBuildTriggers.push(build.buildTrigger);
                imageUriByName.set(image.name, build.imageUri);

                const outputKey = `EdhImageBuildProject-${image.name}`.replace(/[^A-Za-z0-9]/g, "");
                if (outputKeys.has(outputKey)) {
                    throw new Error(
                        `containers.buildAndPushImages: '${image.name}' collides with another image ` +
                            `on the output id ${outputKey}; rename one of them.`
                    );
                }
                outputKeys.add(outputKey);
                new cdk.CfnOutput(this, `EdhImageBuildProject-${image.name}`, {
                    value: build.codeBuildProjectName,
                    description: `AWS CodeBuild project that builds the EDH container image '${image.name}'`,
                });
            }
            for (const existing of containers.existingEcrImages) {
                imageUriByName.set(existing.imageUri, existing.imageUri);
            }
            // imageRef names a built image or an existing URI; getConfig() rejects anything else.
            for (const jobDefinition of containers.jobDefinitions) {
                containerJobDefinitions.push({
                    name: jobDefinition.name,
                    imageUri: imageUriByName.get(jobDefinition.imageRef) ?? jobDefinition.imageRef,
                    vcpus: jobDefinition.vcpus,
                    memory: jobDefinition.memory,
                    ...(jobDefinition.gpu !== undefined ? { gpu: jobDefinition.gpu } : {}),
                    ...(jobDefinition.platform !== undefined
                        ? { platform: jobDefinition.platform }
                        : {}),
                });
            }
        }

        // Deploy-time registration of the predefined images. The handler skips entries EDH already
        // has and reports per-item failures as a warnings attribute, so a registration failure never
        // fails the deploy. The provider framework Lambda stays outside the VPC; the handler it
        // invokes is the in-VPC function above.
        const registrationFunction = buildEdhCatalogRegistrationFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            tables,
            props.config,
            network,
            secrets
        );
        tableNameReaders.push(registrationFunction);

        // Job definitions are registered by this Lambda with AWS Batch: EDH offers no create route and
        // lists a definition by the edh:visibility resource tag, which the registration writes. The
        // listing action takes no resource ARN in IAM; the writes are scoped to the configured names
        // (getConfig() requires each to begin with `edh-`), every revision of each, since the
        // registration registers, tags and retires revisions of those names only.
        if (containers.enabled) {
            const jobDefinitionArns = containers.jobDefinitions.flatMap((jobDefinition) =>
                [jobDefinition.name, `${jobDefinition.name}:*`].map((resourceName) =>
                    cdk.Stack.of(this).formatArn({
                        service: "batch",
                        resource: "job-definition",
                        resourceName,
                    })
                )
            );
            if (jobDefinitionArns.length > 0) {
                registrationFunction.addToRolePolicy(
                    new iam.PolicyStatement({
                        actions: [
                            "batch:RegisterJobDefinition",
                            "batch:DeregisterJobDefinition",
                            "batch:TagResource",
                        ],
                        resources: jobDefinitionArns,
                    })
                );
            }
            registrationFunction.addToRolePolicy(
                new iam.PolicyStatement({
                    actions: ["batch:DescribeJobDefinitions"],
                    resources: ["*"],
                })
            );
            NagSuppressions.addResourceSuppressions(
                registrationFunction,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "The catalog registration Lambda registers the deployment's configured EDH job definitions with AWS Batch. batch:DescribeJobDefinitions accepts no resource ARN; the write actions name each configured job definition, with a wildcard only over the revision Batch assigns at registration.",
                        appliesTo: [
                            { regex: "/^Resource::arn:.*:batch:.*:job-definition\\/edh-.*:\\*$/g" },
                            { regex: "/^Resource::\\*$/g" },
                        ],
                    },
                ],
                true
            );
        }
        // AMI builds. The recipe service, the registrar, the build instance profile and security group
        // and the seeded recipe bundles exist only with amiBuilds on; the registration resource below
        // carries the bundle list either way (empty while the feature is off).
        const amiBuilds = edhConfig.amiBuilds;
        let amiBuild: EdhAmiBuildConstruct | undefined;
        if (amiBuilds.enabled) {
            amiBuild = new EdhAmiBuildConstruct(this, "EdhAmiBuild", {
                config: props.config,
                vpc: props.vpc,
                privateSubnets: props.privateSubnets,
                isolatedSubnets: props.isolatedSubnets,
                artefactsBucket: props.storageResources.s3.artefactsBucket,
            });
            const amiRecipeServiceFunction = buildEdhAmiRecipeServiceFunction(
                this,
                props.lambdaCommonBaseLayer,
                props.storageResources,
                tables,
                props.config,
                network,
                secrets
            );
            tableNameReaders.push(amiRecipeServiceFunction);
            applyEdhAmiBuildEnvironmentAndPermissions(
                amiRecipeServiceFunction,
                props.config,
                amiBuild.resources
            );
            // One registration per method+path pair; the strings are the AMI recipe members of
            // EDH_ROUTES in addonApiRoutes.py.
            const amiRecipeRoutes: Array<{ routePath: string; method: apigwv2.HttpMethod }> = [
                { routePath: "/addon/edh/admin/ami-recipes", method: apigwv2.HttpMethod.GET },
                { routePath: "/addon/edh/admin/ami-recipes", method: apigwv2.HttpMethod.POST },
                {
                    routePath: "/addon/edh/admin/ami-recipes/{recipeId}",
                    method: apigwv2.HttpMethod.GET,
                },
                {
                    routePath: "/addon/edh/admin/ami-recipes/{recipeId}",
                    method: apigwv2.HttpMethod.PUT,
                },
                {
                    routePath: "/addon/edh/admin/ami-recipes/{recipeId}",
                    method: apigwv2.HttpMethod.DELETE,
                },
                {
                    routePath: "/addon/edh/admin/ami-recipes/{recipeId}/builds",
                    method: apigwv2.HttpMethod.GET,
                },
                {
                    routePath: "/addon/edh/admin/ami-recipes/{recipeId}/builds",
                    method: apigwv2.HttpMethod.POST,
                },
                {
                    routePath: "/addon/edh/admin/ami-recipes/{recipeId}/builds/{version}",
                    method: apigwv2.HttpMethod.DELETE,
                },
                {
                    routePath: "/addon/edh/admin/ami-recipes/{recipeId}/builds/{version}/register",
                    method: apigwv2.HttpMethod.POST,
                },
            ];
            for (const route of amiRecipeRoutes) {
                attachFunctionToApi(this, amiRecipeServiceFunction, {
                    routePath: route.routePath,
                    method: route.method,
                    registry: props.registry,
                });
            }

            const registrarFunction = buildEdhAmiBuildRegistrarFunction(
                this,
                props.lambdaCommonBaseLayer,
                props.storageResources,
                tables,
                props.config,
                network,
                secrets
            );
            tableNameReaders.push(registrarFunction);
            applyEdhAmiBuildEnvironmentAndPermissions(
                registrarFunction,
                props.config,
                amiBuild.resources,
                { cancelsBuilds: true }
            );
            new events.Rule(this, "EdhAmiBuildStateRule", {
                description:
                    "Completes an EDH AMI recipe build when Image Builder reports its image available or failed",
                // Only this deployment's images: the event carries the image build version ARN, whose
                // image name begins with the deployment prefix.
                eventPattern: {
                    source: ["aws.imagebuilder"],
                    detailType: ["EC2 Image Builder Image State Change"],
                    resources: events.Match.prefix(
                        cdk.Stack.of(this).formatArn({
                            service: "imagebuilder",
                            resource: "image",
                            resourceName: `${amiBuild.resources.prefix}-`,
                            arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME,
                        })
                    ),
                },
                targets: [new eventsTargets.LambdaFunction(registrarFunction)],
            });
            new events.Rule(this, "EdhAmiBuildSweepSchedule", {
                description:
                    "Closes EDH AMI builds whose completion event was missed, retries failed registrations and starts queued builds",
                schedule: events.Schedule.rate(cdk.Duration.minutes(30)),
                targets: [
                    new eventsTargets.LambdaFunction(registrarFunction, {
                        event: events.RuleTargetInput.fromObject({ sweep: true }),
                    }),
                ],
            });

            // The registration resource seeds the recipes and starts buildOnDeploy builds.
            amiRecipeTable.grantReadWriteData(registrationFunction);
            props.storageResources.s3.artefactsBucket.grantRead(
                registrationFunction,
                `${EDH_AMI_RECIPES_ARTEFACTS_PREFIX}/*`
            );
            NagSuppressions.addResourceSuppressions(
                registrationFunction,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "The catalog registration Lambda reads back the recipe directories the deployment uploaded. Their keys carry the bundle hash and the recipe id, neither of which is a fixed string, so the grant is scoped to the addon/edh/amiRecipes prefix of the artefacts bucket.",
                        appliesTo: [EDH_AMI_RECIPES_ARTEFACTS_NAG_RESOURCE],
                    },
                ],
                true
            );
            applyEdhAmiBuildEnvironmentAndPermissions(
                registrationFunction,
                props.config,
                amiBuild.resources,
                { deletes: false }
            );
        }

        for (const reader of tableNameReaders) {
            reader.node.addDependency(...tableNameParameters);
        }

        const registrationProvider = new cr.Provider(this, "EdhCatalogRegistrationProvider", {
            onEventHandler: registrationFunction,
        });

        const predefinedImages = edhConfig.predefinedImages;
        // A content hash of the lists plus the operator-bumped revision re-runs the resource when
        // either changes; Timestamp re-runs it on every deploy so a warned item is retried.
        const registrationContentHash = crypto
            .createHash("sha256")
            .update(JSON.stringify(predefinedImages.vdi))
            .update(JSON.stringify(predefinedImages.simulationAmis))
            .update(String(predefinedImages.registrationRevision))
            .update(JSON.stringify(edhConfig.containers.jobDefinitions))
            .update(amiBuild?.bundleHash ?? "")
            .digest("hex");
        const registration = new cdk.CustomResource(this, "EdhCatalogRegistration", {
            serviceToken: registrationProvider.serviceToken,
            properties: {
                predefinedVdiImages: JSON.stringify(predefinedImages.vdi),
                predefinedSimulationAmis: JSON.stringify(predefinedImages.simulationAmis),
                // Resolved image URIs carry repository tokens, so the list is serialized by the stack.
                jobDefinitions: cdk.Stack.of(this).toJsonString(containerJobDefinitions),
                amiBuildsEnabled: amiBuilds.enabled ? "true" : "false",
                amiRecipeBundles: JSON.stringify(amiBuild?.bundles ?? []),
                leasePolicyDefaults: JSON.stringify(edhConfig.vdi.lease),
                contentHash: registrationContentHash,
                registrationRevision: String(predefinedImages.registrationRevision),
                Timestamp: Date.now().toString(),
            },
        });

        new cdk.CfnOutput(this, "EdhCatalogRegistrationWarnings", {
            value: registration.getAttString("warnings"),
            description:
                "Predefined EDH catalog registrations that failed on the last deploy ('none' when every entry registered or was already present)",
        });

        // A job definition names an image tag a build pushes, so the registration follows every build
        // trigger: it runs once each build has STARTED, and EDH records the definition while the image
        // is still being pushed. A job submitted in that window fails to pull until the push lands.
        for (const trigger of imageBuildTriggers) {
            registration.node.addDependency(trigger);
        }

        // The registration reads each recipe directory back out of the artefacts bucket, so it runs
        // once every bundle upload has completed.
        for (const deployment of amiBuild?.deployments ?? []) {
            registration.node.addDependency(deployment);
        }

        // The group id an operator references from the EDH ALB security group (same VPC or peered).
        new cdk.CfnOutput(this, "EdhAddonLambdaSecurityGroupId", {
            value: this.lambdaSecurityGroup.securityGroupId,
            description: `Security group of the EDH Workstations add-on Lambdas; allow it on ${edhPort} in the EDH ALB security group`,
        });

        // Written here rather than through the resource-name registry because this stack builds after
        // the ResourceNames stack materializes the registry.
        new ssm.StringParameter(this, "ResourceNameParamEdhAddonLambdaSecurityGroup", {
            parameterName: `${props.config.resourceNamesSSMParamPrefix}/${EDH_ADDON_LAMBDA_SECURITY_GROUP_PARAM_KEY}`,
            stringValue: this.lambdaSecurityGroup.securityGroupId,
        });

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: NAG_REASON_LAMBDA_BASIC_EXECUTION,
                    appliesTo: [{ regex: "/.*AWSLambdaBasicExecutionRole$/g" }],
                },
            ],
            true
        );
    }
}
