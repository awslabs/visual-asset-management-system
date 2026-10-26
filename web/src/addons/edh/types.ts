/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export type WorkstationState = "CREATING" | "ACTIVE" | "STOPPED" | "DELETED" | "ORPHANED";
export type EdhOsFamily = "linux" | "windows";

/**
 * Why a launch ended ORPHANED, why the lease sweep stopped the desktop, why a start of a stopped
 * desktop left it STOPPED, or (`START_STALLED`, on a CREATING row) that EDH accepted a start and has
 * not finished it after 30 minutes. The service stores these codes and never EDH's own wording.
 */
export type WorkstationLastError =
    | "USER_NOT_PROVISIONED"
    | "QUOTA_EXCEEDED"
    | "IMAGE_UNAVAILABLE"
    | "CAPACITY_UNAVAILABLE"
    | "EDH_REFUSED"
    | "LAUNCH_ERROR"
    | "CREATE_TIMED_OUT"
    | "LEASE_EXPIRED"
    | "RESERVATION_FULL"
    | "RESERVATION_ENDED"
    | "START_FAILED"
    | "START_STALLED";

/**
 * What a workstation is doing, as the service derives it on every read, whatever `state` says:
 * `starting` from the start request until EDH reports the desktop connectable, `stopping` while EDH
 * still stops it. Null when neither.
 */
export type WorkstationActivity = "starting" | "stopping";

/**
 * Why a start of a stopped desktop left it STOPPED: the AWS error code EDH's refusal named (e.g.
 * `InsufficientInstanceCapacity`) and the Availability Zone the desktop's instance, and so its disk,
 * is in. Each is absent when it was not read. Kept until the next start is accepted. On a start the
 * sweep found stalled (`lastError` `START_STALLED`) it carries `observedAt`, and the zone when read,
 * but no `errorCode`: EDH reported none.
 */
export interface StartFailure {
    errorCode?: string | null;
    availabilityZone?: string | null;
    /** When VAMS recorded it (ISO-8601 UTC). */
    observedAt: string;
}

/** What became of the per-session VAMS credential a launch places on the desktop. */
export type SessionBootstrapOutcome = "provisioned" | "skipped" | "unavailable" | "disabled";

/** The EDH event a captured launch failure came from: a refused placement, or a failed desktop setup. */
export type EdhFailureEvent = "placement_failed" | "session-failed";

/**
 * The launch failure VAMS captured from EDH's own record of the desktop: the AWS error code and
 * operation parsed from EDH's status, or the setup checkpoint of a failed desktop. Never EDH's wording.
 */
export interface EdhFailure {
    event: EdhFailureEvent;
    /** The AWS error code, e.g. `InsufficientInstanceCapacity`; absent when EDH's status names none. */
    errorCode?: string | null;
    /** The AWS operation that returned the code, e.g. `CreateCapacityReservation`. */
    operation?: string | null;
    /** For `session-failed`, the EDH checkpoint the setup failed at. */
    checkpoint?: string | null;
    /** When VAMS captured it (ISO-8601 UTC). */
    observedAt: string;
}

/** One launch, as the workstation service returns it (also the session-table row). */
export interface WorkstationRecord {
    bootstrapId: string;
    sessionId: string | null;
    userId: string;
    edhUid: string;
    state: WorkstationState;
    /** The raw EDH session state string, once EDH has reported one. */
    edhState: string | null;
    /** What the workstation is doing, whatever `state` says; null or absent when neither. */
    activity?: WorkstationActivity | null;
    /** The failure code the row carries; null or absent when there is none. */
    lastError?: string | null;
    /** The launch failure VAMS captured from EDH; null or absent when it captured none. */
    edhFailure?: EdhFailure | null;
    /** Why the last start left the desktop STOPPED, or when it stalled; null or absent when neither. */
    startFailure?: StartFailure | null;
    /** How many times the launch worker has started on this row. */
    launchAttempts?: number;
    name: string;
    imageId: string;
    /** The EDH image name the workstation was launched from; null or absent when not known. */
    imageName?: string | null;
    /** The AMI recipe whose build produced the image; null or absent for a baseline image. */
    recipeId?: string | null;
    /** That recipe's display name. */
    recipeDisplayName?: string | null;
    /** The build version the workstation runs. */
    recipeVersion?: number | null;
    /** The recipe's newest AVAILABLE build version, derived on each read; null when none or unknown. */
    latestRecipeVersion?: number | null;
    /**
     * Whether the workstation runs its recipe's newest build: false only when a newer AVAILABLE build
     * exists; null for a baseline image, an unknown recipe or version, or a recipe with no AVAILABLE
     * build.
     */
    imageLatest?: boolean | null;
    osFamily: EdhOsFamily;
    instanceType: string;
    createdAt: string;
    updatedAt: string;
    connectionAvailable: boolean;
    /** When the lease runs out; null or absent on a row no lease covers. */
    leaseExpiresAt?: string | null;
    /** When the lease stopped the desktop; cleared by a renewal. */
    leaseStoppedAt?: string | null;
    /** The lease window the launch was stamped with, in hours. */
    leaseHoursAtLaunch?: number | null;
    /** The capacity reservation the launch was sent with; null or absent for an on-demand launch. */
    capacityReservationId?: string | null;
    /** The reservation's label, read from the registry; null once it is unregistered. */
    capacityReservationLabel?: string | null;
    /**
     * True when the workstation asked for a reservation and runs on-demand: the reservation was full
     * at launch, or it had ended when a start of the stopped workstation moved it to on-demand.
     */
    capacityReservationFallback?: boolean | null;
    /**
     * Why it runs on-demand: `full` (a launch into a full reservation that falls back) or `ended`
     * (VAMS moved the stopped workstation to on-demand because its reservation ended). Absent on a
     * fallback recorded before the field, which was a full reservation's.
     */
    capacityReservationFallbackReason?: CapacityReservationFallbackReason | string | null;
    /** The reservation a fallback row asked for, when the service returns it. */
    capacityReservationRequested?: string | null;
    /**
     * Present on the launch and start responses, and on a renew response when the renew re-issued,
     * or tried to re-issue, the desktop's VAMS sign-in. A start is accepted (202) with `activity`
     * `starting` and runs in the background; the list then shows its progress.
     */
    sessionBootstrap?: SessionBootstrapOutcome;
}

export interface CreateWorkstationRequest {
    /** Client-generated uuid4; the launch's idempotency key. */
    bootstrapId: string;
    imageId: string;
    instanceType: string;
    name: string;
    rootSizeGb: number;
    osFamily: EdhOsFamily;
    /** The lease window asked for, in hours; omitted for none. */
    leaseHours?: number;
    /** The registered capacity reservation to launch into; omitted for an on-demand launch. */
    capacityReservationId?: string;
}

/** Why a workstation that asked for a capacity reservation runs on-demand. */
export type CapacityReservationFallbackReason = "full" | "ended";

/** EC2's reservation state, `notFound` when EC2 no longer knows the id, `unknown` when the read failed. */
export type CapacityReservationState =
    | "active"
    | "expired"
    | "cancelled"
    | "pending"
    | "failed"
    | "scheduled"
    | "payment-pending"
    | "payment-failed"
    | "assessing"
    | "delayed"
    | "unsupported"
    | "notFound"
    | "unknown";

/**
 * A registered EC2 capacity reservation: the registry's fields, and the EC2 facts read live (null
 * when the read failed or EC2 does not know the reservation).
 */
export interface CapacityReservationView {
    capacityReservationId: string;
    label: string;
    /** Whether a launch into the reservation runs on-demand when it has no free slot. */
    fallbackToOnDemand: boolean;
    enabled: boolean;
    instanceType: string | null;
    availabilityZone: string | null;
    /** EC2's platform, e.g. `Linux/UNIX` or `Windows`. */
    instancePlatform: string | null;
    state: CapacityReservationState | string;
    totalInstanceCount: number | null;
    availableInstanceCount: number | null;
    instanceMatchCriteria: string | null;
    /** ISO-8601, or null for a reservation without an end date. */
    endDate: string | null;
    reservationType: "default" | "capacity-block" | string | null;
    /**
     * Whether EDH has a private subnet in the reservation's Availability Zone, which it needs to
     * launch into it: null when VAMS could not read EDH's subnets. The catalog lists no reservation
     * for which it is false.
     */
    edhSubnetAvailable?: boolean | null;
    /**
     * Catalog only: whether the user's role may launch into the reservation, not only see it.
     * Absent on the admin list, and from a service that predates the field.
     */
    launchAllowed?: boolean;
    createdAt?: string;
    createdBy?: string;
    updatedAt?: string;
    updatedBy?: string;
}

/** The registry fields an administrator sets; the id is the EC2 reservation id. */
export interface RegisterCapacityReservationRequest {
    capacityReservationId: string;
    label: string;
    fallbackToOnDemand?: boolean;
    enabled?: boolean;
}

export type UpdateCapacityReservationRequest = Partial<
    Pick<RegisterCapacityReservationRequest, "label" | "fallbackToOnDemand" | "enabled">
>;

export interface WorkstationConnection {
    sessionId: string;
    connectionUrl: string;
    osFamily: EdhOsFamily;
}

/** A running Windows workstation's sign-in; `password` is null for a directory account. */
export interface WindowsSignIn {
    sessionId: string;
    username: string;
    password: string | null;
    directoryAccount: boolean;
}

/** `amiRecipe` for an image an AMI recipe build registered, `baseline` for every other image. */
export type EdhCatalogImageCategory = "amiRecipe" | "baseline";

export interface EdhCatalogImage {
    imageId: string;
    name: string;
    baseOs: string;
    osFamily: EdhOsFamily;
    /** The image's CPU architecture as EDH registered it (`x86_64`, `arm64`); null when unknown. */
    architecture?: string | null;
    rootSize: number | null;
    description: string | null;
    category: EdhCatalogImageCategory;
    /** The recipe, its display name and the build version of an `amiRecipe` image. */
    recipeId?: string;
    recipeDisplayName?: string;
    recipeVersion?: number;
    /**
     * The instance types the image's recipe offers its desktops, in the recipe's order; present only on
     * an `amiRecipe` image whose recipe lists any. Absent means no restriction of the recipe's own.
     */
    launchInstanceTypes?: string[];
}

export interface EdhInstanceType {
    instanceType: string;
    /** The architecture groups of the deployment's desktop profile that list the type. */
    architectures?: string[];
}

/** List responses carry `Items`, plus `NextToken` only when the service paginates. */
export interface EdhListResponse<T> {
    Items: T[];
    NextToken?: string;
}

export type EdhContainerBackend = "batch" | "eks";
export type EdhContainerCatalogKind = "jobDefinition" | "jobQueue" | "eksCluster";

/** One curated catalog record, tagged with the listing it came from; an eksCluster item is `{kind, name}`. */
export interface EdhContainerCatalogItem {
    kind: EdhContainerCatalogKind;
    [field: string]: unknown;
}

export interface EdhContainerCatalog {
    Items: EdhContainerCatalogItem[];
    /** Whether the deployment accepts the `eks` backend (experimental). */
    eksEnabled: boolean;
}

/** Where an eks job lives; the list and kill routes take it as query parameters. */
export interface EdhContainerScope {
    cluster: string;
    namespace?: string;
}

/** One of the caller's jobs: the EDH record plus the id, name, status, definition and handle the service names. */
export interface EdhContainerJob {
    backend: EdhContainerBackend;
    /** The Batch job id, or the EKS job name; what the kill route takes as `{jobId}`. */
    jobId: string;
    jobName: string;
    status: string;
    edhJobDefinition: string;
    jobHandle: string;
    cluster?: string;
    namespace?: string;
    [field: string]: unknown;
}

export interface SubmitContainerJobRequest {
    /** A Batch job-definition name (or ARN), or the image URI for the `eks` backend. */
    jobDefinition: string;
    /** A Batch job queue, or the cluster name for the `eks` backend. Required by the service. */
    jobQueue: string;
    /** Batch: letters, digits, hyphens, underscores (1-128), starting alphanumeric; eks: a lower-case DNS label (max 63). */
    name: string;
    command?: string[];
    environment?: Record<string, string>;
    vcpus?: number;
    /** MiB; at least 512. */
    memory?: number;
    gpu?: number;
    backend: EdhContainerBackend;
}

export interface SubmitContainerJobResponse {
    jobId: string;
    jobHandle: string;
    backend: EdhContainerBackend;
    jobDefinition: string;
    jobQueue: string;
    name: string;
    message: string;
    cluster?: string;
    namespace?: string;
}

export interface KillContainerJobResponse {
    jobId: string;
    backend: EdhContainerBackend;
    message: string;
    cluster?: string;
    namespace?: string;
}

export type EdhAmiRecipeKind = "vdi" | "simulation";
export type EdhArchitecture = "x86_64" | "arm64";
export type EdhAmiRecipeSource = "config" | "admin";
export type EdhAmiBuildStatus =
    | "QUEUED"
    | "BUILDING"
    | "REGISTERING"
    | "AVAILABLE"
    | "REGISTER_FAILED"
    | "FAILED"
    | "RETIRED";
export type EdhAmiRecipeComponent = { arn: string } | { name: string; data: string };

/** A repository directory a build fetches; a component reaches it as `${ASSET_<id>}`. */
export interface EdhAmiRecipeAsset {
    id: string;
    repoPath: string;
}

export interface EdhAmiRecipeRequest {
    recipeId: string;
    displayName: string;
    kind: EdhAmiRecipeKind;
    osFamily: EdhOsFamily;
    baseOs?: string;
    architecture: EdhArchitecture;
    parentImage: string;
    /** One EC2 instance type, or a list Image Builder chooses among by availability. */
    buildInstanceType: string | string[];
    /**
     * The instance types a desktop of the recipe's image may launch on; absent, null or empty is no
     * restriction of the recipe's own. Outside the content hash, so it never provokes a build.
     */
    launchInstanceTypes?: string[] | null;
    rootSizeGb: number;
    stackName: string;
    profileId: number;
    components: EdhAmiRecipeComponent[];
    /** The values a component reaches as `${PARAM_<key>}`. */
    parameters?: Record<string, string>;
    keepVersions?: number;
    recipeRevision?: number;
    description?: string;
    enabled?: boolean;
    /** On create, the recipe to duplicate; the new recipe takes its bundled files. */
    duplicateOf?: string;
}

export interface EdhAmiBuild {
    recipeId: string;
    version: number;
    status: EdhAmiBuildStatus;
    contentHash?: string;
    requestedBy?: string;
    requestedAt?: string;
    startedAt?: string;
    completedAt?: string;
    imageBuildVersionArn?: string;
    resolvedParentAmi?: string;
    amiId?: string;
    snapshotIds?: string[];
    edhStackId?: string;
    edhStackName?: string;
    edhKind?: string;
    registeredAt?: string;
    retiredAt?: string;
    amiDeregisteredAt?: string;
    amiInUseBy?: string[];
    amiRemovalPending?: boolean;
    error?: string;
    retireWarning?: string;
    pruneWarning?: string;
    outcome?: string;
}

export interface EdhAmiRecipe extends EdhAmiRecipeRequest {
    source: EdhAmiRecipeSource;
    enabled: boolean;
    contentHash: string;
    /** The bundled files; a shipped recipe declares them and a duplicate keeps its source's. */
    assets?: EdhAmiRecipeAsset[];
    /** The recipe this one was duplicated from. */
    duplicatedFrom?: string;
    latestVersion?: number;
    buildOnDeploy?: boolean;
    latestBuild: EdhAmiBuild | null;
    createdAt: string;
    updatedAt: string;
    createdBy?: string;
    updatedBy?: string;
}

export const ACTIVE_BUILD_STATUSES: ReadonlyArray<EdhAmiBuildStatus> = [
    "QUEUED",
    "BUILDING",
    "REGISTERING",
];

export interface LeasePolicy {
    enabled: boolean;
    leaseHours: number;
    forced: boolean;
    graceHours: number;
    updatedAt?: string;
    updatedBy?: string;
}

/** The lease policy as a launching user reads it: the terms, without the audit fields. */
export type CatalogLeasePolicy = Pick<
    LeasePolicy,
    "enabled" | "leaseHours" | "forced" | "graceHours"
>;
