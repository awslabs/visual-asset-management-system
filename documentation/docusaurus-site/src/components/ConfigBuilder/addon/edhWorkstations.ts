/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The ConfigBuilder mirror of the EDH Workstations add-on block, `app.addons.useEdhWorkstations`:
 * its form fields, its entry in the VPC-requiring features, the rules ported from
 * infra/config/addon/edhWorkstationsConfig.ts, its template defaults, the one normalization
 * `getConfig()` performs on it, and the paths edited only in the downloaded config.json. `index.ts`
 * joins each into the core tables.
 *
 * Each rule section names the block it mirrors by quoting that block's leading comment or its
 * error-message text; search edhWorkstationsConfig.ts for the quoted string to find it.
 */

import type { ConfigShape, DerivedChange, FieldMeta, Rule } from "../types";
import { getByPath, setByPath } from "../pathUtils";
import {
    g,
    isAbsent,
    isBlank,
    isValidUrl,
    partitionForRegionName,
    SECRETSMANAGER_ARN_PATTERN,
} from "../ruleHelpers";
import type { VpcRequiringFeature } from "../ruleHelpers";

// Regexes copied verbatim from edhWorkstationsConfig.ts (the EDH validators and helpers).
const EDH_INSTANCE_TYPE_PATTERN = /^[a-z0-9-]+\.[a-z0-9-]+$/;
const EDH_AMI_ID_PATTERN = /^ami-[0-9a-f]{8,17}$/;
const EDH_IMAGE_NAME_PATTERN = /^[a-z0-9][a-z0-9-]{0,62}$/;
const EDH_BUILD_PLATFORMS = ["linux/amd64", "linux/arm64"];
const EDH_JOB_PLATFORMS = ["EC2", "FARGATE"];
const EDH_BATCH_NAME_PREFIX = "edh-";
const EDH_ECR_IMAGE_URI_PATTERN =
    /^\d{12}\.dkr\.ecr\.[a-z0-9-]+\.[a-z0-9.-]+\/[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9_.-]{1,128}|@sha256:[0-9a-f]{64})?$/;
const EDH_TEMPLATE_PLACEHOLDERS = [
    "<ACCOUNTID>",
    "<REGION>",
    "<ECR-REPOSITORY>",
    "<IMAGE-ID>",
    "<IMAGE-TAG>",
];
const EDH_SECRET_COMPLETE_ARN_PATTERN =
    /^arn:aws[a-z-]*:secretsmanager:[a-z0-9-]+:\d{12}:secret:[A-Za-z0-9/_+=.@-]+-[A-Za-z0-9]{6}$/;
const EDH_ROLE_ARN_PATTERN = /^arn:aws[a-z-]*:iam::\d{12}:role\/[\w+=,.@/-]+$/;
const EDH_CLUSTER_ID_PATTERN = /^[A-Za-z0-9 _.:/=+@-]{1,113}$/;
const EDH_SECRET_PREFIX_PATTERN = /^[A-Za-z0-9/_+=.@-]+\/$/;
// edhWorkstationsConfig.ts: EDH_SESSION_SECRET_PREFIX_MAX_LENGTH
const EDH_SESSION_SECRET_PREFIX_MAX_LENGTH = 128;
// edhWorkstationsConfig.ts: EDH_TLS_SERVER_NAME_PATTERN
const EDH_TLS_SERVER_NAME_PATTERN =
    /^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$/;

/**
 * Add-on paths with no `EDH_FIELDS` entry, which configBuilderSync.test.ts accepts as config
 * leaves the builder does not render.
 */
export const EDH_JSON_ONLY_PATHS: readonly string[] = [
    // The EDH catalog and container lists are edited in the downloaded config.json: each entry is a
    // record of typed members and no composite input exists for them. EDH_FIELDS therefore carries no
    // path for the list, and the note lives on the neighbouring `registrationRevision` and
    // `containers.enabled` fields. Both the list and every member are listed so that adding a member
    // to any of the five interfaces fails configBuilderSync.test.ts until this list is updated.
    "app.addons.useEdhWorkstations.predefinedImages.vdi",
    "app.addons.useEdhWorkstations.predefinedImages.vdi.amiId",
    "app.addons.useEdhWorkstations.predefinedImages.vdi.stackName",
    "app.addons.useEdhWorkstations.predefinedImages.vdi.baseOs",
    "app.addons.useEdhWorkstations.predefinedImages.vdi.rootSize",
    "app.addons.useEdhWorkstations.predefinedImages.vdi.virtualDesktopProfileId",
    "app.addons.useEdhWorkstations.predefinedImages.vdi.launchTenancy",
    "app.addons.useEdhWorkstations.predefinedImages.vdi.description",
    "app.addons.useEdhWorkstations.predefinedImages.simulationAmis",
    "app.addons.useEdhWorkstations.predefinedImages.simulationAmis.amiId",
    "app.addons.useEdhWorkstations.predefinedImages.simulationAmis.stackName",
    "app.addons.useEdhWorkstations.predefinedImages.simulationAmis.rootSize",
    "app.addons.useEdhWorkstations.predefinedImages.simulationAmis.targetNodeProfileId",
    "app.addons.useEdhWorkstations.predefinedImages.simulationAmis.targetNodeUserDataId",
    "app.addons.useEdhWorkstations.predefinedImages.simulationAmis.userDataVariables",
    "app.addons.useEdhWorkstations.predefinedImages.simulationAmis.description",
    "app.addons.useEdhWorkstations.containers.buildAndPushImages",
    "app.addons.useEdhWorkstations.containers.buildAndPushImages.name",
    "app.addons.useEdhWorkstations.containers.buildAndPushImages.dockerfileDir",
    "app.addons.useEdhWorkstations.containers.buildAndPushImages.platform",
    "app.addons.useEdhWorkstations.containers.existingEcrImages",
    "app.addons.useEdhWorkstations.containers.existingEcrImages.imageUri",
    "app.addons.useEdhWorkstations.containers.existingEcrImages.displayName",
    "app.addons.useEdhWorkstations.containers.jobDefinitions",
    "app.addons.useEdhWorkstations.containers.jobDefinitions.name",
    "app.addons.useEdhWorkstations.containers.jobDefinitions.imageRef",
    "app.addons.useEdhWorkstations.containers.jobDefinitions.vcpus",
    "app.addons.useEdhWorkstations.containers.jobDefinitions.memory",
    "app.addons.useEdhWorkstations.containers.jobDefinitions.gpu",
    "app.addons.useEdhWorkstations.containers.jobDefinitions.platform",
    // amiBuilds: EDH_FIELDS carries individual field entries (enabled, includeShippedRecipes, etc.).
    "app.addons.useEdhWorkstations.amiBuilds",
    // amiRecipes: edited in the downloaded config.json, like vdi/simulationAmis above.
    "app.addons.useEdhWorkstations.predefinedImages.amiRecipes",
    "app.addons.useEdhWorkstations.predefinedImages.amiRecipes.recipeDir",
    "app.addons.useEdhWorkstations.predefinedImages.amiRecipes.buildOnDeploy",
    // The add-on block itself is a named interface, which the sync test's walker records as a path of
    // its own; EDH_FIELDS carries its fields one by one, like amiBuilds above.
    "app.addons.useEdhWorkstations",
];

/** The add-on block of the template JSONs; `defaults.ts` splices it in after `usePhysnaSync`. */
export const EDH_DEFAULTS: ConfigShape = {
    useEdhWorkstations: {
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
    },
};

/** The add-on's form fields, rendered at the end of the Add-ons section. */
export const EDH_FIELDS: FieldMeta[] = [
    {
        path: "app.addons.useEdhWorkstations.enabled",
        label: "EDH Workstations",
        input: "boolean",
        section: "addons",
        advanced: true,
        help: "Launch and connect to EDH GPU DCV desktops from VAMS. Requires app.useGlobalVpc.enabled and the aws or aws-us-gov partition.",
    },
    {
        path: "app.addons.useEdhWorkstations.edhApiEndpoint",
        label: "EDH — management API endpoint",
        input: "text",
        section: "addons",
        advanced: true,
        placeholder: "https://internal-edh-alb.us-east-1.elb.amazonaws.com",
        help: "Scheme and host of the EDH management load balancer (https, no path). A path is not caught before deployment: it makes every add-on function fail at initialization, and the deployment rolls back. Private addresses and .internal names are accepted; loopback and link-local are not.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.edhAdminApiKeySecretArn",
        label: "EDH — root key secret ARN",
        input: "text",
        section: "addons",
        advanced: true,
        placeholder:
            "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams/edh/root-key-AbCdEf",
        help: 'Complete ARN (with the 6-character suffix) of an operator-managed secret whose SecretString is {"rootKey": "..."}.',
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.edhApiCaBundleSecretArn",
        label: "EDH — CA bundle secret ARN (optional)",
        input: "text",
        section: "addons",
        advanced: true,
        help: "Complete ARN of a secret holding the PEM bundle that signs the EDH load balancer certificate. Leave empty for a publicly trusted certificate.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.edhApiTlsServerName",
        label: "EDH — certificate server name (optional)",
        input: "text",
        section: "addons",
        advanced: true,
        placeholder: "EDH.DEFAULT.CREATE.YOUR.OWN.CERTIFICATE",
        help: "Name the EDH load balancer certificate is verified against when it is not the endpoint host, such as the name of the certificate the EDH installer creates with --tls-certificate auto. Requires the CA bundle secret. Leave empty to verify the endpoint host.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.edhClusterId",
        label: "EDH — cluster id",
        input: "text",
        section: "addons",
        advanced: true,
        help: "Required when EDH Workstations is enabled. 1-113 tag-key characters (letters, digits, spaces, _.:/=+@-).",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.autoProvisionUsers",
        label: "EDH — auto-provision users",
        input: "boolean",
        section: "addons",
        advanced: true,
        help: "Create the EDH user on first launch. When off, a user unknown to EDH is refused.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.edhAllowedInstanceTypes",
        label: "EDH — allowed instance types",
        input: "string-array",
        section: "addons",
        advanced: true,
        help: "Instance types offered in the launch wizard (family.size). Empty defers to the EDH profile. Serializes to at most 2048 bytes.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.vdi.defaultProject",
        label: "EDH — VDI default project",
        input: "text",
        section: "addons",
        advanced: true,
        placeholder: "default",
        help: "EDH project the launched desktops are created in. Required whenever the add-on is enabled.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.vdi.virtualDesktopProfileId",
        label: "EDH — VDI profile id",
        input: "number",
        section: "addons",
        advanced: true,
        min: 1,
        help: "EDH virtual desktop profile whose allowed instance types the launch wizard offers; each desktop launches under the profile its image is bound to. A positive integer, required whenever the add-on is enabled; a stock EDH cluster seeds profile 1.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.vdi.lease.enabled",
        label: "EDH — desktop leases",
        input: "boolean",
        section: "addons",
        advanced: true,
        help: "Offer a lease on launch that expires unless the user renews it. The four lease values seed the policy only on the first deployment that finds none; after that, change it on the Workstation Admin page's Settings tab.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.vdi.lease.leaseHours",
        label: "EDH — lease hours",
        input: "number",
        section: "addons",
        advanced: true,
        min: 1,
        help: "Longest lease a user may request, and the lease every launch gets when leases are forced. 1-720.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.vdi.lease.forced",
        label: "EDH — force leases",
        input: "boolean",
        section: "addons",
        advanced: true,
        help: "Lease every launch for the full lease hours, with no unleased option.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.vdi.lease.graceHours",
        label: "EDH — lease grace hours",
        input: "number",
        section: "addons",
        advanced: true,
        min: 0,
        help: "Hours an expired desktop stays stopped before it is deleted. 0-168.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.sessionBootstrap.enabled",
        label: "EDH — session bootstrap",
        input: "boolean",
        section: "addons",
        advanced: true,
        help: "Seed a per-session VAMS API key into each Linux workstation through a Secrets Manager secret.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.sessionBootstrap.sessionKeyTtlMinutes",
        label: "EDH — session key TTL (minutes)",
        input: "number",
        section: "addons",
        advanced: true,
        min: 1,
        help: "Lifetime of each session key in minutes; at least 1. The key expires at launch time plus this value and is re-minted on every start.",
        visibleWhen: edhBootstrap,
    },
    {
        path: "app.addons.useEdhWorkstations.sessionBootstrap.secretNamePrefix",
        label: "EDH — session secret name prefix",
        input: "text",
        section: "addons",
        advanced: true,
        placeholder: "vams/edh-session/",
        help: "Letters, digits and / _ + = . @ -, ending with '/', at most 128 characters. The add-on's IAM grants and key policy are bounded to this prefix.",
        visibleWhen: edhBootstrap,
    },
    {
        path: "app.addons.useEdhWorkstations.sessionBootstrap.workstationInstanceRoleArns",
        label: "EDH — workstation instance role ARNs",
        input: "string-array",
        section: "addons",
        advanced: true,
        help: "IAM roles the EDH workstation instances run under; they are granted read on the per-session secret from that session's desktop instance. At least one is required when session bootstrap is enabled.",
        visibleWhen: edhBootstrap,
    },
    {
        path: "app.addons.useEdhWorkstations.sessionBootstrap.launchManifest",
        label: "EDH — launch manifest",
        input: "boolean",
        section: "addons",
        advanced: true,
        help: "Reserved; has no effect.",
        visibleWhen: edhBootstrap,
    },
    {
        path: "app.addons.useEdhWorkstations.amiBuilds.enabled",
        label: "EDH — AMI builds",
        input: "boolean",
        section: "addons",
        advanced: true,
        help: "Build AMIs for EDH with EC2 Image Builder from recipes shipped in the repository and defined on the Workstation Admin page; each build is registered in EDH as a versioned software stack.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.amiBuilds.includeShippedRecipes",
        label: "EDH — include shipped recipes",
        input: "boolean",
        section: "addons",
        advanced: true,
        help: "Seed the recipes shipped under backendAddons/edh/amiRecipes/ (al2023-desktop, windows2022-desktop, isaacsim) with buildOnDeploy off. With AMI builds on and session bootstrap off, the Linux desktops built from them start with no signed-in VAMS CLI.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.amiBuilds.buildSubnetId",
        label: "EDH — build subnet ID",
        input: "text",
        section: "addons",
        advanced: true,
        placeholder: "subnet-...",
        help: "Subnet the Image Builder build instance launches in; it must be in the VAMS VPC, where the build security group is created. Empty selects the first private subnet, falling back to the first isolated subnet. The shipped recipes install packages from the internet, so name a subnet with internet egress when that VPC has no private subnets.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.amiBuilds.buildTimeoutMinutes",
        label: "EDH — build timeout minutes",
        input: "number",
        section: "addons",
        advanced: true,
        min: 30,
        help: "Minutes after which the sweep fails a build Image Builder still reports running and cancels its image creation. 30-720.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.amiBuilds.keepVersionsDefault",
        label: "EDH — keep versions default",
        input: "number",
        section: "addons",
        advanced: true,
        min: 1,
        help: "AMIs kept per recipe when the recipe sets no keepVersions; older retired and failed builds have their AMI and snapshots deregistered once no Amazon EC2 instance uses the AMI. 1-20.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.predefinedImages.registrationRevision",
        label: "EDH — catalog registration revision",
        input: "number",
        section: "addons",
        advanced: true,
        min: 0,
        help: "Revision recorded with each registration run; registration runs on every deployment, so a redeploy retries warned entries. The image lists themselves — predefinedImages.vdi[], predefinedImages.simulationAmis[] and predefinedImages.amiRecipes[] — are edited in the downloaded config.json; the builder has no editor for them.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.containers.enabled",
        label: "EDH — container jobs",
        input: "boolean",
        section: "addons",
        advanced: true,
        help: "Submit EDH Batch/EKS container jobs from VAMS. containers.buildAndPushImages[], containers.existingEcrImages[] and containers.jobDefinitions[] are edited in the downloaded config.json; the builder has no editor for them.",
        visibleWhen: edhEnabled,
    },
    {
        path: "app.addons.useEdhWorkstations.containers.eksEnabled",
        label: "EDH — EKS job backend",
        input: "boolean",
        section: "addons",
        advanced: true,
        visibleWhen: edhContainers,
    },
    {
        path: "app.addons.useEdhWorkstations.containers.imagePullRoleArns",
        label: "EDH — image pull role ARNs",
        input: "string-array",
        section: "addons",
        advanced: true,
        help: "IAM roles EDH's Batch compute pulls the built images with (the Amazon ECS container instance role of EDH's AWS Batch compute environment); every VAMS-built image repository grants them pull access. Leave empty when that role pulls from a same-account repository on its own identity policy. Validated whenever the add-on is enabled; the repositories exist only with container jobs on.",
        visibleWhen: edhEnabled,
    },
];

/** The add-on's entry in the enabled features that require a VPC (edhWorkstationsConfig.ts: `edhVpcRequiringFeatures`). */
export const EDH_VPC_REQUIRING_FEATURES: VpcRequiringFeature[] = [
    {
        id: "vpc-required-edh-workstations",
        label: "addons.useEdhWorkstations",
        fieldPaths: ["app.addons.useEdhWorkstations.enabled"],
        appliesWhen: (c) => !!g(c, "app.addons.useEdhWorkstations.enabled"),
    },
];

export const EDH_RULES: Rule[] = [
    // ----- EDH vdi block
    // (edhWorkstationsConfig.ts: "app.addons.useEdhWorkstations.vdi.enabled is not a configuration") -----
    // Applies whether or not the add-on is enabled: getConfig() refuses the key while backfilling
    // the block's defaults, before the add-on's own validation.
    {
        id: "edh-vdi-enabled-not-a-field",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.enabled"],
        appliesWhen: (c) => {
            const vdi = g(c, "app.addons.useEdhWorkstations.vdi");
            return vdi != null && vdi.enabled !== undefined;
        },
        message:
            "app.addons.useEdhWorkstations.vdi.enabled is not a configuration field. The desktop routes and the Workstations page deploy whenever app.addons.useEdhWorkstations.enabled is true; remove the vdi.enabled key from the downloaded config.json.",
    },

    // ----- EDH Workstations (edhWorkstationsConfig.ts: "EDH Workstations Configuration Validation") -----
    {
        id: "edh-partition",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.enabled", "env.region"],
        appliesWhen: (c) => {
            if (!edhEnabled(c)) return false;
            const partition = partitionForRegionName(g(c, "env.region"));
            return partition !== undefined && !["aws", "aws-us-gov"].includes(partition);
        },
        message:
            "EDH Workstations is not available in this deployment Region's partition; it is offered in the commercial and GovCloud partitions. Set app.addons.useEdhWorkstations.enabled to false.",
    },
    {
        id: "edh-apiendpoint-required",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.edhApiEndpoint"],
        appliesWhen: (c) =>
            edhEnabled(c) && isBlank(g(c, "app.addons.useEdhWorkstations.edhApiEndpoint")),
        message: "EDH Workstations requires edhApiEndpoint when enabled.",
    },
    {
        id: "edh-apiendpoint-url",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.edhApiEndpoint"],
        appliesWhen: (c) => {
            if (!edhEnabled(c)) return false;
            const endpoint = g(c, "app.addons.useEdhWorkstations.edhApiEndpoint");
            if (isBlank(endpoint)) return false; // covered by edh-apiendpoint-required
            return !isValidUrl(endpoint);
        },
        message: "useEdhWorkstations.edhApiEndpoint must be a valid URL.",
    },
    {
        id: "edh-apiendpoint-https",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.edhApiEndpoint"],
        appliesWhen: (c) => {
            if (!edhEnabled(c)) return false;
            const endpoint = g(c, "app.addons.useEdhWorkstations.edhApiEndpoint");
            if (isBlank(endpoint) || !isValidUrl(endpoint)) return false;
            return new URL(endpoint).protocol !== "https:";
        },
        message:
            "useEdhWorkstations.edhApiEndpoint must use https, or the EDH root key VAMS sends to it travels in cleartext.",
    },
    {
        id: "edh-apiendpoint-loopback",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.edhApiEndpoint"],
        appliesWhen: (c) => {
            if (!edhEnabled(c)) return false;
            const endpoint = g(c, "app.addons.useEdhWorkstations.edhApiEndpoint");
            if (isBlank(endpoint) || !isValidUrl(endpoint)) return false;
            return edhEndpointHostBlocked(endpoint);
        },
        message:
            "useEdhWorkstations.edhApiEndpoint resolves to a loopback or link-local address. It is called by a Lambda that holds the EDH root key, so it must name the EDH management load balancer. Private addresses and .internal names are accepted.",
    },
    {
        id: "edh-adminkey-secretarn-required",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.edhAdminApiKeySecretArn"],
        appliesWhen: (c) =>
            edhEnabled(c) && isBlank(g(c, "app.addons.useEdhWorkstations.edhAdminApiKeySecretArn")),
        message: "EDH Workstations requires edhAdminApiKeySecretArn when enabled.",
    },
    {
        id: "edh-adminkey-secretarn-format",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.edhAdminApiKeySecretArn"],
        appliesWhen: (c) => {
            if (!edhEnabled(c)) return false;
            const arn = g(c, "app.addons.useEdhWorkstations.edhAdminApiKeySecretArn");
            if (isBlank(arn)) return false; // covered by edh-adminkey-secretarn-required
            return (
                !SECRETSMANAGER_ARN_PATTERN.test(arn) || !EDH_SECRET_COMPLETE_ARN_PATTERN.test(arn)
            );
        },
        message:
            "useEdhWorkstations.edhAdminApiKeySecretArn must be the complete Secrets Manager secret ARN, including the 6-character suffix Secrets Manager appends to the name (...:secret:<name>-AbCdEf).",
    },
    {
        id: "edh-adminkey-secretarn-partition",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.edhAdminApiKeySecretArn", "env.region"],
        appliesWhen: (c) => {
            if (!edhEnabled(c)) return false;
            const arn = g(c, "app.addons.useEdhWorkstations.edhAdminApiKeySecretArn");
            if (isBlank(arn) || !EDH_SECRET_COMPLETE_ARN_PATTERN.test(arn)) return false;
            return edhArnPartitionMismatch(c, arn);
        },
        message:
            "useEdhWorkstations.edhAdminApiKeySecretArn uses a partition that does not match the deployment Region's partition.",
    },
    {
        id: "edh-cabundle-secretarn-format",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.edhApiCaBundleSecretArn"],
        appliesWhen: (c) => {
            if (!edhEnabled(c)) return false;
            const arn = g(c, "app.addons.useEdhWorkstations.edhApiCaBundleSecretArn");
            if (isBlank(arn)) return false; // optional
            return (
                !SECRETSMANAGER_ARN_PATTERN.test(arn) || !EDH_SECRET_COMPLETE_ARN_PATTERN.test(arn)
            );
        },
        message:
            "useEdhWorkstations.edhApiCaBundleSecretArn, when set, must be the complete Secrets Manager secret ARN including the 6-character suffix.",
    },
    {
        id: "edh-cabundle-secretarn-partition",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.edhApiCaBundleSecretArn", "env.region"],
        appliesWhen: (c) => {
            if (!edhEnabled(c)) return false;
            const arn = g(c, "app.addons.useEdhWorkstations.edhApiCaBundleSecretArn");
            if (isBlank(arn) || !EDH_SECRET_COMPLETE_ARN_PATTERN.test(arn)) return false;
            return edhArnPartitionMismatch(c, arn);
        },
        message:
            "useEdhWorkstations.edhApiCaBundleSecretArn uses a partition that does not match the deployment Region's partition.",
    },
    {
        id: "edh-tlsservername-requires-cabundle",
        severity: "error",
        fieldPaths: [
            "app.addons.useEdhWorkstations.edhApiTlsServerName",
            "app.addons.useEdhWorkstations.edhApiCaBundleSecretArn",
        ],
        appliesWhen: (c) =>
            edhEnabled(c) &&
            !isBlank(g(c, "app.addons.useEdhWorkstations.edhApiTlsServerName")) &&
            isBlank(g(c, "app.addons.useEdhWorkstations.edhApiCaBundleSecretArn")),
        message:
            "useEdhWorkstations.edhApiTlsServerName requires edhApiCaBundleSecretArn: a certificate whose name is not the endpoint host is verified only against a configured bundle.",
    },
    {
        id: "edh-tlsservername-format",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.edhApiTlsServerName"],
        appliesWhen: (c) => {
            if (!edhEnabled(c)) return false;
            const name = g(c, "app.addons.useEdhWorkstations.edhApiTlsServerName");
            if (isBlank(name)) return false;
            return !EDH_TLS_SERVER_NAME_PATTERN.test(String(name).trim());
        },
        message:
            "useEdhWorkstations.edhApiTlsServerName must be a DNS name of at most 253 characters: labels of letters, digits and hyphens, separated by dots.",
    },
    {
        id: "edh-clusterid-required",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.edhClusterId"],
        appliesWhen: (c) =>
            edhEnabled(c) && isBlank(g(c, "app.addons.useEdhWorkstations.edhClusterId")),
        message: "EDH Workstations requires edhClusterId when enabled.",
    },
    {
        id: "edh-clusterid-format",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.edhClusterId"],
        appliesWhen: (c) => {
            if (!edhEnabled(c)) return false;
            const clusterId = g(c, "app.addons.useEdhWorkstations.edhClusterId");
            if (isBlank(clusterId)) return false; // covered by edh-clusterid-required
            return typeof clusterId !== "string" || !EDH_CLUSTER_ID_PATTERN.test(clusterId);
        },
        message:
            "useEdhWorkstations.edhClusterId is the suffix of the edh:visibility:<edhClusterId> resource tag key, so it must be 1-113 characters of letters, digits, spaces and _.:/=+@- .",
    },
    {
        id: "edh-instancetypes-pattern",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.edhAllowedInstanceTypes"],
        appliesWhen: (c) =>
            edhEnabled(c) &&
            edhList(c, "app.addons.useEdhWorkstations.edhAllowedInstanceTypes").some(
                (entry) => typeof entry !== "string" || !EDH_INSTANCE_TYPE_PATTERN.test(entry)
            ),
        message:
            "Every useEdhWorkstations.edhAllowedInstanceTypes entry must be an EC2 instance type (family.size, e.g. g5.2xlarge or c7i.metal-24xl).",
    },
    {
        id: "edh-instancetypes-size",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.edhAllowedInstanceTypes"],
        appliesWhen: (c) =>
            edhEnabled(c) &&
            JSON.stringify(edhList(c, "app.addons.useEdhWorkstations.edhAllowedInstanceTypes"))
                .length > 2048,
        message:
            "useEdhWorkstations.edhAllowedInstanceTypes serializes to more than 2048 bytes. Shorten the list, or leave it empty to defer to the EDH profile.",
    },
    {
        id: "edh-vdi-defaultproject-required",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.vdi.defaultProject"],
        appliesWhen: (c) =>
            edhEnabled(c) && isBlank(g(c, "app.addons.useEdhWorkstations.vdi.defaultProject")),
        message:
            "useEdhWorkstations.vdi.defaultProject must be a non-empty string when the EDH Workstations add-on is enabled.",
    },
    {
        id: "edh-vdi-profileid",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.vdi.virtualDesktopProfileId"],
        appliesWhen: (c) =>
            edhEnabled(c) &&
            !isPositiveInteger(g(c, "app.addons.useEdhWorkstations.vdi.virtualDesktopProfileId")),
        message:
            "useEdhWorkstations.vdi.virtualDesktopProfileId must be a positive integer when the EDH Workstations add-on is enabled.",
    },
    {
        id: "edh-lease-enabled-boolean",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.vdi.lease.enabled"],
        appliesWhen: (c) => {
            const enabled = g(c, "app.addons.useEdhWorkstations.vdi.lease.enabled");
            return edhEnabled(c) && typeof enabled !== "boolean";
        },
        message: "useEdhWorkstations.vdi.lease.enabled must be a boolean.",
    },
    {
        id: "edh-lease-forced-boolean",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.vdi.lease.forced"],
        appliesWhen: (c) => {
            const forced = g(c, "app.addons.useEdhWorkstations.vdi.lease.forced");
            return edhEnabled(c) && typeof forced !== "boolean";
        },
        message: "useEdhWorkstations.vdi.lease.forced must be a boolean.",
    },
    {
        id: "edh-lease-hours-range",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.vdi.lease.leaseHours"],
        appliesWhen: (c) => {
            const hours = g(c, "app.addons.useEdhWorkstations.vdi.lease.leaseHours");
            return edhEnabled(c) && (!Number.isInteger(hours) || hours < 1 || hours > 720);
        },
        message: "useEdhWorkstations.vdi.lease.leaseHours must be an integer between 1 and 720.",
    },
    {
        id: "edh-lease-grace-range",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.vdi.lease.graceHours"],
        appliesWhen: (c) => {
            const hours = g(c, "app.addons.useEdhWorkstations.vdi.lease.graceHours");
            return edhEnabled(c) && (!Number.isInteger(hours) || hours < 0 || hours > 168);
        },
        message: "useEdhWorkstations.vdi.lease.graceHours must be an integer between 0 and 168.",
    },
    {
        id: "edh-registration-revision",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.predefinedImages.registrationRevision"],
        appliesWhen: (c) => {
            if (!edhEnabled(c)) return false;
            const revision = g(
                c,
                "app.addons.useEdhWorkstations.predefinedImages.registrationRevision"
            );
            return isAbsent(revision) ? false : !Number.isInteger(revision) || revision < 0;
        },
        message:
            "useEdhWorkstations.predefinedImages.registrationRevision must be a non-negative integer.",
    },
    {
        id: "edh-sessionbootstrap-ttl",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.sessionBootstrap.sessionKeyTtlMinutes"],
        appliesWhen: (c) =>
            edhEnabled(c) &&
            !!g(c, "app.addons.useEdhWorkstations.sessionBootstrap.enabled") &&
            !isPositiveInteger(
                g(c, "app.addons.useEdhWorkstations.sessionBootstrap.sessionKeyTtlMinutes")
            ),
        message:
            "useEdhWorkstations.sessionBootstrap.sessionKeyTtlMinutes must be an integer of at least 1 when session bootstrap is enabled.",
    },
    {
        id: "edh-sessionbootstrap-prefix",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.sessionBootstrap.secretNamePrefix"],
        // Checked whenever the add-on is on: the prefix scopes the reconciler's revoke grant either way.
        appliesWhen: (c) => {
            if (!edhEnabled(c)) return false;
            const prefix = g(c, "app.addons.useEdhWorkstations.sessionBootstrap.secretNamePrefix");
            return typeof prefix !== "string" || !EDH_SECRET_PREFIX_PATTERN.test(prefix);
        },
        message:
            "useEdhWorkstations.sessionBootstrap.secretNamePrefix must be a Secrets Manager name prefix ending in '/' (letters, digits and /_+=.@-).",
    },
    {
        id: "edh-sessionbootstrap-prefix-length",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.sessionBootstrap.secretNamePrefix"],
        appliesWhen: (c) => {
            if (!edhEnabled(c)) return false;
            const prefix = g(c, "app.addons.useEdhWorkstations.sessionBootstrap.secretNamePrefix");
            return (
                typeof prefix === "string" && prefix.length > EDH_SESSION_SECRET_PREFIX_MAX_LENGTH
            );
        },
        message: `useEdhWorkstations.sessionBootstrap.secretNamePrefix must be at most ${EDH_SESSION_SECRET_PREFIX_MAX_LENGTH} characters: the shipped Linux recipes' session bootstrap component resolves it into a document the 16,000-character component limit applies to.`,
    },
    {
        id: "edh-sessionbootstrap-prefix-covers-secret",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.sessionBootstrap.secretNamePrefix"],
        appliesWhen: (c) => {
            if (!edhEnabled(c)) return false;
            const prefix = g(c, "app.addons.useEdhWorkstations.sessionBootstrap.secretNamePrefix");
            if (typeof prefix !== "string" || prefix === "") return false;
            return ["edhAdminApiKeySecretArn", "edhApiCaBundleSecretArn"].some((field) => {
                const arn = g(c, `app.addons.useEdhWorkstations.${field}`);
                const at = typeof arn === "string" ? arn.indexOf(":secret:") : -1;
                return at >= 0 && arn.slice(at + ":secret:".length).startsWith(prefix);
            });
        },
        message:
            "useEdhWorkstations.sessionBootstrap.secretNamePrefix must not cover the edhAdminApiKeySecretArn or edhApiCaBundleSecretArn secret: the add-on's Lambdas may overwrite and delete every secret under the prefix.",
    },
    {
        id: "edh-sessionbootstrap-rolearns-required",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.sessionBootstrap.workstationInstanceRoleArns"],
        appliesWhen: (c) =>
            edhEnabled(c) &&
            !!g(c, "app.addons.useEdhWorkstations.sessionBootstrap.enabled") &&
            edhList(c, "app.addons.useEdhWorkstations.sessionBootstrap.workstationInstanceRoleArns")
                .length === 0,
        message:
            "useEdhWorkstations.sessionBootstrap.workstationInstanceRoleArns must list at least one IAM role ARN when session bootstrap is enabled; the workstation instance role reads the per-session secret.",
    },
    {
        id: "edh-sessionbootstrap-rolearns-format",
        severity: "error",
        fieldPaths: [
            "app.addons.useEdhWorkstations.sessionBootstrap.workstationInstanceRoleArns",
            "env.region",
        ],
        appliesWhen: (c) =>
            edhEnabled(c) &&
            !!g(c, "app.addons.useEdhWorkstations.sessionBootstrap.enabled") &&
            edhList(
                c,
                "app.addons.useEdhWorkstations.sessionBootstrap.workstationInstanceRoleArns"
            ).some(
                (arn) =>
                    typeof arn !== "string" ||
                    !EDH_ROLE_ARN_PATTERN.test(arn) ||
                    edhArnPartitionMismatch(c, arn)
            ),
        message:
            "Every useEdhWorkstations.sessionBootstrap.workstationInstanceRoleArns entry must be an IAM role ARN (arn:<partition>:iam::<account>:role/<name>) in the deployment Region's partition; the path and name use only letters, digits and _+=,.@/- .",
    },
    {
        id: "edh-predefined-vdi-entries",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.predefinedImages.registrationRevision"],
        appliesWhen: (c) => edhEnabled(c) && edhVdiImagesInvalid(c),
        message:
            "A useEdhWorkstations.predefinedImages.vdi[] entry is invalid: amiId must be ami-…, stackName (unique) and baseOs non-empty, rootSize and virtualDesktopProfileId positive integers. Edit the list in the downloaded config.json.",
    },
    {
        id: "edh-simulation-ami-entries",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.predefinedImages.registrationRevision"],
        appliesWhen: (c) => edhEnabled(c) && edhSimulationAmisInvalid(c),
        message:
            "A useEdhWorkstations.predefinedImages.simulationAmis[] entry is invalid: amiId must be ami-…, stackName (unique) non-empty, rootSize and targetNodeProfileId positive integers, targetNodeUserDataId (optional, EDH template 1 when omitted) a positive integer. Edit the list in the downloaded config.json.",
    },
    {
        id: "edh-build-image-entries",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.containers.enabled"],
        appliesWhen: (c) => edhEnabled(c) && edhBuildImagesInvalid(c),
        message:
            "A useEdhWorkstations.containers.buildAndPushImages[] entry is invalid: name must be lowercase letters, digits and hyphens (unique); dockerfileDir must be relative to the repository root without '..'; platform, when set, linux/amd64 or linux/arm64. Whether the directory exists is checked at cdk synth.",
    },
    {
        id: "edh-existing-ecr-entries",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.containers.enabled"],
        appliesWhen: (c) => edhEnabled(c) && edhExistingEcrImagesInvalid(c),
        message:
            "A useEdhWorkstations.containers.existingEcrImages[] entry is invalid: imageUri must be a placeholder-free Amazon ECR image URI (unique) and displayName non-empty.",
    },
    {
        id: "edh-jobdefinition-entries",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.containers.enabled"],
        appliesWhen: (c) => edhEnabled(c) && edhJobDefinitionsInvalid(c),
        message:
            "A useEdhWorkstations.containers.jobDefinitions[] entry is invalid: name (unique) must begin with edh-, the only job definitions EDH's controller role may submit to; imageRef must name a buildAndPushImages[].name or an existingEcrImages[].imageUri; vcpus and memory positive integers; gpu, when set, a non-negative integer; platform, when set, EC2 or FARGATE.",
    },

    // ----- EDH container image builds outside the CodeBuild partitions
    // (edhWorkstationsConfig.ts: "in-account CodeBuild container build is supported in the aws and aws-us-gov") -----
    {
        id: "edh-containers-codebuild-partition",
        severity: "warning",
        fieldPaths: ["env.region", "app.addons.useEdhWorkstations.containers.buildAndPushImages"],
        appliesWhen: (c) => {
            const partition = partitionForRegionName(g(c, "env.region"));
            const images = g(c, "app.addons.useEdhWorkstations.containers.buildAndPushImages");
            return (
                partition !== undefined &&
                partition !== "aws" &&
                partition !== "aws-us-gov" &&
                !!g(c, "app.addons.useEdhWorkstations.enabled") &&
                !!g(c, "app.addons.useEdhWorkstations.containers.enabled") &&
                Array.isArray(images) &&
                images.length > 0
            );
        },
        message:
            "addons.useEdhWorkstations.containers.buildAndPushImages names images to build with AWS CodeBuild, " +
            "which is supported in the aws and aws-us-gov partitions only. Build the images out of band and " +
            "list them under containers.existingEcrImages.",
    },
    {
        id: "edh-containers-imagepullrolearns-format",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.containers.imagePullRoleArns", "env.region"],
        appliesWhen: (c) =>
            edhEnabled(c) &&
            edhList(c, "app.addons.useEdhWorkstations.containers.imagePullRoleArns").some(
                (arn) =>
                    typeof arn !== "string" ||
                    !EDH_ROLE_ARN_PATTERN.test(arn) ||
                    edhArnPartitionMismatch(c, arn)
            ),
        message:
            "Every useEdhWorkstations.containers.imagePullRoleArns entry must be an IAM role ARN (arn:<partition>:iam::<account>:role/<name>) in the deployment Region's partition; the path and name use only letters, digits and _+=,.@/- .",
    },
    {
        id: "edh-amibuilds-requires-addon",
        severity: "error",
        fieldPaths: [
            "app.addons.useEdhWorkstations.enabled",
            "app.addons.useEdhWorkstations.amiBuilds.enabled",
        ],
        appliesWhen: (c) =>
            !edhEnabled(c) && g(c, "app.addons.useEdhWorkstations.amiBuilds.enabled"),
        message: "amiBuilds.enabled requires app.addons.useEdhWorkstations.enabled.",
    },

    // ----- EDH AMI build settings (edhWorkstationsConfig.ts: "Validates the AMI build settings") -----
    // Absent values are backfilled by getConfig() (enabled false, includeShippedRecipes true).
    {
        id: "edh-amibuilds-enabled-boolean",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.amiBuilds.enabled"],
        appliesWhen: (c) => {
            const enabled = g(c, "app.addons.useEdhWorkstations.amiBuilds.enabled");
            return edhEnabled(c) && !isAbsent(enabled) && typeof enabled !== "boolean";
        },
        message: "amiBuilds.enabled must be a boolean.",
    },
    {
        id: "edh-amibuilds-shippedrecipes-boolean",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.amiBuilds.includeShippedRecipes"],
        appliesWhen: (c) => {
            const shipped = g(c, "app.addons.useEdhWorkstations.amiBuilds.includeShippedRecipes");
            return edhEnabled(c) && !isAbsent(shipped) && typeof shipped !== "boolean";
        },
        message: "amiBuilds.includeShippedRecipes must be a boolean.",
    },
    {
        id: "edh-amibuilds-timeout-range",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.amiBuilds.buildTimeoutMinutes"],
        appliesWhen: (c) => {
            const timeout = g(c, "app.addons.useEdhWorkstations.amiBuilds.buildTimeoutMinutes");
            return edhEnabled(c) && (!Number.isInteger(timeout) || timeout < 30 || timeout > 720);
        },
        message: "amiBuilds.buildTimeoutMinutes must be an integer between 30 and 720.",
    },
    {
        id: "edh-amibuilds-keepversions-range",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.amiBuilds.keepVersionsDefault"],
        appliesWhen: (c) => {
            const keep = g(c, "app.addons.useEdhWorkstations.amiBuilds.keepVersionsDefault");
            return edhEnabled(c) && (!Number.isInteger(keep) || keep < 1 || keep > 20);
        },
        message: "amiBuilds.keepVersionsDefault must be an integer between 1 and 20.",
    },
    {
        id: "edh-amibuilds-subnet-format",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.amiBuilds.buildSubnetId"],
        appliesWhen: (c) => {
            const subnetId = g(c, "app.addons.useEdhWorkstations.amiBuilds.buildSubnetId");
            return (
                edhEnabled(c) &&
                typeof subnetId === "string" &&
                subnetId !== "" &&
                !/^subnet-[0-9a-f]{8,17}$/.test(subnetId)
            );
        },
        message: "amiBuilds.buildSubnetId must be a subnet id (subnet-...) or empty.",
    },

    // ----- EDH AMI recipe references (edhWorkstationsConfig.ts: "Validates the recipe references") -----
    {
        id: "edh-amirecipes-entries",
        severity: "error",
        fieldPaths: ["app.addons.useEdhWorkstations.predefinedImages.registrationRevision"],
        appliesWhen: (c) => edhEnabled(c) && edhAmiRecipesInvalid(c),
        message:
            "useEdhWorkstations.predefinedImages.amiRecipes must be an array whose entries carry a recipeDir relative to the repository root, without '..' and unique in the list, and a boolean buildOnDeploy. Each directory's recipe.json (including that a Windows recipe is x86_64 and that an SSM parentImage is a public AMI parameter under ssm:/aws/service/), and whether two directories declare the same recipeId, is checked at cdk synth when amiBuilds.enabled is true. Edit the list in the downloaded config.json.",
    },

    // ===== Warnings (edhWorkstationsConfig.ts console.warn advisories) =====
    {
        id: "edh-shipped-recipes-no-session-bootstrap-warn",
        severity: "warning",
        fieldPaths: [
            "app.addons.useEdhWorkstations.amiBuilds.includeShippedRecipes",
            "app.addons.useEdhWorkstations.sessionBootstrap.enabled",
        ],
        appliesWhen: (c) =>
            edhEnabled(c) &&
            !!g(c, "app.addons.useEdhWorkstations.amiBuilds.enabled") &&
            !!g(c, "app.addons.useEdhWorkstations.amiBuilds.includeShippedRecipes") &&
            !g(c, "app.addons.useEdhWorkstations.sessionBootstrap.enabled"),
        message:
            "useEdhWorkstations.amiBuilds.includeShippedRecipes seeds the Linux desktop recipes (al2023-desktop, isaacsim) while useEdhWorkstations.sessionBootstrap.enabled is false. Their desktop sign-in has no session secret to read, so a desktop built from them starts with no signed-in VAMS CLI.",
    },
];

/** Session bootstrap is on while the add-on is enabled. */
function edhBootstrap(c: ConfigShape): boolean {
    return (
        edhEnabled(c) && !!getByPath(c, "app.addons.useEdhWorkstations.sessionBootstrap.enabled")
    );
}

/** Container jobs are on while the add-on is enabled. */
function edhContainers(c: ConfigShape): boolean {
    return edhEnabled(c) && !!getByPath(c, "app.addons.useEdhWorkstations.containers.enabled");
}

/** The add-on is enabled (edhWorkstationsConfig.ts: "EDH Workstations Configuration Validation"). */
function edhEnabled(cfg: ConfigShape): boolean {
    return !!g(cfg, "app.addons.useEdhWorkstations.enabled");
}

/** An array-valued EDH field, or `[]` for anything that is not an array. */
function edhList(cfg: ConfigShape, path: string): any[] {
    const value = g(cfg, path);
    return Array.isArray(value) ? value : [];
}

function isPositiveInteger(value: unknown): boolean {
    return typeof value === "number" && Number.isInteger(value) && value >= 1;
}

/**
 * True when an ARN's partition segment differs from the partition implied by `env.region`
 * (edhWorkstationsConfig.ts: `validateEdhSecretCompleteArn`). Silent when the region is unset.
 */
function edhArnPartitionMismatch(cfg: ConfigShape, arn: unknown): boolean {
    const partition = partitionForRegionName(g(cfg, "env.region"));
    if (partition === undefined || typeof arn !== "string") return false;
    return arn.split(":")[1] !== partition;
}

/** The loopback / link-local hosts `validateInternalHttpsEndpoint` blocks (edhWorkstationsConfig.ts). */
function edhEndpointHostBlocked(value: unknown): boolean {
    let host: string;
    try {
        host = new URL(String(value)).hostname.toLowerCase().replace(/^\[|\]$/g, "");
    } catch {
        return false;
    }
    return (
        host === "localhost" ||
        host.endsWith(".localhost") ||
        host === "::1" ||
        /^127\./.test(host) ||
        /^169\.254\./.test(host)
    );
}

function edhIsNonEmptyString(value: unknown): boolean {
    return typeof value === "string" && value.trim() !== "" && value !== "UNDEFINED";
}

/** Mirrors `validateEdhPredefinedVdiImages`: true when any entry is invalid. */
function edhVdiImagesInvalid(cfg: ConfigShape): boolean {
    const seen = new Set<string>();
    return edhList(cfg, "app.addons.useEdhWorkstations.predefinedImages.vdi").some((image) => {
        if (typeof image?.amiId !== "string" || !EDH_AMI_ID_PATTERN.test(image.amiId)) return true;
        if (!edhIsNonEmptyString(image.stackName) || seen.has(image.stackName)) return true;
        seen.add(image.stackName);
        return (
            !edhIsNonEmptyString(image.baseOs) ||
            !isPositiveInteger(image.rootSize) ||
            !isPositiveInteger(image.virtualDesktopProfileId)
        );
    });
}

/** Mirrors `validateEdhSimulationAmis`. */
function edhSimulationAmisInvalid(cfg: ConfigShape): boolean {
    const seen = new Set<string>();
    return edhList(cfg, "app.addons.useEdhWorkstations.predefinedImages.simulationAmis").some(
        (ami) => {
            if (typeof ami?.amiId !== "string" || !EDH_AMI_ID_PATTERN.test(ami.amiId)) return true;
            if (!edhIsNonEmptyString(ami.stackName) || seen.has(ami.stackName)) return true;
            seen.add(ami.stackName);
            return (
                !isPositiveInteger(ami.rootSize) ||
                !isPositiveInteger(ami.targetNodeProfileId) ||
                (ami.targetNodeUserDataId !== undefined &&
                    !isPositiveInteger(ami.targetNodeUserDataId))
            );
        }
    );
}

/**
 * Mirrors the shape half of `validateEdhBuildImages`. Whether `dockerfileDir` exists is a synth-time
 * check against the repository and cannot be evaluated in the browser.
 */
function edhBuildImagesInvalid(cfg: ConfigShape): boolean {
    const seen = new Set<string>();
    return edhList(cfg, "app.addons.useEdhWorkstations.containers.buildAndPushImages").some(
        (image) => {
            if (typeof image?.name !== "string" || !EDH_IMAGE_NAME_PATTERN.test(image.name))
                return true;
            if (seen.has(image.name)) return true;
            seen.add(image.name);
            if (!edhIsNonEmptyString(image.dockerfileDir)) return true;
            const dir: string = image.dockerfileDir;
            if (/^[A-Za-z]:[\\/]/.test(dir) || /^[\\/]/.test(dir)) return true;
            if (dir.split(/[\\/]/).includes("..")) return true;
            return image.platform !== undefined && !EDH_BUILD_PLATFORMS.includes(image.platform);
        }
    );
}

/** Mirrors `validateEdhExistingEcrImages`. */
function edhExistingEcrImagesInvalid(cfg: ConfigShape): boolean {
    const seen = new Set<string>();
    return edhList(cfg, "app.addons.useEdhWorkstations.containers.existingEcrImages").some(
        (image) => {
            if (!edhIsNonEmptyString(image?.imageUri)) return true;
            if (EDH_TEMPLATE_PLACEHOLDERS.some((token) => image.imageUri.includes(token)))
                return true;
            if (!EDH_ECR_IMAGE_URI_PATTERN.test(image.imageUri)) return true;
            if (seen.has(image.imageUri)) return true;
            seen.add(image.imageUri);
            return !edhIsNonEmptyString(image.displayName);
        }
    );
}

/**
 * Mirrors `validateEdhJobDefinitions`, including the `edh-` name prefix and `imageRef` resolution
 * against both image lists.
 */
function edhJobDefinitionsInvalid(cfg: ConfigShape): boolean {
    const refs = new Set<string>([
        ...edhList(cfg, "app.addons.useEdhWorkstations.containers.buildAndPushImages").map(
            (i) => i?.name
        ),
        ...edhList(cfg, "app.addons.useEdhWorkstations.containers.existingEcrImages").map(
            (i) => i?.imageUri
        ),
    ]);
    const seen = new Set<string>();
    return edhList(cfg, "app.addons.useEdhWorkstations.containers.jobDefinitions").some((job) => {
        if (!edhIsNonEmptyString(job?.name) || seen.has(job.name)) return true;
        if (!job.name.startsWith(EDH_BATCH_NAME_PREFIX)) return true;
        seen.add(job.name);
        if (!edhIsNonEmptyString(job.imageRef) || !refs.has(job.imageRef)) return true;
        if (!isPositiveInteger(job.vcpus) || !isPositiveInteger(job.memory)) return true;
        if (job.gpu !== undefined && (!Number.isInteger(job.gpu) || job.gpu < 0)) return true;
        return job.platform !== undefined && !EDH_JOB_PLATFORMS.includes(job.platform);
    });
}

/**
 * Mirrors the shape half of `validateEdhAmiRecipes`. Loading each directory's `recipe.json`, and the
 * duplicate-`recipeId` check that reads it, runs at cdk synth against the repository and cannot be
 * evaluated in the browser. An absent list is backfilled to `[]` by `getConfig()`.
 */
function edhAmiRecipesInvalid(cfg: ConfigShape): boolean {
    const refs = g(cfg, "app.addons.useEdhWorkstations.predefinedImages.amiRecipes");
    if (isAbsent(refs)) return false;
    if (!Array.isArray(refs)) return true;
    const seen = new Set<string>();
    return refs.some((ref) => {
        if (!edhIsNonEmptyString(ref?.recipeDir)) return true;
        const raw: string = ref.recipeDir;
        if (/^[A-Za-z]:[\\/]/.test(raw) || /^[\\/]/.test(raw)) return true;
        if (raw.split(/[\\/]/).includes("..")) return true;
        if (typeof ref.buildOnDeploy !== "boolean") return true;
        const dir = raw.replace(/\\/g, "/").replace(/\/+$/, "");
        if (seen.has(dir)) return true;
        seen.add(dir);
        return false;
    });
}

/**
 * Mirrors the normalization `getConfig()` performs on the add-on block (edhWorkstationsConfig.ts:
 * "useEdhWorkstations.containers.enabled is ignored because useEdhWorkstations.enabled is false") —
 * the container build resources and the EDH_CONTAINERS flag read the field directly. Records each
 * forced field in `changes`.
 */
export function applyEdhWorkstationsDerived(
    input: ConfigShape,
    changes: DerivedChange[]
): ConfigShape {
    let config = input;
    if (
        !getByPath(config, "app.addons.useEdhWorkstations.enabled") &&
        getByPath(config, "app.addons.useEdhWorkstations.containers.enabled") === true
    ) {
        config = setByPath(config, "app.addons.useEdhWorkstations.containers.enabled", false);
        changes.push({
            path: "app.addons.useEdhWorkstations.containers.enabled",
            to: false,
            reason: "EDH container jobs are part of the EDH Workstations add-on; getConfig() resolves this to false while useEdhWorkstations.enabled is false.",
        });
    }

    return config;
}
