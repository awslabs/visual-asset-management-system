/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The ConfigBuilder mirror of the Miris Spatial Streaming add-on block, `app.addons.useMirisStreaming`,
 * and its upload pipeline: its form fields, its entry in the VPC-requiring features, the rules ported
 * from infra/config/addon/mirisStreamingConfig.ts and its template defaults. `index.ts` joins each into
 * the core tables.
 *
 * Each rule section names the block it mirrors by quoting that block's leading comment or its
 * error-message text; search mirisStreamingConfig.ts for the quoted string to find it.
 */

import type { ConfigShape, FieldMeta, Rule } from "../types";
import { g, isAbsent, isBlank, isUnset, isValidUrl, PARTITION_DNS_SUFFIXES } from "../ruleHelpers";
import type { VpcRequiringFeature } from "../ruleHelpers";

/** The deployment Region is in the commercial partition (an unset Region counts as commercial). */
function isCommercialPartition(cfg: ConfigShape): boolean {
    const region = g(cfg, "env.region");
    if (isUnset(region) || typeof region !== "string") return true;
    return !PARTITION_DNS_SUFFIXES.some((entry) => region.startsWith(entry.prefix));
}

/**
 * True when an endpoint's host is loopback, link-local, or private (mirisStreamingConfig.ts:
 * `validateOutboundHttpsEndpoint`). False for an unparseable URL, which a parseability rule reports.
 */
function isPrivateEndpointHost(endpoint: string): boolean {
    let host: string;
    try {
        host = new URL(endpoint).hostname.toLowerCase().replace(/^\[|\]$/g, "");
    } catch {
        return false;
    }
    return (
        host === "localhost" ||
        host.endsWith(".localhost") ||
        host.endsWith(".local") ||
        host.endsWith(".internal") ||
        host === "::1" ||
        /^127\./.test(host) ||
        /^169\.254\./.test(host) ||
        /^10\./.test(host) ||
        /^192\.168\./.test(host) ||
        /^172\.(1[6-9]|2\d|3[01])\./.test(host) ||
        /^(fc|fd)[0-9a-f]{2}:/.test(host)
    );
}

/** Viewer key modes accepted by `app.addons.useMirisStreaming.viewerKeyMode` (mirisStreamingConfig.ts: `MIRIS_VIEWER_KEY_MODES`). */
const MIRIS_VIEWER_KEY_MODES = ["static", "perView"];

/**
 * A complete commercial-partition Secrets Manager secret ARN, suffix included (mirisStreamingConfig.ts:
 * `MIRIS_SECRET_ARN_PATTERN`).
 */
const MIRIS_SECRET_ARN_PATTERN =
    /^arn:aws:secretsmanager:[a-z0-9-]+:\d{12}:secret:[A-Za-z0-9/_+=.@-]+-[A-Za-z0-9]{6}$/;

/**
 * File extensions the GLOBAL "Miris stream" file metadata schema accepts (mirisStreamingConfig.ts:
 * `MIRIS_STREAM_SCHEMA_EXTENSIONS`).
 */
export const MIRIS_STREAM_SCHEMA_EXTENSIONS = [".usd", ".usda", ".usdc", ".usdz"];

/** mirisStreamingConfig.ts `parseMirisTriggerExtensions`: the extensions, or undefined when malformed. */
function parseMirisTriggerExtensions(value: unknown): string[] | undefined {
    if (typeof value !== "string") return undefined;
    const extensions = value.split(",");
    return extensions.every((extension) => /^\.[a-z0-9]+$/.test(extension))
        ? extensions
        : undefined;
}

/** A string value with leading or trailing whitespace (mirisStreamingConfig.ts: the `!== ....trim()` checks). */
function hasSurroundingWhitespace(value: unknown): boolean {
    return typeof value === "string" && value !== value.trim();
}

/** A string value containing whitespace anywhere (mirisStreamingConfig.ts: the triggerExtensions `/\s/` check). */
function containsWhitespace(value: unknown): boolean {
    return typeof value === "string" && /\s/.test(value);
}

/** mirisStreamingConfig.ts `isPositiveInteger`. */
function isPositiveInteger(value: unknown): boolean {
    return typeof value === "number" && Number.isInteger(value) && value > 0;
}

/** The Miris add-on is enabled in static viewer key mode. An absent mode is filled with "perView". */
function mirisStatic(cfg: ConfigShape): boolean {
    return (
        !!g(cfg, "app.addons.useMirisStreaming.enabled") &&
        g(cfg, "app.addons.useMirisStreaming.viewerKeyMode") === "static"
    );
}

const miris = (c: ConfigShape) => !!g(c, "app.addons.useMirisStreaming.enabled");
const mirisPerView = (c: ConfigShape) => miris(c) && !mirisStatic(c);
const mirisUpload = (c: ConfigShape) =>
    miris(c) && !!g(c, "app.addons.useMirisStreaming.upload.enabled");

/**
 * Add-on paths with no `MIRIS_FIELDS` entry, which configBuilderSync.test.ts accepts as config
 * leaves the builder does not render: the block itself, whose fields each have their own entry.
 */
export const MIRIS_JSON_ONLY_PATHS: readonly string[] = ["app.addons.useMirisStreaming"];

/** The add-on block of the template JSONs; `index.ts` splices it in after the EDH block. */
export const MIRIS_DEFAULTS: ConfigShape = {
    useMirisStreaming: {
        enabled: false,
        viewerKeyMode: "perView",
        viewerKey: "",
        viewerKeyTtlMinutes: 15,
        apiKeySecretArn: "",
        mirisApiBaseUrl: "https://app.miris.com",
        upload: {
            enabled: false,
            autoRegisterWithVAMS: true,
            autoRegisterAutoTriggerOnFileUpload: false,
            triggerExtensions: ".usd,.usda,.usdc,.usdz",
            taskTimeoutSeconds: 1800,
            maxAssetSizeBytes: 5000000000,
        },
    },
};

export const MIRIS_FIELDS: FieldMeta[] = [
    {
        path: "app.addons.useMirisStreaming.enabled",
        label: "Miris Spatial Streaming",
        input: "boolean",
        section: "addons",
        advanced: true,
        help: "Streams 3D assets uploaded to Miris in the VAMS viewer. Requires allowUnsafeEvalFeatures. Commercial partition only.",
    },
    {
        path: "app.addons.useMirisStreaming.viewerKeyMode",
        label: "Miris viewer key mode",
        input: "select",
        section: "addons",
        advanced: true,
        options: [
            { value: "perView", label: "Per view (a short-lived key minted per viewer open)" },
            { value: "static", label: "Static (one deployment-wide viewer key)" },
        ],
        help: "perView (the default) mints a short-lived viewer key scoped to the one Miris asset each time an authorized user opens the viewer, and stores no viewer key. static returns the configured viewer key to an authorized viewer; VAMS stores that key in a Secrets Manager secret it fills at deploy time.",
        visibleWhen: miris,
    },
    {
        path: "app.addons.useMirisStreaming.viewerKey",
        label: "Miris viewer key",
        input: "text",
        section: "addons",
        advanced: true,
        help: "Required in static mode; ignored in perView mode. A viewer key authorizes streaming only and is meant to reach the browser, so scope it with tags and an expiry when you create it. Miris shows it only once.",
        visibleWhen: mirisStatic,
    },
    {
        path: "app.addons.useMirisStreaming.viewerKeyTtlMinutes",
        label: "Miris per-view key lifetime (minutes)",
        input: "number",
        section: "addons",
        advanced: true,
        min: 1,
        help: "Whole number from 1 to 60. How long a minted viewer key can start new streams; a stream already open keeps playing after its key expires.",
        visibleWhen: mirisPerView,
    },
    {
        path: "app.addons.useMirisStreaming.apiKeySecretArn",
        label: "Miris Integration Key secret ARN",
        input: "text",
        section: "addons",
        advanced: true,
        placeholder:
            "arn:aws:secretsmanager:us-east-1:123456789012:secret:miris-integration-key-AbCdEf",
        help: "Required in both modes. Complete Secrets Manager ARN, including the six-character suffix, of the secret holding the Miris Integration Key. It checks that a Miris asset is bound to the VAMS asset before a viewer key is returned, mints per-view keys, and is read by the Miris upload pipeline.",
        visibleWhen: miris,
    },
    {
        path: "app.addons.useMirisStreaming.mirisApiBaseUrl",
        label: "Miris API base URL",
        input: "text",
        section: "addons",
        advanced: true,
        placeholder: "https://app.miris.com",
        help: "Must be https and an external host.",
        visibleWhen: miris,
    },
    {
        path: "app.addons.useMirisStreaming.upload.enabled",
        label: "Miris upload",
        input: "boolean",
        section: "addons",
        advanced: true,
        help: "Deploys the add-on's upload pipeline, which uploads USD source files to Miris and records the Miris asset UUID in the file's metadata so the viewer can stream them, and offers the viewer's Stream with Miris button. Requires a VPC. Commercial partition only.",
        visibleWhen: miris,
    },
    {
        path: "app.addons.useMirisStreaming.upload.autoRegisterWithVAMS",
        label: "Miris upload auto-register",
        input: "boolean",
        section: "addons",
        advanced: true,
        visibleWhen: mirisUpload,
    },
    {
        path: "app.addons.useMirisStreaming.upload.autoRegisterAutoTriggerOnFileUpload",
        label: "Miris upload auto-trigger on upload",
        input: "boolean",
        section: "addons",
        advanced: true,
        help: "Runs the pipeline whenever a file with a trigger extension is uploaded to any database, sending it to Miris. Off (default): run it from the file manager only.",
        visibleWhen: mirisUpload,
    },
    {
        path: "app.addons.useMirisStreaming.upload.triggerExtensions",
        label: "Miris upload trigger file extensions",
        input: "text",
        section: "addons",
        advanced: true,
        placeholder: ".usd,.usda,.usdc,.usdz",
        help: "Comma-separated lowercase extensions. Each must be one the Miris stream file metadata schema accepts: .usd, .usda, .usdc or .usdz.",
        visibleWhen: mirisUpload,
    },
    {
        path: "app.addons.useMirisStreaming.upload.taskTimeoutSeconds",
        label: "Miris upload task timeout (seconds)",
        input: "number",
        section: "addons",
        advanced: true,
        min: 1,
        help: "How long the upload task may run. A positive whole number.",
        visibleWhen: mirisUpload,
    },
    {
        path: "app.addons.useMirisStreaming.upload.maxAssetSizeBytes",
        label: "Miris upload max source size (bytes)",
        input: "number",
        section: "addons",
        advanced: true,
        min: 1,
        help: "Largest upload to Miris: the root USD file and the files it references. Other files in the asset do not count. A positive whole number.",
        visibleWhen: mirisUpload,
    },
];

/** The add-on's entry in the enabled features that require a VPC (mirisStreamingConfig.ts: `mirisVpcRequiringFeatures`). */
export const MIRIS_VPC_REQUIRING_FEATURES: VpcRequiringFeature[] = [
    {
        id: "vpc-required-miris-upload",
        label: "addons.useMirisStreaming.upload",
        fieldPaths: ["app.addons.useMirisStreaming.upload.enabled"],
        appliesWhen: (c) => !!g(c, "app.addons.useMirisStreaming.upload.enabled"),
    },
];

export const MIRIS_RULES: Rule[] = [
    // ----- Miris upload pipeline (mirisStreamingConfig.ts: "Miris upload pipeline validation") -----
    {
        id: "miris-upload-requires-addon",
        severity: "error",
        fieldPaths: [
            "app.addons.useMirisStreaming.upload.enabled",
            "app.addons.useMirisStreaming.enabled",
        ],
        appliesWhen: (c) =>
            !!g(c, "app.addons.useMirisStreaming.upload.enabled") &&
            !g(c, "app.addons.useMirisStreaming.enabled"),
        message:
            "addons.useMirisStreaming.upload requires the Miris Spatial Streaming add-on (app.addons.useMirisStreaming.enabled). The pipeline reads the Miris Integration Key and API base URL from the add-on.",
    },
    // An absent upload field is filled with its default before these checks run.
    ...(
        [
            ["taskTimeoutSeconds", "miris-upload-task-timeout"],
            ["maxAssetSizeBytes", "miris-upload-max-asset-size"],
        ] as const
    ).map(
        ([field, id]): Rule => ({
            id,
            severity: "error",
            fieldPaths: [`app.addons.useMirisStreaming.upload.${field}`],
            appliesWhen: (c) => {
                const value = g(c, `app.addons.useMirisStreaming.upload.${field}`);
                return (
                    !!g(c, "app.addons.useMirisStreaming.upload.enabled") &&
                    !isAbsent(value) &&
                    !isPositiveInteger(value)
                );
            },
            message: `addons.useMirisStreaming.upload.${field} must be a positive whole number.`,
        })
    ),
    {
        id: "miris-upload-trigger-extensions-whitespace",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.upload.triggerExtensions"],
        appliesWhen: (c) =>
            !!g(c, "app.addons.useMirisStreaming.upload.enabled") &&
            containsWhitespace(g(c, "app.addons.useMirisStreaming.upload.triggerExtensions")),
        message:
            'addons.useMirisStreaming.upload.triggerExtensions must not contain whitespace. List the extensions separated by commas only, such as ".usd,.usdz".',
    },
    {
        id: "miris-upload-trigger-extensions-format",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.upload.triggerExtensions"],
        appliesWhen: (c) => {
            const value = g(c, "app.addons.useMirisStreaming.upload.triggerExtensions");
            return (
                !!g(c, "app.addons.useMirisStreaming.upload.enabled") &&
                !isAbsent(value) &&
                !containsWhitespace(value) &&
                parseMirisTriggerExtensions(value) === undefined
            );
        },
        message:
            'addons.useMirisStreaming.upload.triggerExtensions must be a comma-separated list of lowercase file extensions, such as ".usd,.usdz".',
    },
    {
        id: "miris-upload-trigger-extensions-schema",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.upload.triggerExtensions"],
        appliesWhen: (c) => {
            if (!g(c, "app.addons.useMirisStreaming.upload.enabled")) return false;
            const extensions = parseMirisTriggerExtensions(
                g(c, "app.addons.useMirisStreaming.upload.triggerExtensions")
            );
            return (
                extensions !== undefined &&
                extensions.some((extension) => !MIRIS_STREAM_SCHEMA_EXTENSIONS.includes(extension))
            );
        },
        message:
            "addons.useMirisStreaming.upload.triggerExtensions may only list extensions the Miris stream file metadata schema accepts (" +
            MIRIS_STREAM_SCHEMA_EXTENSIONS.join(", ") +
            ").",
    },

    // ----- Miris Spatial Streaming (mirisStreamingConfig.ts: "Miris Spatial Streaming") -----
    {
        id: "miris-commercial-partition-only",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.enabled", "env.region"],
        appliesWhen: (c) =>
            !!g(c, "app.addons.useMirisStreaming.enabled") && !isCommercialPartition(c),
        message:
            "Miris Spatial Streaming is supported in the commercial partition only. Miris is reached at *.miris.com over the public internet.",
    },
    {
        id: "miris-unsafe-eval",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.enabled", "app.webUi.allowUnsafeEvalFeatures"],
        appliesWhen: (c) =>
            !!g(c, "app.addons.useMirisStreaming.enabled") &&
            !g(c, "app.webUi.allowUnsafeEvalFeatures"),
        message:
            "Miris Spatial Streaming requires app.webUi.allowUnsafeEvalFeatures = true. The Miris web SDK compiles code at runtime.",
    },
    {
        id: "miris-use-for-all-lambdas",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.enabled", "app.useGlobalVpc.useForAllLambdas"],
        appliesWhen: (c) =>
            !!g(c, "app.addons.useMirisStreaming.enabled") &&
            !!g(c, "app.useGlobalVpc.enabled") &&
            !!g(c, "app.useGlobalVpc.useForAllLambdas"),
        message:
            "Miris Spatial Streaming cannot be combined with app.useGlobalVpc.useForAllLambdas: that setting places the add-on's Lambda functions in the VPC's isolated subnets, which have no route to the Miris API.",
    },
    {
        id: "miris-viewerkeymode",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.viewerKeyMode"],
        appliesWhen: (c) => {
            if (!g(c, "app.addons.useMirisStreaming.enabled")) return false;
            const mode = g(c, "app.addons.useMirisStreaming.viewerKeyMode");
            return !isAbsent(mode) && !MIRIS_VIEWER_KEY_MODES.includes(mode);
        },
        message: 'addons.useMirisStreaming.viewerKeyMode must be "static" or "perView".',
    },
    {
        id: "miris-viewerkey-ttl",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.viewerKeyTtlMinutes"],
        appliesWhen: (c) => {
            if (!g(c, "app.addons.useMirisStreaming.enabled")) return false;
            const ttl = g(c, "app.addons.useMirisStreaming.viewerKeyTtlMinutes");
            if (isAbsent(ttl)) return false; // backfilled to 15
            return typeof ttl !== "number" || !Number.isInteger(ttl) || ttl < 1 || ttl > 60;
        },
        message:
            "addons.useMirisStreaming.viewerKeyTtlMinutes must be a whole number of minutes from 1 to 60.",
    },
    // An absent mirisApiBaseUrl is filled with https://app.miris.com; "" and "UNDEFINED" are not.
    {
        id: "miris-baseurl-required",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.mirisApiBaseUrl"],
        appliesWhen: (c) => {
            const url = g(c, "app.addons.useMirisStreaming.mirisApiBaseUrl");
            return !!g(c, "app.addons.useMirisStreaming.enabled") && !isAbsent(url) && isUnset(url);
        },
        message: "Miris Spatial Streaming requires mirisApiBaseUrl when enabled.",
    },
    {
        id: "miris-baseurl-whitespace",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.mirisApiBaseUrl"],
        appliesWhen: (c) => {
            const url = g(c, "app.addons.useMirisStreaming.mirisApiBaseUrl");
            return (
                !!g(c, "app.addons.useMirisStreaming.enabled") &&
                !isUnset(url) &&
                hasSurroundingWhitespace(url)
            );
        },
        message:
            "addons.useMirisStreaming.mirisApiBaseUrl has leading or trailing whitespace. Remove it.",
    },
    {
        id: "miris-baseurl-url",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.mirisApiBaseUrl"],
        appliesWhen: (c) => {
            const url = g(c, "app.addons.useMirisStreaming.mirisApiBaseUrl");
            return (
                !!g(c, "app.addons.useMirisStreaming.enabled") &&
                !isUnset(url) &&
                !hasSurroundingWhitespace(url) &&
                !isValidUrl(url)
            );
        },
        message: "addons.useMirisStreaming.mirisApiBaseUrl must be a valid URL.",
    },
    {
        id: "miris-baseurl-https",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.mirisApiBaseUrl"],
        appliesWhen: (c) => {
            const url = g(c, "app.addons.useMirisStreaming.mirisApiBaseUrl");
            if (!g(c, "app.addons.useMirisStreaming.enabled") || !isValidUrl(url)) return false;
            if (hasSurroundingWhitespace(url)) return false; // covered by miris-baseurl-whitespace
            return new URL(url).protocol !== "https:";
        },
        message:
            "addons.useMirisStreaming.mirisApiBaseUrl must use https. VAMS sends the Miris Integration Key to it, which plain http transmits in cleartext.",
    },
    {
        id: "miris-baseurl-private-host",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.mirisApiBaseUrl"],
        appliesWhen: (c) => {
            const url = g(c, "app.addons.useMirisStreaming.mirisApiBaseUrl");
            if (!g(c, "app.addons.useMirisStreaming.enabled") || isUnset(url)) return false;
            if (hasSurroundingWhitespace(url)) return false; // covered by miris-baseurl-whitespace
            return isPrivateEndpointHost(String(url));
        },
        message:
            "addons.useMirisStreaming.mirisApiBaseUrl points at a loopback, link-local, or private address. VAMS Lambdas send the Miris Integration Key to it, so it must name an external service.",
    },
    {
        id: "miris-secret-required",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.apiKeySecretArn"],
        appliesWhen: (c) =>
            !!g(c, "app.addons.useMirisStreaming.enabled") &&
            isUnset(g(c, "app.addons.useMirisStreaming.apiKeySecretArn")),
        message:
            "Miris Spatial Streaming requires apiKeySecretArn, the Secrets Manager ARN of the Miris Integration Key, in both viewer key modes.",
    },
    {
        id: "miris-secret-arn-format",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.apiKeySecretArn"],
        appliesWhen: (c) => {
            if (!g(c, "app.addons.useMirisStreaming.enabled")) return false;
            const arn = g(c, "app.addons.useMirisStreaming.apiKeySecretArn");
            return !isUnset(arn) && !MIRIS_SECRET_ARN_PATTERN.test(String(arn));
        },
        message:
            "addons.useMirisStreaming.apiKeySecretArn must be the complete ARN of a Secrets Manager secret, including the six-character suffix Secrets Manager appends to the secret name, in the commercial partition (arn:aws:secretsmanager:<region>:<account>:secret:<name>-AbCdEf).",
    },
    {
        id: "miris-viewerkey-required",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.viewerKey"],
        appliesWhen: (c) =>
            mirisStatic(c) && isBlank(g(c, "app.addons.useMirisStreaming.viewerKey")),
        message:
            'addons.useMirisStreaming.viewerKeyMode "static" requires viewerKey. Generate one in the Miris Portal or with `miris viewerkey create`.',
    },
    {
        id: "miris-viewerkey-whitespace",
        severity: "error",
        fieldPaths: ["app.addons.useMirisStreaming.viewerKey"],
        appliesWhen: (c) => {
            if (!mirisStatic(c)) return false;
            const key = g(c, "app.addons.useMirisStreaming.viewerKey");
            return !isBlank(key) && hasSurroundingWhitespace(key);
        },
        message:
            "addons.useMirisStreaming.viewerKey has leading or trailing whitespace. Remove it; a Miris viewer key contains none.",
    },
    {
        id: "miris-perview-viewerkey-ignored-warn",
        severity: "warning",
        fieldPaths: ["app.addons.useMirisStreaming.viewerKey"],
        // mirisStreamingConfig.ts: "viewerKey is ignored when"
        appliesWhen: (c) =>
            !!g(c, "app.addons.useMirisStreaming.enabled") &&
            (isAbsent(g(c, "app.addons.useMirisStreaming.viewerKeyMode")) ||
                g(c, "app.addons.useMirisStreaming.viewerKeyMode") === "perView") &&
            !isUnset(g(c, "app.addons.useMirisStreaming.viewerKey")),
        message:
            'addons.useMirisStreaming.viewerKey is ignored when viewerKeyMode is "perView". Remove it from config.json.',
    },
    // ----- Imported VPC (mirisStreamingConfig.ts: "an imported VPC must name private subnets") -----
    {
        id: "miris-upload-external-vpc-private-subnets",
        severity: "error",
        fieldPaths: [
            "app.useGlobalVpc.optionalExternalPrivateSubnetIds",
            "app.addons.useMirisStreaming.upload.enabled",
        ],
        appliesWhen: (c) =>
            !!g(c, "app.addons.useMirisStreaming.upload.enabled") &&
            !!g(c, "app.useGlobalVpc.enabled") &&
            !isUnset(g(c, "app.useGlobalVpc.optionalExternalVpcId")) &&
            isUnset(g(c, "app.useGlobalVpc.optionalExternalPrivateSubnetIds")),
        message:
            "Must define at least one private subnet ID when using an external VPC with the Miris upload pipeline, whose compute runs in private subnets.",
    },
    // ----- Armed trigger (mirisStreamingConfig.ts: "an armed trigger on an unregistered pipeline is discarded") -----
    {
        id: "miris-upload-armed-trigger-unregistered",
        severity: "warning",
        fieldPaths: [
            "app.addons.useMirisStreaming.upload.autoRegisterWithVAMS",
            "app.addons.useMirisStreaming.upload.autoRegisterAutoTriggerOnFileUpload",
        ],
        appliesWhen: (c) =>
            !!g(c, "app.addons.useMirisStreaming.upload.enabled") &&
            !!g(c, "app.addons.useMirisStreaming.upload.autoRegisterAutoTriggerOnFileUpload") &&
            g(c, "app.addons.useMirisStreaming.upload.autoRegisterWithVAMS") !== true,
        message:
            "addons.useMirisStreaming.upload.autoRegisterAutoTriggerOnFileUpload is true but autoRegisterWithVAMS is not, so no registration and no upload trigger are created. Set autoRegisterWithVAMS to true to arm the trigger.",
    },
];
