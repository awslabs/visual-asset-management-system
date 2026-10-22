/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Configuration of the EDH Workstations add-on, `app.addons.useEdhWorkstations`: its interfaces, the
 * defaults `getConfig()` fills in, its validation, and the AMI recipe loader the add-on stack reads
 * recipes with. `addonConfig.ts` calls these from `getConfig()`.
 */
import { existsSync, readFileSync, statSync } from "fs";
import { isAbsolute, join } from "path";
import type { Config } from "../config";

// ============================================================================================
// Constants that shape the deployment
//
// Every value here reaches synthesized infrastructure, through the recipes the add-on stack seeds.
// ============================================================================================

/** The recipe directories shipped with VAMS; seeded when `amiBuilds.includeShippedRecipes` is on. */
export const EDH_SHIPPED_AMI_RECIPE_DIRS = [
    "backendAddons/edh/amiRecipes/al2023-desktop",
    "backendAddons/edh/amiRecipes/windows2022-desktop",
    "backendAddons/edh/amiRecipes/isaacsim",
];

// ============================================================================================
// Constants used only to check configuration
//
// Nothing here reaches synthesized infrastructure. Each one exists so getConfig() can reject a bad
// add-on configuration at synth, where the error names the offending field.
// ============================================================================================

/** The characters and length a resource tag key allows, less the `edh:visibility:` prefix. */
const EDH_CLUSTER_ID_PATTERN = /^[A-Za-z0-9 _.:/=+@-]{1,113}$/;
// The longest sessionBootstrap.secretNamePrefix accepted. The shipped Linux recipes' session bootstrap
// component resolves the prefix into a document the 16,000-character component limit applies to.
export const EDH_SESSION_SECRET_PREFIX_MAX_LENGTH = 128;
// A DNS name: labels of letters, digits and hyphens, at most 253 characters in all.
const EDH_TLS_SERVER_NAME_PATTERN =
    /^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$/;

const EDH_AMI_ID_PATTERN = /^ami-[0-9a-f]{8,17}$/;
// Becomes part of an ECR repository name, which is lowercase.
const EDH_IMAGE_NAME_PATTERN = /^[a-z0-9][a-z0-9-]{0,62}$/;
const EDH_BUILD_PLATFORMS = ["linux/amd64", "linux/arm64"];
const EDH_JOB_PLATFORMS = ["EC2", "FARGATE"];
// EDH's controller role may submit jobs only to job definitions and job queues named with this prefix.
const EDH_BATCH_NAME_PREFIX = "edh-";
const EDH_ECR_IMAGE_URI_PATTERN =
    /^\d{12}\.dkr\.ecr\.[a-z0-9-]+\.[a-z0-9.-]+\/[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9_.-]{1,128}|@sha256:[0-9a-f]{64})?$/;
// The angle-bracket tokens the shipped templates use for container image URIs.
const EDH_TEMPLATE_PLACEHOLDERS = [
    "<ACCOUNTID>",
    "<REGION>",
    "<ECR-REPOSITORY>",
    "<IMAGE-ID>",
    "<IMAGE-TAG>",
];

const EDH_RECIPE_ID_PATTERN = /^[a-z0-9][a-z0-9-]{1,39}$/;
const EDH_SUBNET_ID_PATTERN = /^subnet-[0-9a-f]{8,17}$/;
const EDH_AMI_RECIPE_KINDS = ["vdi", "simulation"];
const EDH_AMI_ARCHITECTURES = ["x86_64", "arm64"];
// EDH 26.8.0 DCV_BASE_OS keys EDH shows in its own catalog form (centos7 is hidden there).
export const EDH_DCV_BASE_OS_KEYS = [
    "amazonlinux2",
    "amazonlinux2023",
    "rhel7",
    "rhel8",
    "rhel9",
    "rocky8",
    "rocky9",
    "ubuntu2204",
    "ubuntu2404",
    "windows2019",
    "windows2022",
    "windows2025",
];
const EDH_PARENT_IMAGE_PATTERN =
    /^(ami-[0-9a-f]{8,17}|ssm:\/aws\/service\/[A-Za-z0-9/_.-]{1,1000}|edhStack:[A-Za-z0-9\-._ ]{1,256})$/;
const EDH_COMPONENT_ARN_PATTERN =
    /^arn:(aws|aws-us-gov|\$\{AWS::Partition\}):imagebuilder:([a-z0-9-]+|\$\{AWS::Region\}):(aws|[0-9]{12}):component\/[a-z0-9-]+\/([0-9]+|x)\.([0-9]+|x)\.([0-9]+|x)(\/[0-9]+)?$/;
const EDH_ASSET_ID_PATTERN = /^[a-z][a-z0-9-]{0,31}$/;
const EDH_PARAMETER_KEY_PATTERN = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const EDH_AMI_RECIPE_MAX_ASSETS = 8;
const EDH_AMI_RECIPE_MAX_PARAMETERS = 16;
const EDH_AMI_RECIPE_PARAMETER_VALUE_MAX = 1024;
// A recipe names one build instance type, or a list Image Builder chooses among by availability.
const EDH_AMI_RECIPE_MAX_BUILD_INSTANCE_TYPES = 8;
const EDH_AMI_RECIPE_BUILD_INSTANCE_TYPE_MAX_LENGTH = 64;
// The instance types a desktop of a recipe's image may launch on, each spelled as a build type is.
const EDH_AMI_RECIPE_MAX_LAUNCH_INSTANCE_TYPES = 64;
// Repository subtrees a recipe may bundle as a build asset. Only source a build legitimately
// installs belongs here; widening this widens what a recipe uploads to the artefacts bucket.
const EDH_ASSET_REPO_PATH_PREFIXES = ["tools/"];
// An EC2 instance type, family.size; both segments may carry hyphens (u-6tb1.56xlarge, c7i.metal-24xl).
const EDH_INSTANCE_TYPE_PATTERN = /^[a-z0-9-]+\.[a-z0-9-]+$/;

export interface ConfigEdhPredefinedVdiImage {
    amiId: string;
    stackName: string;
    baseOs: string;
    rootSize: number;
    virtualDesktopProfileId: number;
    launchTenancy?: string;
    description?: string;
}

export interface ConfigEdhSimulationAmi {
    amiId: string;
    stackName: string;
    rootSize: number;
    targetNodeProfileId: number;
    // EDH user-data template the stack boots with; EDH's protected default template is 1.
    targetNodeUserDataId?: number;
    userDataVariables?: string;
    description?: string;
}

export interface ConfigEdhBuildImage {
    name: string;
    // Directory holding the Dockerfile, relative to the repository root.
    dockerfileDir: string;
    platform?: string;
}

export interface ConfigEdhExistingEcrImage {
    imageUri: string;
    displayName: string;
}

export interface ConfigEdhJobDefinition {
    name: string;
    // A containers.buildAndPushImages[].name or a containers.existingEcrImages[].imageUri.
    imageRef: string;
    vcpus: number;
    memory: number;
    gpu?: number;
    platform?: string;
}

export interface ConfigEdhAmiBuilds {
    enabled: boolean;
    // Seed the recipe directories shipped under backendAddons/edh/amiRecipes as config recipes.
    includeShippedRecipes: boolean;
    // Subnet the Image Builder build instance launches in; empty selects the first VAMS private subnet.
    buildSubnetId: string;
    buildTimeoutMinutes: number;
    keepVersionsDefault: number;
}

export interface ConfigEdhAmiRecipeRef {
    // Directory holding recipe.json and components/, relative to the repository root.
    recipeDir: string;
    buildOnDeploy: boolean;
}

export interface EdhAmiRecipeManifest {
    recipeId: string;
    displayName: string;
    kind: "vdi" | "simulation";
    osFamily: "linux" | "windows";
    baseOs?: string;
    architecture: "x86_64" | "arm64";
    parentImage: string;
    // One instance type, or a list Image Builder chooses among by availability.
    buildInstanceType: string | string[];
    // The instance types a desktop of the recipe's image may launch on; absent, null or empty is no
    // restriction of the recipe's own. Outside the content hash, so it never provokes a build.
    launchInstanceTypes?: string[] | null;
    rootSizeGb: number;
    stackName: string;
    profileId: number;
    components: Array<{ arn?: string; file?: string; name?: string; data?: string }>;
    // Repository directory trees uploaded beside the recipe bundle; a component reaches one as
    // `${ASSET_<id>}`, substituted to its s3:// prefix when the build is materialized.
    assets?: Array<{ id: string; repoPath: string }>;
    // Flat string map a component reaches as `${PARAM_<key>}`.
    parameters?: Record<string, string>;
    keepVersions?: number;
    recipeRevision?: number;
    description?: string;
}

export interface LoadedEdhAmiRecipe {
    recipeDir: string;
    recipeId: string;
    manifest: EdhAmiRecipeManifest;
    // Repository-relative component files, in the manifest's order.
    componentFiles: string[];
    // Declared assets, repository-relative and POSIX-separated; empty when the manifest declares none.
    assets: Array<{ id: string; repoPath: string }>;
    buildOnDeploy: boolean;
}

export interface ConfigEdhWorkstations {
    enabled: boolean;
    edhApiEndpoint: string;
    edhAdminApiKeySecretArn: string;
    edhApiCaBundleSecretArn: string;
    edhApiTlsServerName: string;
    edhClusterId: string;
    autoProvisionUsers: boolean;
    edhAllowedInstanceTypes: string[];
    vdi: {
        defaultProject: string;
        virtualDesktopProfileId: number;
        lease: {
            enabled: boolean;
            leaseHours: number;
            forced: boolean;
            graceHours: number;
        };
    };
    sessionBootstrap: {
        enabled: boolean;
        sessionKeyTtlMinutes: number;
        secretNamePrefix: string;
        workstationInstanceRoleArns: string[];
        launchManifest: boolean;
    };
    amiBuilds: ConfigEdhAmiBuilds;
    predefinedImages: {
        vdi: ConfigEdhPredefinedVdiImage[];
        simulationAmis: ConfigEdhSimulationAmi[];
        amiRecipes: ConfigEdhAmiRecipeRef[];
        registrationRevision: number;
    };
    containers: {
        enabled: boolean;
        buildAndPushImages: ConfigEdhBuildImage[];
        existingEcrImages: ConfigEdhExistingEcrImage[];
        jobDefinitions: ConfigEdhJobDefinition[];
        eksEnabled: boolean;
        imagePullRoleArns: string[];
    };
}

/**
 * Fills in the add-on block's defaults, field by field, so a config.json written before a field
 * existed still deploys. Refuses `vdi.enabled`, which is not a configuration field.
 */
export function applyEdhWorkstationsDefaults(config: Config): void {
    if (config.app.addons.useEdhWorkstations == undefined) {
        config.app.addons.useEdhWorkstations = {
            enabled: false,
            edhApiEndpoint: "",
            edhAdminApiKeySecretArn: "",
            edhApiCaBundleSecretArn: "",
            edhApiTlsServerName: "",
            edhClusterId: "",
            autoProvisionUsers: true,
            edhAllowedInstanceTypes: [],
            vdi: {
                defaultProject: "default",
                virtualDesktopProfileId: 0,
                lease: { enabled: false, leaseHours: 8, forced: false, graceHours: 24 },
            },
            sessionBootstrap: {
                enabled: false,
                sessionKeyTtlMinutes: 480,
                secretNamePrefix: "vams/edh-session/",
                workstationInstanceRoleArns: [],
                launchManifest: false,
            },
            amiBuilds: {
                enabled: false,
                includeShippedRecipes: true,
                buildSubnetId: "",
                buildTimeoutMinutes: 180,
                keepVersionsDefault: 3,
            },
            predefinedImages: {
                vdi: [],
                simulationAmis: [],
                amiRecipes: [],
                registrationRevision: 1,
            },
            containers: {
                enabled: false,
                buildAndPushImages: [],
                existingEcrImages: [],
                jobDefinitions: [],
                eksEnabled: false,
                imagePullRoleArns: [],
            },
        };
    }
    const edhBlock = config.app.addons.useEdhWorkstations;
    if (edhBlock.enabled == undefined) edhBlock.enabled = false;
    if (edhBlock.edhApiEndpoint == undefined) edhBlock.edhApiEndpoint = "";
    if (edhBlock.edhAdminApiKeySecretArn == undefined) edhBlock.edhAdminApiKeySecretArn = "";
    if (edhBlock.edhApiCaBundleSecretArn == undefined) edhBlock.edhApiCaBundleSecretArn = "";
    if (edhBlock.edhApiTlsServerName == undefined) edhBlock.edhApiTlsServerName = "";
    if (edhBlock.edhClusterId == undefined) edhBlock.edhClusterId = "";
    if (edhBlock.autoProvisionUsers == undefined) edhBlock.autoProvisionUsers = true;
    if (edhBlock.edhAllowedInstanceTypes == undefined) edhBlock.edhAllowedInstanceTypes = [];
    if (edhBlock.vdi == undefined) {
        edhBlock.vdi = {
            defaultProject: "default",
            virtualDesktopProfileId: 0,
            lease: { enabled: false, leaseHours: 8, forced: false, graceHours: 24 },
        };
    }
    // The desktop routes and the Workstations page deploy whenever the add-on is enabled; the vdi
    // block carries no switch of its own.
    if ((edhBlock.vdi as { enabled?: unknown }).enabled !== undefined) {
        throw new Error(
            "Configuration Error: app.addons.useEdhWorkstations.vdi.enabled is not a configuration " +
                "field. The desktop routes and the Workstations page deploy whenever " +
                "app.addons.useEdhWorkstations.enabled is true; remove the vdi.enabled key."
        );
    }
    if (edhBlock.vdi.defaultProject == undefined) edhBlock.vdi.defaultProject = "default";
    if (edhBlock.vdi.virtualDesktopProfileId == undefined) {
        edhBlock.vdi.virtualDesktopProfileId = 0;
    }
    if (edhBlock.vdi.lease == undefined) {
        edhBlock.vdi.lease = { enabled: false, leaseHours: 8, forced: false, graceHours: 24 };
    }
    if (edhBlock.vdi.lease.enabled == undefined) edhBlock.vdi.lease.enabled = false;
    if (edhBlock.vdi.lease.leaseHours == undefined) edhBlock.vdi.lease.leaseHours = 8;
    if (edhBlock.vdi.lease.forced == undefined) edhBlock.vdi.lease.forced = false;
    if (edhBlock.vdi.lease.graceHours == undefined) edhBlock.vdi.lease.graceHours = 24;
    if (edhBlock.sessionBootstrap == undefined) {
        edhBlock.sessionBootstrap = {
            enabled: false,
            sessionKeyTtlMinutes: 480,
            secretNamePrefix: "vams/edh-session/",
            workstationInstanceRoleArns: [],
            launchManifest: false,
        };
    }
    if (edhBlock.sessionBootstrap.enabled == undefined) edhBlock.sessionBootstrap.enabled = false;
    if (edhBlock.sessionBootstrap.sessionKeyTtlMinutes == undefined) {
        edhBlock.sessionBootstrap.sessionKeyTtlMinutes = 480;
    }
    if (edhBlock.sessionBootstrap.secretNamePrefix == undefined) {
        edhBlock.sessionBootstrap.secretNamePrefix = "vams/edh-session/";
    }
    if (edhBlock.sessionBootstrap.workstationInstanceRoleArns == undefined) {
        edhBlock.sessionBootstrap.workstationInstanceRoleArns = [];
    }
    if (edhBlock.sessionBootstrap.launchManifest == undefined) {
        edhBlock.sessionBootstrap.launchManifest = false;
    }
    if (edhBlock.predefinedImages == undefined) {
        edhBlock.predefinedImages = {
            vdi: [],
            simulationAmis: [],
            amiRecipes: [],
            registrationRevision: 1,
        };
    }
    if (edhBlock.predefinedImages.vdi == undefined) edhBlock.predefinedImages.vdi = [];
    if (edhBlock.predefinedImages.simulationAmis == undefined) {
        edhBlock.predefinedImages.simulationAmis = [];
    }
    if (edhBlock.predefinedImages.amiRecipes == undefined)
        edhBlock.predefinedImages.amiRecipes = [];
    if (edhBlock.predefinedImages.registrationRevision == undefined) {
        edhBlock.predefinedImages.registrationRevision = 1;
    }
    if (edhBlock.amiBuilds == undefined) {
        edhBlock.amiBuilds = {
            enabled: false,
            includeShippedRecipes: true,
            buildSubnetId: "",
            buildTimeoutMinutes: 180,
            keepVersionsDefault: 3,
        };
    }
    if (edhBlock.amiBuilds.enabled == undefined) edhBlock.amiBuilds.enabled = false;
    if (edhBlock.amiBuilds.includeShippedRecipes == undefined)
        edhBlock.amiBuilds.includeShippedRecipes = true;
    if (edhBlock.amiBuilds.buildSubnetId == undefined) edhBlock.amiBuilds.buildSubnetId = "";
    if (edhBlock.amiBuilds.buildTimeoutMinutes == undefined)
        edhBlock.amiBuilds.buildTimeoutMinutes = 180;
    if (edhBlock.amiBuilds.keepVersionsDefault == undefined)
        edhBlock.amiBuilds.keepVersionsDefault = 3;
    if (edhBlock.containers == undefined) {
        edhBlock.containers = {
            enabled: false,
            buildAndPushImages: [],
            existingEcrImages: [],
            jobDefinitions: [],
            eksEnabled: false,
            imagePullRoleArns: [],
        };
    }
    if (edhBlock.containers.enabled == undefined) edhBlock.containers.enabled = false;
    if (edhBlock.containers.buildAndPushImages == undefined)
        edhBlock.containers.buildAndPushImages = [];
    if (edhBlock.containers.existingEcrImages == undefined)
        edhBlock.containers.existingEcrImages = [];
    if (edhBlock.containers.jobDefinitions == undefined) edhBlock.containers.jobDefinitions = [];
    if (edhBlock.containers.eksEnabled == undefined) edhBlock.containers.eksEnabled = false;
    if (edhBlock.containers.imagePullRoleArns == undefined)
        edhBlock.containers.imagePullRoleArns = [];
}

/**
 * Warns about container images the add-on would build with AWS CodeBuild in a partition outside the
 * in-account CodeBuild partitions. `getConfig()` calls it only for such a partition.
 */
export function warnEdhImageBuildsOutsideCodeBuildPartitions(
    config: Config,
    resolvedPartition: string
): void {
    const edhAddon = config.app.addons?.useEdhWorkstations;
    const edhContainers = edhAddon?.containers;
    if (
        edhAddon?.enabled &&
        edhContainers?.enabled &&
        (edhContainers.buildAndPushImages?.length ?? 0) > 0
    ) {
        console.warn(
            `Configuration Warning: app.addons.useEdhWorkstations.containers.buildAndPushImages ` +
                `names ${edhContainers.buildAndPushImages.length} image(s) to build with AWS ` +
                `CodeBuild while deploying to the '${resolvedPartition}' partition. The ` +
                "in-account CodeBuild container build is supported in the aws and aws-us-gov " +
                "partitions; elsewhere the build host cannot reach the public base images and " +
                "the build trigger does not wait for the build, so a failure appears later as an " +
                "EDH job that cannot pull its image. Build the images out of band and list them " +
                "under containers.existingEcrImages instead."
        );
    }
}

/** The add-on's entry in getConfig()'s list of enabled features that require a VPC. */
export function edhVpcRequiringFeatures(config: Config): string[] {
    return config.app.addons.useEdhWorkstations.enabled ? ["addons.useEdhWorkstations"] : [];
}

/** Validates the add-on block. Normalizes `containers.enabled` to false while the add-on is off. */
/** The secret name an AWS Secrets Manager secret ARN carries (with its random suffix), or undefined. */
function secretNameOfArn(arn: unknown): string | undefined {
    if (typeof arn !== "string") return undefined;
    const marker = ":secret:";
    const at = arn.indexOf(marker);
    return at < 0 ? undefined : arn.slice(at + marker.length);
}

export function validateEdhWorkstationsConfig(config: Config): void {
    // EDH Workstations Configuration Validation
    const edh = config.app.addons.useEdhWorkstations;
    if (!edh.enabled && edh.containers.enabled) {
        // Normalized to false rather than left unvalidated: the container build resources and the
        // EDH_CONTAINERS feature flag read this field directly, without re-checking the parent.
        console.warn(
            "Configuration Warning: useEdhWorkstations.containers.enabled is ignored because useEdhWorkstations.enabled is false. EDH container jobs are part of the EDH Workstations add-on and need it enabled."
        );
        edh.containers.enabled = false;
    }
    if (!edh.enabled && edh.amiBuilds?.enabled) {
        throw new Error(
            `Configuration Error: app.addons.useEdhWorkstations.amiBuilds.enabled requires app.addons.useEdhWorkstations.enabled.`
        );
    }
    if (edh.enabled) {
        const edhPath = "app.addons.useEdhWorkstations";

        // EDH clusters are deployed in the commercial and GovCloud partitions only. Checked against
        // the resolved partition rather than app.govCloud.enabled, which also covers EU Sovereign
        // and the ISO partitions.
        if (!["aws", "aws-us-gov"].includes(config.env.partition)) {
            throw new Error(
                `Configuration Error: EDH Workstations is not available in the '${config.env.partition}' partition. ` +
                    "Set app.addons.useEdhWorkstations.enabled to false."
            );
        }

        if (
            !edh.edhApiEndpoint ||
            edh.edhApiEndpoint === "UNDEFINED" ||
            edh.edhApiEndpoint.trim() === ""
        ) {
            throw new Error(
                "Configuration Error: EDH Workstations requires edhApiEndpoint when enabled"
            );
        }
        // The internal load balancer lives in private address space, which the outbound validator
        // rejects by design; this one keeps the https and loopback/link-local rules only.
        validateInternalHttpsEndpoint(edh.edhApiEndpoint, `${edhPath}.edhApiEndpoint`);

        if (
            !edh.edhAdminApiKeySecretArn ||
            edh.edhAdminApiKeySecretArn === "UNDEFINED" ||
            edh.edhAdminApiKeySecretArn.trim() === ""
        ) {
            throw new Error(
                "Configuration Error: EDH Workstations requires edhAdminApiKeySecretArn when enabled"
            );
        }
        validateEdhSecretCompleteArn(
            edh.edhAdminApiKeySecretArn,
            `${edhPath}.edhAdminApiKeySecretArn`,
            config.env.partition
        );
        const edhCaBundleConfigured =
            !!edh.edhApiCaBundleSecretArn &&
            edh.edhApiCaBundleSecretArn !== "UNDEFINED" &&
            edh.edhApiCaBundleSecretArn.trim() !== "";
        if (edhCaBundleConfigured) {
            validateEdhSecretCompleteArn(
                edh.edhApiCaBundleSecretArn,
                `${edhPath}.edhApiCaBundleSecretArn`,
                config.env.partition
            );
        }
        // The name the ALB certificate is verified against when it is not the endpoint host. It is
        // honoured only beside a configured bundle: against the public trust store it would accept
        // any publicly issued certificate for that name.
        const edhTlsServerName =
            edh.edhApiTlsServerName && edh.edhApiTlsServerName !== "UNDEFINED"
                ? edh.edhApiTlsServerName.trim()
                : "";
        if (edhTlsServerName !== "") {
            if (!edhCaBundleConfigured) {
                throw new Error(
                    `Configuration Error: ${edhPath}.edhApiTlsServerName requires edhApiCaBundleSecretArn`
                );
            }
            if (!EDH_TLS_SERVER_NAME_PATTERN.test(edhTlsServerName)) {
                throw new Error(
                    `Configuration Error: ${edhPath}.edhApiTlsServerName must be a DNS name of at most 253 characters: labels of letters, digits and hyphens, separated by dots`
                );
            }
        }

        if (
            !edh.edhClusterId ||
            edh.edhClusterId === "UNDEFINED" ||
            edh.edhClusterId.trim() === ""
        ) {
            throw new Error(
                "Configuration Error: EDH Workstations requires edhClusterId when enabled"
            );
        }
        // The id is the suffix of the `edh:visibility:<edhClusterId>` resource tag key EDH lists
        // repositories and job definitions by; a tag key is at most 128 characters of the tag
        // character set, and the prefix takes 15 of them.
        if (!EDH_CLUSTER_ID_PATTERN.test(edh.edhClusterId)) {
            throw new Error(
                `Configuration Error: ${edhPath}.edhClusterId is the suffix of the edh:visibility:<edhClusterId> ` +
                    `resource tag key, so it must be 1-113 characters of letters, digits, spaces and _.:/=+@- . ` +
                    `Got: ${JSON.stringify(edh.edhClusterId)}`
            );
        }

        edh.edhAllowedInstanceTypes.forEach((instanceType, i) => {
            if (typeof instanceType !== "string" || !EDH_INSTANCE_TYPE_PATTERN.test(instanceType)) {
                throw new Error(
                    `Configuration Error: ${edhPath}.edhAllowedInstanceTypes[${i}] is not an EC2 instance ` +
                        `type (family.size, e.g. g5.2xlarge or c7i.metal-24xl). Got: ${JSON.stringify(
                            instanceType
                        )}`
                );
            }
        });
        // The list reaches the workstation Lambda as one JSON environment variable, and Lambda caps
        // all environment variables at 4 KB in aggregate.
        if (JSON.stringify(edh.edhAllowedInstanceTypes).length > 2048) {
            throw new Error(
                `Configuration Error: ${edhPath}.edhAllowedInstanceTypes serializes to more than 2048 bytes. ` +
                    "Shorten the list, or leave it empty to defer to the EDH profile."
            );
        }

        // Every launch reaches EDH with this project and profile, so both are required whenever the
        // add-on is enabled.
        if (typeof edh.vdi.defaultProject !== "string" || edh.vdi.defaultProject.trim() === "") {
            throw new Error(
                `Configuration Error: ${edhPath}.vdi.defaultProject must be a non-empty string when ` +
                    `the EDH Workstations add-on is enabled. Got: ${JSON.stringify(
                        edh.vdi.defaultProject
                    )}`
            );
        }
        if (
            !Number.isInteger(edh.vdi.virtualDesktopProfileId) ||
            edh.vdi.virtualDesktopProfileId < 1
        ) {
            throw new Error(
                `Configuration Error: ${edhPath}.vdi.virtualDesktopProfileId must be a positive integer ` +
                    `when the EDH Workstations add-on is enabled. Got: ${JSON.stringify(
                        edh.vdi.virtualDesktopProfileId
                    )}`
            );
        }

        // The lease hours and the grace period are seeded as whole hours, so both are bounded here
        // rather than at the first sweep.
        const lease = edh.vdi.lease;
        if (typeof lease.enabled !== "boolean") {
            throw new Error(`Configuration Error: ${edhPath}.vdi.lease.enabled must be a boolean.`);
        }
        if (typeof lease.forced !== "boolean") {
            throw new Error(`Configuration Error: ${edhPath}.vdi.lease.forced must be a boolean.`);
        }
        if (!Number.isInteger(lease.leaseHours) || lease.leaseHours < 1 || lease.leaseHours > 720) {
            throw new Error(
                `Configuration Error: ${edhPath}.vdi.lease.leaseHours must be an integer between 1 and 720. Got: ${JSON.stringify(
                    lease.leaseHours
                )}`
            );
        }
        if (!Number.isInteger(lease.graceHours) || lease.graceHours < 0 || lease.graceHours > 168) {
            throw new Error(
                `Configuration Error: ${edhPath}.vdi.lease.graceHours must be an integer between 0 and 168. Got: ${JSON.stringify(
                    lease.graceHours
                )}`
            );
        }

        if (
            !Number.isInteger(edh.predefinedImages.registrationRevision) ||
            edh.predefinedImages.registrationRevision < 0
        ) {
            throw new Error(
                `Configuration Error: ${edhPath}.predefinedImages.registrationRevision must be a ` +
                    `non-negative integer. Got: ${JSON.stringify(
                        edh.predefinedImages.registrationRevision
                    )}`
            );
        }

        // The session secret prefix scopes the reconciler's revoke grant (PutSecretValue, DeleteSecret)
        // whether or not session bootstrap is on, so it is checked whenever the add-on is enabled.
        const bootstrap = edh.sessionBootstrap;
        if (
            typeof bootstrap.secretNamePrefix !== "string" ||
            !/^[A-Za-z0-9/_+=.@-]+\/$/.test(bootstrap.secretNamePrefix)
        ) {
            throw new Error(
                `Configuration Error: ${edhPath}.sessionBootstrap.secretNamePrefix must be a Secrets ` +
                    `Manager name prefix ending in '/' (letters, digits and /_+=.@-). Got: ${JSON.stringify(
                        bootstrap.secretNamePrefix
                    )}`
            );
        }
        if (bootstrap.secretNamePrefix.length > EDH_SESSION_SECRET_PREFIX_MAX_LENGTH) {
            throw new Error(
                `Configuration Error: ${edhPath}.sessionBootstrap.secretNamePrefix must be at most ` +
                    `${EDH_SESSION_SECRET_PREFIX_MAX_LENGTH} characters: the shipped Linux recipes' session ` +
                    "bootstrap component resolves it into a document the 16,000-character component " +
                    `limit applies to. Got ${bootstrap.secretNamePrefix.length} characters.`
            );
        }
        for (const field of ["edhAdminApiKeySecretArn", "edhApiCaBundleSecretArn"] as const) {
            const name = secretNameOfArn(edh[field]);
            if (name !== undefined && name.startsWith(bootstrap.secretNamePrefix)) {
                throw new Error(
                    `Configuration Error: ${edhPath}.sessionBootstrap.secretNamePrefix must not cover the ` +
                        `${field} secret: the add-on's Lambdas may overwrite and delete every secret under the ` +
                        `prefix. Choose a prefix the operator's own secrets are not named under. Got: ${JSON.stringify(
                            bootstrap.secretNamePrefix
                        )}`
                );
            }
        }
        if (bootstrap.enabled) {
            if (
                !Number.isInteger(bootstrap.sessionKeyTtlMinutes) ||
                bootstrap.sessionKeyTtlMinutes < 1
            ) {
                throw new Error(
                    `Configuration Error: ${edhPath}.sessionBootstrap.sessionKeyTtlMinutes must be an integer ` +
                        `of at least 1 when session bootstrap is enabled. Got: ${JSON.stringify(
                            bootstrap.sessionKeyTtlMinutes
                        )}`
                );
            }
            if (
                !Array.isArray(bootstrap.workstationInstanceRoleArns) ||
                bootstrap.workstationInstanceRoleArns.length === 0
            ) {
                throw new Error(
                    `Configuration Error: ${edhPath}.sessionBootstrap.workstationInstanceRoleArns must list ` +
                        "at least one IAM role ARN when session bootstrap is enabled; the workstation " +
                        "instance role reads the per-session secret."
                );
            }
            validateEdhRoleArnList(
                bootstrap.workstationInstanceRoleArns,
                `${edhPath}.sessionBootstrap.workstationInstanceRoleArns`,
                config.env.partition
            );
        }

        validateEdhPredefinedVdiImages(edh.predefinedImages.vdi, `${edhPath}.predefinedImages.vdi`);
        validateEdhSimulationAmis(
            edh.predefinedImages.simulationAmis,
            `${edhPath}.predefinedImages.simulationAmis`
        );

        validateEdhAmiBuilds(edh.amiBuilds, `${edhPath}.amiBuilds`);
        // Shape is checked whenever the add-on is on; directories are read only when the recipes
        // will be seeded, so a disabled build surface does not fail on a directory nothing reads.
        validateEdhAmiRecipes(
            edh.predefinedImages.amiRecipes,
            `${edhPath}.predefinedImages.amiRecipes`,
            edh.amiBuilds.enabled ? join(__dirname, "..", "..", "..") : undefined
        );
        // A desktop built from a shipped recipe signs itself in from a per-session secret, which
        // only the session bootstrap surface creates.
        if (
            edh.amiBuilds.enabled &&
            edh.amiBuilds.includeShippedRecipes &&
            !edh.sessionBootstrap.enabled
        ) {
            console.warn(
                `Configuration Warning: ${edhPath}.amiBuilds.includeShippedRecipes seeds the Linux desktop recipes (al2023-desktop, isaacsim) while ${edhPath}.sessionBootstrap.enabled is false. Their desktop sign-in has no session secret to read, so a desktop built from them starts with no signed-in VAMS CLI.`
            );
        }

        // Shape is checked whenever the add-on is on; Dockerfile directories are resolved against
        // the repository root only when the container build resources will actually be created.
        validateEdhBuildImages(
            edh.containers.buildAndPushImages,
            `${edhPath}.containers.buildAndPushImages`,
            edh.containers.enabled ? join(__dirname, "..", "..", "..") : undefined
        );
        validateEdhExistingEcrImages(
            edh.containers.existingEcrImages,
            `${edhPath}.containers.existingEcrImages`
        );
        validateEdhJobDefinitions(
            edh.containers.jobDefinitions,
            `${edhPath}.containers.jobDefinitions`,
            edh.containers.buildAndPushImages,
            edh.containers.existingEcrImages
        );

        // Roles EDH's Batch compute pulls the built images with. Checked whenever the add-on is on: an
        // ARN that is malformed or in another partition cannot become valid by enabling containers.
        if (!Array.isArray(edh.containers.imagePullRoleArns)) {
            throw new Error(
                `Configuration Error: ${edhPath}.containers.imagePullRoleArns must be a list of IAM role ARNs ` +
                    `(empty when EDH's Batch compute pulls on its own identity policy). Got: ${JSON.stringify(
                        edh.containers.imagePullRoleArns
                    )}`
            );
        }
        validateEdhRoleArnList(
            edh.containers.imagePullRoleArns,
            `${edhPath}.containers.imagePullRoleArns`,
            config.env.partition
        );
    }
}

/**
 * Requires a configured in-network endpoint to be HTTPS and not to point back at the caller.
 *
 * The EDH management load balancer is reached from inside the VPC, so RFC1918 addresses and
 * private-zone names (`.internal`, `.local`) are legitimate destinations here and are not blocked as
 * they are for an outbound endpoint. Loopback and link-local stay blocked: the Lambda holds the EDH
 * root key, and 169.254.169.254 is the instance metadata endpoint.
 */
export function validateInternalHttpsEndpoint(endpoint: string, configPath: string): void {
    let url: URL;
    try {
        url = new URL(endpoint);
    } catch (e) {
        throw new Error(`Configuration Error: ${configPath} must be a valid URL. Got: ${endpoint}`);
    }
    if (url.protocol !== "https:") {
        throw new Error(
            `Configuration Error: ${configPath} must use https, or the EDH root key VAMS sends to it ` +
                `travels in cleartext. Got: ${endpoint}`
        );
    }

    const host = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
    const blocked =
        host === "localhost" ||
        host.endsWith(".localhost") ||
        host === "::1" ||
        /^127\./.test(host) ||
        /^169\.254\./.test(host); // link-local, including the instance metadata endpoint
    if (blocked) {
        throw new Error(
            `Configuration Error: ${configPath} resolves to a loopback or link-local address ` +
                `(${url.hostname}). This endpoint is called by a Lambda that holds the EDH root key, ` +
                `so it must name the EDH management load balancer. Got: ${endpoint}`
        );
    }
}

/**
 * A complete Secrets Manager secret ARN in the deployment partition. `Secret.fromSecretCompleteArn`
 * requires the 6-character suffix Secrets Manager appends to the secret name and throws at synth
 * without it, so the suffix is checked here where the message can name the configuration field.
 */
export function validateEdhSecretCompleteArn(
    arn: string,
    configPath: string,
    deploymentPartition: string
): void {
    if (!/^arn:aws[a-z-]*:secretsmanager:/.test(arn)) {
        throw new Error(
            `Configuration Error: ${configPath} must be a valid AWS Secrets Manager secret ARN. Got: ${arn}`
        );
    }
    const arnPartition = arn.split(":")[1];
    if (arnPartition !== deploymentPartition) {
        throw new Error(
            `Configuration Error: ${configPath} uses partition '${arnPartition}' which does not match ` +
                `the deployment partition '${deploymentPartition}'.`
        );
    }
    if (
        !/^arn:aws[a-z-]*:secretsmanager:[a-z0-9-]+:\d{12}:secret:[A-Za-z0-9/_+=.@-]+-[A-Za-z0-9]{6}$/.test(
            arn
        )
    ) {
        throw new Error(
            `Configuration Error: ${configPath} must be the complete secret ARN, including the ` +
                `6-character suffix Secrets Manager appends to the name (...:secret:<name>-AbCdEf). Got: ${arn}`
        );
    }
}

/**
 * Validates a list of IAM role ARNs in the deployment partition, naming the offending entry as
 * `${configPath}[i]`.
 */
function validateEdhRoleArnList(
    roleArns: string[],
    configPath: string,
    deploymentPartition: string
): void {
    roleArns.forEach((roleArn, i) => {
        const context = `${configPath}[${i}]`;
        if (
            typeof roleArn !== "string" ||
            !/^arn:aws[a-z-]*:iam::\d{12}:role\/[\w+=,.@/-]+$/.test(roleArn)
        ) {
            throw new Error(
                `Configuration Error: ${context} must be an IAM role ARN ` +
                    `(arn:<partition>:iam::<account>:role/<name>); the path and name use only ` +
                    `letters, digits and _+=,.@/- . Got: ${JSON.stringify(roleArn)}`
            );
        }
        const arnPartition = roleArn.split(":")[1];
        if (arnPartition !== deploymentPartition) {
            throw new Error(
                `Configuration Error: ${context} uses partition '${arnPartition}' which does not ` +
                    `match the deployment partition '${deploymentPartition}'.`
            );
        }
    });
}

function requireEdhNonEmptyString(value: unknown, context: string): void {
    if (typeof value !== "string" || value.trim() === "" || value === "UNDEFINED") {
        throw new Error(
            `Configuration Error: ${context} must be a non-empty string. Got: ${JSON.stringify(
                value
            )}`
        );
    }
}

function requireEdhPositiveInteger(value: unknown, context: string): void {
    if (typeof value !== "number" || !Number.isInteger(value) || value < 1) {
        throw new Error(
            `Configuration Error: ${context} must be a positive integer. Got: ${JSON.stringify(
                value
            )}`
        );
    }
}

function requireEdhAmiId(value: unknown, context: string): void {
    if (typeof value !== "string" || !EDH_AMI_ID_PATTERN.test(value)) {
        throw new Error(
            `Configuration Error: ${context} must be an AMI id (ami-…). Got: ${JSON.stringify(
                value
            )}`
        );
    }
}

function requireEdhUnique(seen: Set<string>, value: string, context: string): void {
    if (seen.has(value)) {
        throw new Error(
            `Configuration Error: ${context} '${value}' is declared more than once; EDH registers ` +
                "catalog entries by name."
        );
    }
    seen.add(value);
}

/**
 * Validates the VDI software stacks registered with EDH at deploy time. Throws a Configuration Error
 * naming the offending entry as `${configPath}[i].field`. Exported for unit testing.
 */
export function validateEdhPredefinedVdiImages(
    images: ConfigEdhPredefinedVdiImage[],
    configPath: string
): void {
    const seenStackNames = new Set<string>();
    images.forEach((image, i) => {
        const context = `${configPath}[${i}]`;
        requireEdhAmiId(image?.amiId, `${context}.amiId`);
        requireEdhNonEmptyString(image.stackName, `${context}.stackName`);
        requireEdhUnique(seenStackNames, image.stackName, `${context}.stackName`);
        requireEdhNonEmptyString(image.baseOs, `${context}.baseOs`);
        requireEdhPositiveInteger(image.rootSize, `${context}.rootSize`);
        requireEdhPositiveInteger(
            image.virtualDesktopProfileId,
            `${context}.virtualDesktopProfileId`
        );
    });
}

/**
 * Validates the simulation (target node) software stacks registered with EDH at deploy time.
 * Exported for unit testing.
 */
export function validateEdhSimulationAmis(
    amis: ConfigEdhSimulationAmi[],
    configPath: string
): void {
    const seenStackNames = new Set<string>();
    amis.forEach((ami, i) => {
        const context = `${configPath}[${i}]`;
        requireEdhAmiId(ami?.amiId, `${context}.amiId`);
        requireEdhNonEmptyString(ami.stackName, `${context}.stackName`);
        requireEdhUnique(seenStackNames, ami.stackName, `${context}.stackName`);
        requireEdhPositiveInteger(ami.rootSize, `${context}.rootSize`);
        requireEdhPositiveInteger(ami.targetNodeProfileId, `${context}.targetNodeProfileId`);
        if (ami.targetNodeUserDataId !== undefined) {
            requireEdhPositiveInteger(ami.targetNodeUserDataId, `${context}.targetNodeUserDataId`);
        }
    });
}

/**
 * Validates the container images built in-account. `dockerfileDir` is resolved against `repoRoot`
 * and must exist as a directory when `repoRoot` is given; without it only the shape is checked, so
 * a disabled container surface does not fail on a directory that nothing will build.
 * Exported for unit testing.
 */
export function validateEdhBuildImages(
    images: ConfigEdhBuildImage[],
    configPath: string,
    repoRoot?: string
): void {
    const seenNames = new Set<string>();
    images.forEach((image, i) => {
        const context = `${configPath}[${i}]`;
        if (typeof image?.name !== "string" || !EDH_IMAGE_NAME_PATTERN.test(image.name)) {
            throw new Error(
                `Configuration Error: ${context}.name must be lowercase letters, digits and hyphens, ` +
                    `starting with a letter or digit (at most 63 characters). Got: ${JSON.stringify(
                        image?.name
                    )}`
            );
        }
        requireEdhUnique(seenNames, image.name, `${context}.name`);

        requireEdhNonEmptyString(image.dockerfileDir, `${context}.dockerfileDir`);
        const dir = image.dockerfileDir;
        if (isAbsolute(dir) || /^[A-Za-z]:[\\/]/.test(dir) || /^[\\/]/.test(dir)) {
            throw new Error(
                `Configuration Error: ${context}.dockerfileDir must be relative to the repository root, ` +
                    `not absolute. Got: ${dir}`
            );
        }
        if (dir.split(/[\\/]/).includes("..")) {
            throw new Error(
                `Configuration Error: ${context}.dockerfileDir must not contain '..' path segments. Got: ${dir}`
            );
        }
        if (image.platform !== undefined && !EDH_BUILD_PLATFORMS.includes(image.platform)) {
            throw new Error(
                `Configuration Error: ${context}.platform must be one of ${EDH_BUILD_PLATFORMS.join(
                    ", "
                )}. Got: ${JSON.stringify(image.platform)}`
            );
        }
        if (repoRoot !== undefined) {
            const resolved = join(repoRoot, dir);
            if (!existsSync(resolved) || !statSync(resolved).isDirectory()) {
                throw new Error(
                    `Configuration Error: ${context}.dockerfileDir '${dir}' is not a directory under the ` +
                        `repository root (${resolved}). The image is built from the Dockerfile in that ` +
                        "directory at deploy time."
                );
            }
        }
    });
}

/**
 * Validates pre-built container images referenced by ECR URI. Exported for unit testing.
 */
export function validateEdhExistingEcrImages(
    images: ConfigEdhExistingEcrImage[],
    configPath: string
): void {
    const seenUris = new Set<string>();
    images.forEach((image, i) => {
        const context = `${configPath}[${i}]`;
        requireEdhNonEmptyString(image?.imageUri, `${context}.imageUri`);
        const remainingPlaceholders = EDH_TEMPLATE_PLACEHOLDERS.filter((token) =>
            image.imageUri.includes(token)
        );
        if (remainingPlaceholders.length > 0) {
            throw new Error(
                `Configuration Error: ${context}.imageUri still contains the template placeholder(s) ` +
                    `${remainingPlaceholders.join(", ")}. Set it to a real image URI.`
            );
        }
        if (!EDH_ECR_IMAGE_URI_PATTERN.test(image.imageUri)) {
            throw new Error(
                `Configuration Error: ${context}.imageUri must be an Amazon ECR image URI ` +
                    `(<account>.dkr.ecr.<region>.<dnsSuffix>/<repository>[:tag|@sha256:digest]). Got: ${image.imageUri}`
            );
        }
        requireEdhUnique(seenUris, image.imageUri, `${context}.imageUri`);
        requireEdhNonEmptyString(image.displayName, `${context}.displayName`);
    });
}

/**
 * Validates the Batch/EKS job definitions registered through EDH. `name` must begin with `edh-`, the
 * only job definitions EDH's controller role may submit to, and `imageRef` must resolve to a
 * `buildAndPushImages[].name` or an `existingEcrImages[].imageUri`. Exported for unit testing.
 */
export function validateEdhJobDefinitions(
    jobDefinitions: ConfigEdhJobDefinition[],
    configPath: string,
    buildImages: ConfigEdhBuildImage[],
    existingImages: ConfigEdhExistingEcrImage[]
): void {
    const imageRefs = new Set<string>([
        ...buildImages.map((image) => image.name),
        ...existingImages.map((image) => image.imageUri),
    ]);
    const seenNames = new Set<string>();
    jobDefinitions.forEach((jobDefinition, i) => {
        const context = `${configPath}[${i}]`;
        requireEdhNonEmptyString(jobDefinition?.name, `${context}.name`);
        requireEdhUnique(seenNames, jobDefinition.name, `${context}.name`);
        if (!jobDefinition.name.startsWith(EDH_BATCH_NAME_PREFIX)) {
            throw new Error(
                `Configuration Error: ${context}.name '${jobDefinition.name}' must begin with '${EDH_BATCH_NAME_PREFIX}'. ` +
                    "EDH submits container jobs under its controller role, which may submit only to job definitions " +
                    `and job queues whose names begin with '${EDH_BATCH_NAME_PREFIX}'.`
            );
        }
        requireEdhNonEmptyString(jobDefinition.imageRef, `${context}.imageRef`);
        if (!imageRefs.has(jobDefinition.imageRef)) {
            throw new Error(
                `Configuration Error: ${context}.imageRef '${jobDefinition.imageRef}' does not name a ` +
                    "containers.buildAndPushImages[].name or a containers.existingEcrImages[].imageUri."
            );
        }
        requireEdhPositiveInteger(jobDefinition.vcpus, `${context}.vcpus`);
        requireEdhPositiveInteger(jobDefinition.memory, `${context}.memory`);
        if (
            jobDefinition.gpu !== undefined &&
            (typeof jobDefinition.gpu !== "number" ||
                !Number.isInteger(jobDefinition.gpu) ||
                jobDefinition.gpu < 0)
        ) {
            throw new Error(
                `Configuration Error: ${context}.gpu must be a non-negative integer. Got: ${JSON.stringify(
                    jobDefinition.gpu
                )}`
            );
        }
        if (
            jobDefinition.platform !== undefined &&
            !EDH_JOB_PLATFORMS.includes(jobDefinition.platform)
        ) {
            throw new Error(
                `Configuration Error: ${context}.platform must be one of ${EDH_JOB_PLATFORMS.join(
                    ", "
                )}. Got: ${JSON.stringify(jobDefinition.platform)}`
            );
        }
    });
}

/** Validates the AMI build settings. Exported for unit testing. */
export function validateEdhAmiBuilds(amiBuilds: ConfigEdhAmiBuilds, configPath: string): void {
    if (typeof amiBuilds.enabled !== "boolean") {
        throw new Error(`Configuration Error: ${configPath}.enabled must be a boolean.`);
    }
    if (typeof amiBuilds.includeShippedRecipes !== "boolean") {
        throw new Error(
            `Configuration Error: ${configPath}.includeShippedRecipes must be a boolean.`
        );
    }
    if (
        !Number.isInteger(amiBuilds.buildTimeoutMinutes) ||
        amiBuilds.buildTimeoutMinutes < 30 ||
        amiBuilds.buildTimeoutMinutes > 720
    ) {
        throw new Error(
            `Configuration Error: ${configPath}.buildTimeoutMinutes must be an integer between 30 and 720. Got: ${JSON.stringify(
                amiBuilds.buildTimeoutMinutes
            )}`
        );
    }
    if (
        !Number.isInteger(amiBuilds.keepVersionsDefault) ||
        amiBuilds.keepVersionsDefault < 1 ||
        amiBuilds.keepVersionsDefault > 20
    ) {
        throw new Error(
            `Configuration Error: ${configPath}.keepVersionsDefault must be an integer between 1 and 20. Got: ${JSON.stringify(
                amiBuilds.keepVersionsDefault
            )}`
        );
    }
    if (
        typeof amiBuilds.buildSubnetId !== "string" ||
        (amiBuilds.buildSubnetId !== "" && !EDH_SUBNET_ID_PATTERN.test(amiBuilds.buildSubnetId))
    ) {
        throw new Error(
            `Configuration Error: ${configPath}.buildSubnetId must be a subnet id (subnet-...) or empty. Got: ${JSON.stringify(
                amiBuilds.buildSubnetId
            )}`
        );
    }
}

function requireRelativeDir(dir: unknown, context: string): string {
    requireEdhNonEmptyString(dir, context);
    const value = dir as string;
    if (isAbsolute(value) || /^[A-Za-z]:[\\/]/.test(value) || /^[\\/]/.test(value)) {
        throw new Error(
            `Configuration Error: ${context} must be relative to the repository root, not absolute. Got: ${value}`
        );
    }
    if (value.split(/[\\/]/).includes("..")) {
        throw new Error(
            `Configuration Error: ${context} must not contain '..' path segments. Got: ${value}`
        );
    }
    return value;
}

/**
 * Reads and checks one recipe directory: `recipe.json` plus the component files it names, relative
 * to the repository root. Exported for the nested stack and unit tests.
 */
export function loadEdhAmiRecipe(
    repoRoot: string,
    recipeDir: string,
    buildOnDeploy: boolean
): LoadedEdhAmiRecipe {
    const context = `recipe directory '${recipeDir}'`;
    const dir = join(repoRoot, recipeDir);
    const manifestPath = join(dir, "recipe.json");
    if (!existsSync(dir) || !statSync(dir).isDirectory()) {
        throw new Error(
            `Configuration Error: ${context} is not a directory under the repository root (${dir}); it must hold recipe.json.`
        );
    }
    if (!existsSync(manifestPath)) {
        throw new Error(`Configuration Error: ${context} has no recipe.json.`);
    }
    let manifest: EdhAmiRecipeManifest;
    try {
        manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
    } catch (err) {
        throw new Error(
            `Configuration Error: ${context}: recipe.json is not valid JSON (${
                (err as Error).message
            }).`
        );
    }
    if (typeof manifest.recipeId !== "string" || !EDH_RECIPE_ID_PATTERN.test(manifest.recipeId)) {
        throw new Error(
            `Configuration Error: ${context}: recipeId must match ${EDH_RECIPE_ID_PATTERN}. Got: ${JSON.stringify(
                manifest.recipeId
            )}`
        );
    }
    requireEdhNonEmptyString(manifest.displayName, `${context}: displayName`);
    if (!EDH_AMI_RECIPE_KINDS.includes(manifest.kind)) {
        throw new Error(
            `Configuration Error: ${context}: kind must be one of ${EDH_AMI_RECIPE_KINDS.join(
                ", "
            )}.`
        );
    }
    if (!["linux", "windows"].includes(manifest.osFamily)) {
        throw new Error(`Configuration Error: ${context}: osFamily must be linux or windows.`);
    }
    if (manifest.kind === "vdi") {
        if (!EDH_DCV_BASE_OS_KEYS.includes(manifest.baseOs as string)) {
            throw new Error(
                `Configuration Error: ${context}: baseOs must be one of ${EDH_DCV_BASE_OS_KEYS.join(
                    ", "
                )} for a vdi recipe.`
            );
        }
        const family = (manifest.baseOs as string).startsWith("windows") ? "windows" : "linux";
        if (family !== manifest.osFamily) {
            throw new Error(
                `Configuration Error: ${context}: osFamily '${manifest.osFamily}' does not match baseOs '${manifest.baseOs}'.`
            );
        }
    } else if (manifest.baseOs !== undefined) {
        throw new Error(`Configuration Error: ${context}: baseOs applies to vdi recipes only.`);
    }
    if (!EDH_AMI_ARCHITECTURES.includes(manifest.architecture)) {
        throw new Error(`Configuration Error: ${context}: architecture must be x86_64 or arm64.`);
    }
    if (manifest.osFamily === "windows" && manifest.architecture !== "x86_64") {
        throw new Error(`Configuration Error: ${context}: a Windows recipe must be x86_64.`);
    }
    if (
        typeof manifest.parentImage !== "string" ||
        !EDH_PARENT_IMAGE_PATTERN.test(manifest.parentImage)
    ) {
        throw new Error(
            `Configuration Error: ${context}: parentImage must be an AMI id, 'ssm:/aws/service/<parameter>' or 'edhStack:<stack name>'.`
        );
    }
    const buildInstanceTypes: unknown =
        typeof manifest.buildInstanceType === "string"
            ? [manifest.buildInstanceType]
            : manifest.buildInstanceType;
    if (
        !Array.isArray(buildInstanceTypes) ||
        buildInstanceTypes.length < 1 ||
        buildInstanceTypes.length > EDH_AMI_RECIPE_MAX_BUILD_INSTANCE_TYPES ||
        new Set(buildInstanceTypes).size !== buildInstanceTypes.length ||
        !buildInstanceTypes.every(
            (type: unknown) =>
                typeof type === "string" &&
                type.length <= EDH_AMI_RECIPE_BUILD_INSTANCE_TYPE_MAX_LENGTH &&
                EDH_INSTANCE_TYPE_PATTERN.test(type)
        )
    ) {
        throw new Error(
            `Configuration Error: ${context}: buildInstanceType must be family.size, or a list of 1-${EDH_AMI_RECIPE_MAX_BUILD_INSTANCE_TYPES} distinct family.size instance types.`
        );
    }
    // Absent, null or empty is no restriction; a list is spelled as the build instance types are.
    const launchInstanceTypes: unknown = manifest.launchInstanceTypes;
    if (
        launchInstanceTypes !== undefined &&
        launchInstanceTypes !== null &&
        (!Array.isArray(launchInstanceTypes) ||
            launchInstanceTypes.length > EDH_AMI_RECIPE_MAX_LAUNCH_INSTANCE_TYPES ||
            new Set(launchInstanceTypes).size !== launchInstanceTypes.length ||
            !launchInstanceTypes.every(
                (type: unknown) =>
                    typeof type === "string" &&
                    type.length <= EDH_AMI_RECIPE_BUILD_INSTANCE_TYPE_MAX_LENGTH &&
                    EDH_INSTANCE_TYPE_PATTERN.test(type)
            ))
    ) {
        throw new Error(
            `Configuration Error: ${context}: launchInstanceTypes must be a list of 1-${EDH_AMI_RECIPE_MAX_LAUNCH_INSTANCE_TYPES} distinct family.size instance types, or be left out.`
        );
    }
    if (
        !Number.isInteger(manifest.rootSizeGb) ||
        manifest.rootSizeGb < 10 ||
        manifest.rootSizeGb > 2048
    ) {
        throw new Error(
            `Configuration Error: ${context}: rootSizeGb must be an integer between 10 and 2048.`
        );
    }
    requireEdhNonEmptyString(manifest.stackName, `${context}: stackName`);
    if (!Number.isInteger(manifest.profileId) || manifest.profileId < 0) {
        throw new Error(
            `Configuration Error: ${context}: profileId must be a non-negative integer.`
        );
    }
    if (
        !Array.isArray(manifest.components) ||
        manifest.components.length < 1 ||
        manifest.components.length > 20
    ) {
        throw new Error(`Configuration Error: ${context}: components must list 1 to 20 entries.`);
    }
    const componentFiles: string[] = [];
    manifest.components.forEach((component, i) => {
        const cContext = `${context}: components[${i}]`;
        const kinds = ["arn", "file", "data"].filter((k) => (component as any)[k] !== undefined);
        if (kinds.length !== 1) {
            throw new Error(
                `Configuration Error: ${cContext} must carry exactly one of arn, file or data.`
            );
        }
        if (component.arn !== undefined && !EDH_COMPONENT_ARN_PATTERN.test(component.arn)) {
            throw new Error(
                `Configuration Error: ${cContext}.arn is not an Image Builder component ARN.`
            );
        }
        if (component.file !== undefined) {
            const rel = requireRelativeDir(component.file, `${cContext}.file`);
            const filePath = join(dir, rel);
            if (!existsSync(filePath) || !statSync(filePath).isFile()) {
                throw new Error(
                    `Configuration Error: ${cContext}.file '${rel}' does not exist under ${recipeDir}.`
                );
            }
            componentFiles.push(`${recipeDir}/${rel}`.replace(/\\/g, "/"));
        }
        if (component.data !== undefined) {
            requireEdhNonEmptyString(component.name, `${cContext}.name`);
            requireEdhNonEmptyString(component.data, `${cContext}.data`);
        }
    });
    const assets: Array<{ id: string; repoPath: string }> = [];
    if (manifest.assets !== undefined) {
        if (!Array.isArray(manifest.assets)) {
            throw new Error(`Configuration Error: ${context}: assets must be an array.`);
        }
        if (manifest.assets.length > EDH_AMI_RECIPE_MAX_ASSETS) {
            throw new Error(
                `Configuration Error: ${context}: assets must list at most ${EDH_AMI_RECIPE_MAX_ASSETS} entries.`
            );
        }
        const seenAssetIds = new Set<string>();
        manifest.assets.forEach((asset, i) => {
            const aContext = `${context}: assets[${i}]`;
            if (typeof asset?.id !== "string" || !EDH_ASSET_ID_PATTERN.test(asset.id)) {
                throw new Error(
                    `Configuration Error: ${aContext}.id must match ${EDH_ASSET_ID_PATTERN}. Got: ${JSON.stringify(
                        asset?.id
                    )}`
                );
            }
            if (seenAssetIds.has(asset.id)) {
                throw new Error(
                    `Configuration Error: ${context}: duplicate asset id '${asset.id}'.`
                );
            }
            seenAssetIds.add(asset.id);
            const repoPath = requireRelativeDir(asset.repoPath, `${aContext}.repoPath`)
                .replace(/\\/g, "/")
                .replace(/\/+$/, "");
            if (!EDH_ASSET_REPO_PATH_PREFIXES.some((prefix) => repoPath.startsWith(prefix))) {
                throw new Error(
                    `Configuration Error: ${aContext}.repoPath must start with one of: ${EDH_ASSET_REPO_PATH_PREFIXES.join(
                        ", "
                    )}. Got: ${repoPath}`
                );
            }
            const assetDir = join(repoRoot, repoPath);
            if (!existsSync(assetDir) || !statSync(assetDir).isDirectory()) {
                throw new Error(
                    `Configuration Error: ${aContext}.repoPath '${repoPath}' is not a directory under the repository root (${assetDir}).`
                );
            }
            assets.push({ id: asset.id, repoPath });
        });
    }
    if (manifest.parameters !== undefined) {
        if (
            typeof manifest.parameters !== "object" ||
            manifest.parameters === null ||
            Array.isArray(manifest.parameters)
        ) {
            throw new Error(`Configuration Error: ${context}: parameters must be an object.`);
        }
        const keys = Object.keys(manifest.parameters);
        if (keys.length > EDH_AMI_RECIPE_MAX_PARAMETERS) {
            throw new Error(
                `Configuration Error: ${context}: parameters must list at most ${EDH_AMI_RECIPE_MAX_PARAMETERS} entries.`
            );
        }
        for (const key of keys) {
            if (!EDH_PARAMETER_KEY_PATTERN.test(key)) {
                throw new Error(
                    `Configuration Error: ${context}: parameters key '${key}' must match ${EDH_PARAMETER_KEY_PATTERN}.`
                );
            }
            const value = manifest.parameters[key];
            if (typeof value !== "string" || value.length > EDH_AMI_RECIPE_PARAMETER_VALUE_MAX) {
                throw new Error(
                    `Configuration Error: ${context}: parameters.${key} must be a string of at most ${EDH_AMI_RECIPE_PARAMETER_VALUE_MAX} characters.`
                );
            }
        }
    }
    if (
        manifest.keepVersions !== undefined &&
        (!Number.isInteger(manifest.keepVersions) ||
            manifest.keepVersions < 1 ||
            manifest.keepVersions > 20)
    ) {
        throw new Error(
            `Configuration Error: ${context}: keepVersions must be an integer between 1 and 20.`
        );
    }
    if (
        manifest.recipeRevision !== undefined &&
        (!Number.isInteger(manifest.recipeRevision) || manifest.recipeRevision < 1)
    ) {
        throw new Error(
            `Configuration Error: ${context}: recipeRevision must be a positive integer.`
        );
    }
    return {
        recipeDir,
        recipeId: manifest.recipeId,
        manifest,
        componentFiles,
        assets,
        buildOnDeploy,
    };
}

/**
 * Validates the recipe references. Directories are read only when `repoRoot` is given.
 * Exported for unit testing.
 */
export function validateEdhAmiRecipes(
    refs: ConfigEdhAmiRecipeRef[],
    configPath: string,
    repoRoot?: string
): void {
    if (!Array.isArray(refs)) {
        throw new Error(`Configuration Error: ${configPath} must be an array.`);
    }
    const seenDirs = new Set<string>();
    const seenIds = new Set<string>();
    refs.forEach((ref, i) => {
        const context = `${configPath}[${i}]`;
        const dir = requireRelativeDir(ref?.recipeDir, `${context}.recipeDir`)
            .replace(/\\/g, "/")
            .replace(/\/+$/, "");
        if (typeof ref.buildOnDeploy !== "boolean") {
            throw new Error(`Configuration Error: ${context}.buildOnDeploy must be a boolean.`);
        }
        if (repoRoot !== undefined) {
            const loaded = loadEdhAmiRecipe(repoRoot, dir, ref.buildOnDeploy);
            if (seenIds.has(loaded.recipeId)) {
                throw new Error(
                    `Configuration Error: ${context}: duplicate recipeId '${loaded.recipeId}'.`
                );
            }
            seenIds.add(loaded.recipeId);
        }
        requireEdhUnique(seenDirs, dir, `${context}.recipeDir`);
    });
}

/** The recipes a deployment seeds: shipped directories plus operator references, by recipe id. */
export function resolveEdhAmiRecipes(
    edh: ConfigEdhWorkstations,
    repoRoot: string
): LoadedEdhAmiRecipe[] {
    if (!edh.amiBuilds.enabled) return [];
    const byId = new Map<string, LoadedEdhAmiRecipe>();
    if (edh.amiBuilds.includeShippedRecipes) {
        for (const dir of EDH_SHIPPED_AMI_RECIPE_DIRS) {
            const loaded = loadEdhAmiRecipe(repoRoot, dir, false);
            byId.set(loaded.recipeId, loaded);
        }
    }
    for (const ref of edh.predefinedImages.amiRecipes) {
        const loaded = loadEdhAmiRecipe(
            repoRoot,
            ref.recipeDir.replace(/\\/g, "/").replace(/\/+$/, ""),
            ref.buildOnDeploy
        );
        byId.set(loaded.recipeId, loaded);
    }
    return Array.from(byId.values());
}
