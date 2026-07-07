/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Shared types for the VAMS config.json builder.
 *
 * The config shape mirrors the `ConfigPublic` interface in
 * `infra/config/config.ts`. We keep it as a permissive nested object here
 * rather than re-declaring the full TypeScript interface, because the builder
 * reads/writes fields by dotted path. Strong per-field types live in
 * `infra/config/config.ts` (the deploy-time source of truth).
 */

export type Profile = "commercial" | "govcloud" | "eusovereign";

/** A VAMS config object — structurally identical to ConfigPublic. */
export type ConfigShape = Record<string, any>;

/** The kind of input control rendered for a field. */
export type InputKind =
    | "boolean"
    | "text"
    | "number"
    | "select"
    | "string-array" // e.g. NVIDIA instanceTypes
    | "ip-range-list" // allowedIpRanges: string[][]
    | "presigned-url-restrictions" // assetBuckets.presignedUrlNetworkRestrictions object
    | "external-buckets"; // assetBuckets.externalAssetBuckets tuple | null

export interface SelectOption {
    value: string;
    label: string;
}

/** Declarative metadata describing one editable config field. */
export interface FieldMeta {
    /** Dotted path into the config object, e.g. "app.useAlb.domainHost". */
    path: string;
    label: string;
    input: InputKind;
    section: SectionId;
    /** When true the field lives in the section's collapsed "advanced" area. */
    advanced?: boolean;
    /** Short inline help shown under the control. */
    help?: string;
    /** Options for `select` inputs. */
    options?: SelectOption[];
    placeholder?: string;
    /** Optional numeric lower bound (UI hint only — validation is authoritative). */
    min?: number;
    /** When present and returns false, the field is hidden from the form. */
    visibleWhen?: (cfg: ConfigShape) => boolean;
}

export type SectionId =
    | "essentials"
    | "security"
    | "networking"
    | "frontend"
    | "search"
    | "auth"
    | "pipelines-standard"
    | "pipelines-gpu"
    | "addons"
    | "api-webui"
    | "metadata";

export interface Section {
    id: SectionId;
    label: string;
    description?: string;
    /** When true the whole section is collapsed by default (advanced area). */
    advanced?: boolean;
    order: number;
}

export type Severity = "error" | "warning";

/**
 * A validation rule ported from `getConfig()` in infra/config/config.ts.
 * `appliesWhen` returns true when the rule is *violated* (or, for warnings,
 * when the advisory condition is active).
 */
export interface Rule {
    /** Stable id; also carries a config.ts line reference in a code comment. */
    id: string;
    severity: Severity;
    /** Field paths this rule concerns — used to render inline markers. */
    fieldPaths: string[];
    appliesWhen: (cfg: ConfigShape) => boolean;
    message: string;
}

export interface RuleResult {
    rule: Rule;
}

/** A single field forced by the auto-toggle engine, surfaced to the user. */
export interface DerivedChange {
    path: string;
    to: unknown;
    reason: string;
}
