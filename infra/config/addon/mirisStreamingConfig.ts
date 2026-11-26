/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Configuration of the Miris Spatial Streaming add-on, `app.addons.useMirisStreaming`, and its upload
 * pipeline, `app.addons.useMirisStreaming.upload`: the interface, the defaults `getConfig()` fills in,
 * the features that require a VPC, and the validation. `addonConfig.ts` calls these from `getConfig()`.
 */
import type { Config } from "../config";

// ============================================================================================
// Constants that shape the deployment
//
// Every value here reaches synthesized infrastructure, as a default the add-on stacks read.
// ============================================================================================

/**
 * File extensions the GLOBAL "Miris stream" file metadata schema accepts: the
 * `fileKeyTypeRestriction` of
 * infra/lib/nestedStacks/addon/miris/constructs/dynamodb-metadataschema-miris-stream.json. The Miris
 * upload pipeline records its result in that schema, so its trigger extensions must be a subset.
 */
export const MIRIS_STREAM_SCHEMA_EXTENSIONS: readonly string[] = [
    ".usd",
    ".usda",
    ".usdc",
    ".usdz",
];

/**
 * Defaults for `app.addons.useMirisStreaming`, each applied when its own field is absent. The
 * `upload` block has its own defaults (MIRIS_UPLOAD_DEFAULTS).
 */
const MIRIS_STREAMING_DEFAULTS: Omit<ConfigMirisStreaming, "upload"> = {
    enabled: false,
    viewerKeyTtlMinutes: 15,
    apiKeySecretArn: "",
    mirisApiBaseUrl: "https://app.miris.com",
};

/** Defaults for `app.addons.useMirisStreaming.upload`, each applied when its own field is absent. */
const MIRIS_UPLOAD_DEFAULTS: ConfigMirisStreaming["upload"] = {
    enabled: false,
    autoRegisterWithVAMS: true,
    autoRegisterAutoTriggerOnFileUpload: false,
    triggerExtensions: MIRIS_STREAM_SCHEMA_EXTENSIONS.join(","),
    taskTimeoutSeconds: 1800,
    maxAssetSizeBytes: 5_000_000_000,
};

// ============================================================================================
// Constants used only to check configuration
//
// Nothing here reaches synthesized infrastructure. Each one exists so getConfig() can reject a bad
// value.
// ============================================================================================

/**
 * A complete Secrets Manager secret ARN in the commercial partition, the only one the Miris add-on
 * deploys to, including the six-character suffix Secrets Manager appends to the secret name. IAM
 * grants name `app.addons.useMirisStreaming.apiKeySecretArn` verbatim, so a partial ARN without the
 * suffix matches no secret.
 */
const MIRIS_SECRET_ARN_PATTERN =
    /^arn:aws:secretsmanager:[a-z0-9-]+:\d{12}:secret:[A-Za-z0-9/_+=.@-]+-[A-Za-z0-9]{6}$/;

/** `app.addons.useMirisStreaming`. */
export interface ConfigMirisStreaming {
    enabled: boolean;
    viewerKeyTtlMinutes: number;
    apiKeySecretArn: string;
    mirisApiBaseUrl: string;
    upload: {
        enabled: boolean;
        autoRegisterWithVAMS: boolean;
        autoRegisterAutoTriggerOnFileUpload: boolean;
        triggerExtensions: string;
        taskTimeoutSeconds: number;
        maxAssetSizeBytes: number;
    };
}

/** A string setting the operator left unset: missing, empty, or the template placeholder. */
function isUnsetConfigString(value: unknown): boolean {
    return value === undefined || value === null || value === "" || value === "UNDEFINED";
}

/**
 * Splits `app.addons.useMirisStreaming.upload.triggerExtensions` into its extensions, or returns undefined
 * when the value is not a comma-separated list of lowercase extensions such as ".usd,.usdz".
 */
function parseMirisTriggerExtensions(value: unknown): string[] | undefined {
    if (typeof value !== "string") return undefined;
    const extensions = value.split(",");
    return extensions.every((extension) => /^\.[a-z0-9]+$/.test(extension))
        ? extensions
        : undefined;
}

/** True for a whole number greater than zero. */
function isPositiveInteger(value: unknown): boolean {
    return typeof value === "number" && Number.isInteger(value) && value > 0;
}

/**
 * Requires a configured outbound endpoint to be HTTPS and to point somewhere outside the deployment.
 *
 * `new URL(x)` succeeds for `http://169.254.169.254/` and `http://internal.corp/token` alike, so
 * parseability establishes nothing about either confidentiality or destination. Applied to endpoints a
 * VAMS Lambda sends credentials to.
 */
function validateOutboundHttpsEndpoint(endpoint: string, configPath: string) {
    const url = new URL(endpoint);
    if (url.protocol !== "https:") {
        throw new Error(
            `Configuration Error: ${configPath} must use https, or the credentials VAMS sends to it ` +
                `travel in cleartext. Got: ${endpoint}`
        );
    }

    const host = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
    const blocked =
        host === "localhost" ||
        host.endsWith(".localhost") ||
        host.endsWith(".local") ||
        host.endsWith(".internal") ||
        host === "::1" ||
        /^127\./.test(host) ||
        /^169\.254\./.test(host) || // link-local, including the instance metadata endpoint
        /^10\./.test(host) ||
        /^192\.168\./.test(host) ||
        /^172\.(1[6-9]|2\d|3[01])\./.test(host) ||
        /^(fc|fd)[0-9a-f]{2}:/.test(host); // IPv6 unique-local
    if (blocked) {
        throw new Error(
            `Configuration Error: ${configPath} resolves to a loopback, link-local, or private ` +
                `address (${url.hostname}). This endpoint is called by a Lambda that can read the ` +
                `VAMS asset buckets, so it must name an external service. Got: ${endpoint}`
        );
    }
}

/** Fills in `app.addons.useMirisStreaming` and its `upload` block, field by field. */
export function applyMirisStreamingDefaults(config: Config): void {
    if (config.app.addons.useMirisStreaming == undefined) {
        config.app.addons.useMirisStreaming = {
            ...MIRIS_STREAMING_DEFAULTS,
            upload: { ...MIRIS_UPLOAD_DEFAULTS },
        };
    }
    const mirisStreaming = config.app.addons.useMirisStreaming as unknown as Record<
        string,
        unknown
    >;
    for (const [field, value] of Object.entries(MIRIS_STREAMING_DEFAULTS)) {
        if (mirisStreaming[field] == undefined) {
            mirisStreaming[field] = value;
        }
    }

    if (config.app.addons.useMirisStreaming.upload == undefined) {
        config.app.addons.useMirisStreaming.upload = { ...MIRIS_UPLOAD_DEFAULTS };
    }
    const mirisUpload = config.app.addons.useMirisStreaming.upload as unknown as Record<
        string,
        unknown
    >;
    for (const [field, value] of Object.entries(MIRIS_UPLOAD_DEFAULTS)) {
        if (mirisUpload[field] == undefined) {
            mirisUpload[field] = value;
        }
    }
}

/** The enabled Miris features that require a VPC, named as getConfig() reports them. */
export function mirisVpcRequiringFeatures(config: Config): string[] {
    return config.app.addons.useMirisStreaming.upload.enabled
        ? ["addons.useMirisStreaming.upload"]
        : [];
}

/** Validates `app.addons.useMirisStreaming` and its `upload` block. */
export function validateMirisStreamingConfig(config: Config): void {
    if (config.app.addons.useMirisStreaming.enabled) {
        const miris = config.app.addons.useMirisStreaming;

        // Miris is a SaaS service reached at *.miris.com over the public internet. Keyed on the
        // resolved partition, so a GovCloud or EU Sovereign Cloud deployment is rejected whatever
        // app.govCloud.enabled says.
        if (config.env.partition !== "aws") {
            throw new Error(
                "Configuration Error: addons.useMirisStreaming is enabled while deploying to the " +
                    `'${config.env.partition}' partition. Miris is reached at *.miris.com over the ` +
                    "public internet and is supported in the commercial partition only. Set " +
                    "app.addons.useMirisStreaming.enabled to false."
            );
        }

        if (!config.app.webUi.allowUnsafeEvalFeatures) {
            throw new Error(
                "Configuration Error: addons.useMirisStreaming requires " +
                    "app.webUi.allowUnsafeEvalFeatures = true. The Miris web SDK compiles code at " +
                    "runtime, the same trust boundary the Cesium and Needle USD viewer plugins " +
                    "require. Consult your security team before enabling."
            );
        }

        // With useForAllLambdas the add-on's functions are placed in the isolated subnets, which
        // have no route to Miris.
        if (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas) {
            throw new Error(
                "Configuration Error: addons.useMirisStreaming cannot be combined with " +
                    "app.useGlobalVpc.useForAllLambdas. That setting places the add-on's Lambda " +
                    "functions in the VPC's isolated subnets, which have no route to the Miris API " +
                    "at *.miris.com. Set app.useGlobalVpc.useForAllLambdas to false, or " +
                    "app.addons.useMirisStreaming.enabled to false."
            );
        }

        const viewerKeyTtlMinutes: unknown = miris.viewerKeyTtlMinutes;
        if (
            typeof viewerKeyTtlMinutes !== "number" ||
            !Number.isInteger(viewerKeyTtlMinutes) ||
            viewerKeyTtlMinutes < 1 ||
            viewerKeyTtlMinutes > 60
        ) {
            throw new Error(
                "Configuration Error: addons.useMirisStreaming.viewerKeyTtlMinutes must be a whole " +
                    `number of minutes from 1 to 60; got ${JSON.stringify(viewerKeyTtlMinutes)}.`
            );
        }

        // The add-on's Lambdas send the Integration Key to this URL.
        if (isUnsetConfigString(miris.mirisApiBaseUrl)) {
            throw new Error(
                "Configuration Error: addons.useMirisStreaming requires mirisApiBaseUrl when enabled"
            );
        }
        if (String(miris.mirisApiBaseUrl) !== String(miris.mirisApiBaseUrl).trim()) {
            throw new Error(
                "Configuration Error: addons.useMirisStreaming.mirisApiBaseUrl has leading or " +
                    `trailing whitespace. Remove it. Got: ${JSON.stringify(miris.mirisApiBaseUrl)}`
            );
        }
        try {
            new URL(miris.mirisApiBaseUrl);
        } catch (e) {
            throw new Error(
                "Configuration Error: addons.useMirisStreaming.mirisApiBaseUrl must be a valid URL. " +
                    `Got: ${miris.mirisApiBaseUrl}`
            );
        }
        validateOutboundHttpsEndpoint(
            miris.mirisApiBaseUrl,
            "addons.useMirisStreaming.mirisApiBaseUrl"
        );

        // The viewer key endpoint checks the Miris asset's tags with the Integration Key before it
        // mints a key with it.
        if (isUnsetConfigString(miris.apiKeySecretArn)) {
            throw new Error(
                "Configuration Error: addons.useMirisStreaming requires apiKeySecretArn, the Secrets " +
                    "Manager ARN of the Miris Integration Key. The viewer key endpoint uses it to " +
                    "check that a Miris asset is bound to the VAMS asset and to mint viewer keys."
            );
        }
        if (!MIRIS_SECRET_ARN_PATTERN.test(String(miris.apiKeySecretArn))) {
            throw new Error(
                "Configuration Error: addons.useMirisStreaming.apiKeySecretArn must be the complete " +
                    "ARN of a Secrets Manager secret, including the six-character suffix Secrets " +
                    "Manager appends to the secret name " +
                    "in the commercial partition " +
                    "(arn:aws:secretsmanager:<region>:<account>:secret:<name>-AbCdEf); got " +
                    `"${miris.apiKeySecretArn}".`
            );
        }
    }

    // Miris upload pipeline validation. The pipeline reads the Integration Key secret and the Miris
    // API base URL from the Miris Spatial Streaming add-on, and records the Miris asset UUID in file
    // metadata that the add-on's viewer streams.
    if (config.app.addons.useMirisStreaming.upload.enabled) {
        const upload = config.app.addons.useMirisStreaming.upload;
        const miris = config.app.addons.useMirisStreaming;

        if (!miris.enabled) {
            throw new Error(
                "Configuration Error: addons.useMirisStreaming.upload requires " +
                    "app.addons.useMirisStreaming.enabled = true. The pipeline reads the Miris " +
                    "Integration Key and API base URL from the add-on, and the assets it uploads are " +
                    "viewed through the add-on's viewer."
            );
        }
        for (const field of ["taskTimeoutSeconds", "maxAssetSizeBytes"] as const) {
            const value: unknown = upload[field];
            if (!isPositiveInteger(value)) {
                throw new Error(
                    `Configuration Error: addons.useMirisStreaming.upload.${field} must be a positive ` +
                        `whole number; got ${JSON.stringify(value)}.`
                );
            }
        }
        if (typeof upload.triggerExtensions === "string" && /\s/.test(upload.triggerExtensions)) {
            throw new Error(
                "Configuration Error: addons.useMirisStreaming.upload.triggerExtensions must not contain " +
                    'whitespace. List the extensions separated by commas only, such as ".usd,.usdz"; ' +
                    `got ${JSON.stringify(upload.triggerExtensions)}.`
            );
        }
        const triggerExtensions = parseMirisTriggerExtensions(upload.triggerExtensions);
        if (triggerExtensions == undefined) {
            throw new Error(
                "Configuration Error: addons.useMirisStreaming.upload.triggerExtensions must be a " +
                    'comma-separated list of lowercase file extensions, such as ".usd,.usdz"; got ' +
                    `${JSON.stringify(upload.triggerExtensions)}.`
            );
        }
        const unsupportedExtensions = triggerExtensions.filter(
            (extension) => !MIRIS_STREAM_SCHEMA_EXTENSIONS.includes(extension)
        );
        if (unsupportedExtensions.length > 0) {
            throw new Error(
                "Configuration Error: addons.useMirisStreaming.upload.triggerExtensions may only list " +
                    "extensions the Miris stream file metadata schema accepts " +
                    `(${MIRIS_STREAM_SCHEMA_EXTENSIONS.join(", ")}); got ` +
                    `${unsupportedExtensions.join(", ")}.`
            );
        }
    }

    const upload = config.app.addons.useMirisStreaming.upload;

    // The upload pipeline's compute runs in the private pipeline subnets, so an imported VPC must
    // name private subnets.
    const importedVpc =
        !!config.app.useGlobalVpc.optionalExternalVpcId &&
        config.app.useGlobalVpc.optionalExternalVpcId !== "UNDEFINED" &&
        config.app.useGlobalVpc.optionalExternalVpcId.trim() !== "";
    const importedPrivateSubnetIds = config.app.useGlobalVpc.optionalExternalPrivateSubnetIds;
    if (
        upload.enabled &&
        importedVpc &&
        (!importedPrivateSubnetIds ||
            importedPrivateSubnetIds === "UNDEFINED" ||
            importedPrivateSubnetIds.trim() === "")
    ) {
        throw new Error(
            "Configuration Error: Must define at least one private subnet ID when using a pipeline that requires private subnets."
        );
    }

    // The upload trigger ships with the VamsSchemaRegistration custom resource, which is created only
    // when autoRegisterWithVAMS is true, so an armed trigger on an unregistered pipeline is discarded.
    if (
        upload.enabled &&
        upload.autoRegisterAutoTriggerOnFileUpload &&
        upload.autoRegisterWithVAMS !== true
    ) {
        console.warn(
            "Configuration Warning: addons.useMirisStreaming.upload.autoRegisterAutoTriggerOnFileUpload is true but " +
                "autoRegisterWithVAMS is not, so no registration and no upload trigger are created. " +
                "Set autoRegisterWithVAMS to true to arm the trigger."
        );
    }
}
