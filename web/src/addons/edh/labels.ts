/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type {
    CatalogLeasePolicy,
    EdhOsFamily,
    WorkstationActivity,
    WorkstationRecord,
} from "./types";

/** The words shown for the OS family tokens the service stores. */
export const OS_FAMILY_LABEL: Record<EdhOsFamily, string> = {
    linux: "Linux",
    windows: "Windows",
};

/** The OS family as shown to users; a token outside the map is shown as it is. */
export const osFamilyLabel = (value: string | null | undefined): string =>
    (value && OS_FAMILY_LABEL[value as EdhOsFamily]) || value || "—";

/** The image fields of a workstation record, as the image name and the newer-image badge read them. */
export type WorkstationImageFields = Pick<
    WorkstationRecord,
    | "state"
    | "imageId"
    | "imageName"
    | "recipeId"
    | "recipeDisplayName"
    | "recipeVersion"
    | "latestRecipeVersion"
    | "imageLatest"
>;

const isVersion = (value: number | null | undefined): value is number =>
    typeof value === "number" && Number.isFinite(value);

/**
 * The name shown for the image a workstation runs: the recipe's display name and the build version
 * (`Isaac Sim 6.2 v13`) when the record carries both, else the record's image name, else the name
 * the live catalog lists for the image id, else the id. `vamscli edh workstation` prints the same
 * name without the catalog step (workstationImageCases.json).
 */
export const workstationImageName = (
    record: WorkstationImageFields,
    catalogName?: string | null
): string => {
    if (record.recipeDisplayName && isVersion(record.recipeVersion)) {
        return `${record.recipeDisplayName} v${record.recipeVersion}`;
    }
    return record.imageName || catalogName || record.imageId;
};

// The states of a workstation that still exists and is not a failed launch.
const NEWER_IMAGE_STATES: ReadonlySet<string> = new Set(["ACTIVE", "STOPPED", "CREATING"]);

/**
 * The newer-image badge of a workstation whose recipe has a newer AVAILABLE build than the one it
 * runs: its words and the sentence its tooltip and screen-reader description carry. Null unless
 * `imageLatest` is false on an ACTIVE, STOPPED or CREATING workstation.
 */
export const newerImageNotice = (
    record: WorkstationImageFields
): { label: string; description: string } | null => {
    if (record.imageLatest !== false || !NEWER_IMAGE_STATES.has(record.state)) return null;
    const { recipeVersion, latestRecipeVersion } = record;
    if (!isVersion(recipeVersion) || !isVersion(latestRecipeVersion)) return null;
    const recipe = record.recipeDisplayName || record.recipeId || "its recipe";
    return {
        label: `Newer image: v${latestRecipeVersion}`,
        description:
            `This workstation runs v${recipeVersion} of ${recipe}. v${latestRecipeVersion} is the ` +
            "latest build. A workstation keeps the image it was launched from; launch a new " +
            "workstation to use the latest build.",
    };
};

/** An EDH session state (`running`, `pending`, …) as shown to users: EDH's own word, capitalised. */
export const edhStateLabel = (value: string | null | undefined): string =>
    value ? value.charAt(0).toUpperCase() + value.slice(1) : "—";

/**
 * Whether a row is a start EDH accepted and has not finished: the session sweep found the desktop
 * still `pending` 30 minutes after the start was accepted and recorded `lastError` `START_STALLED` on
 * the row, which stays CREATING because EDH may still finish. The service reports no `activity` for
 * it; one that does is moving again and not stalled. Shared by the badge, the row actions and the
 * poll cadence, and mirrored by `vamscli edh workstation` (workstationStatusCases.json).
 */
export const isStartStalled = (row: {
    state: string | null;
    activity?: WorkstationActivity | null;
    lastError?: string | null;
}): boolean => row.state === "CREATING" && row.lastError === "START_STALLED" && !row.activity;

const MINUTE_MS = 60 * 1000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

/** A span in the two largest units it covers: `2 d 4 h`, `3 h 20 min`, `40 min`. */
const spanWords = (ms: number): string => {
    if (ms >= DAY_MS) {
        const days = Math.floor(ms / DAY_MS);
        const hours = Math.floor((ms - days * DAY_MS) / HOUR_MS);
        return hours > 0 ? `${days} d ${hours} h` : `${days} d`;
    }
    if (ms >= HOUR_MS) {
        const hours = Math.floor(ms / HOUR_MS);
        const minutes = Math.floor((ms - hours * HOUR_MS) / MINUTE_MS);
        return minutes > 0 ? `${hours} h ${minutes} min` : `${hours} h`;
    }
    // A span under a minute still reads as a minute rather than as nothing.
    return `${Math.max(1, Math.floor(ms / MINUTE_MS))} min`;
};

/** A number of hours with its unit: `1 hour`, `8 hours`. */
export const hoursWord = (n: number): string => `${n} ${n === 1 ? "hour" : "hours"}`;

const instant = (iso: string | null | undefined): number | null => {
    if (!iso) return null;
    const ms = new Date(iso).getTime();
    return isNaN(ms) ? null : ms;
};

/** How the time a lease has left reads: an ordinary countdown, the last hour, or past the expiry. */
export type LeaseRemainingTone = "normal" | "warning" | "expired";

/**
 * What is left of one workstation's lease at `now`, or null for a row no lease covers. Past the
 * expiry the value tracks the removal instead: the desktop is stopped, then removed once the grace
 * window measured from the stop has run out.
 */
export function leaseRemaining(
    record: Pick<WorkstationRecord, "leaseExpiresAt" | "leaseStoppedAt">,
    policy: Pick<CatalogLeasePolicy, "graceHours">,
    now: number
): { text: string; tone: LeaseRemainingTone } | null {
    const expiresAt = instant(record.leaseExpiresAt);
    if (expiresAt === null) return null;

    const left = expiresAt - now;
    if (left > 0) {
        return { text: `in ${spanWords(left)}`, tone: left > HOUR_MS ? "normal" : "warning" };
    }
    if (!record.leaseStoppedAt) return { text: "Expired, stopping", tone: "expired" };

    const stoppedAt = instant(record.leaseStoppedAt);
    const removeAt = Math.max(expiresAt, stoppedAt ?? expiresAt) + policy.graceHours * HOUR_MS;
    if (policy.graceHours <= 0 || removeAt <= now) {
        return { text: "Expired, removing", tone: "expired" };
    }
    return { text: `Expired, removed in ${spanWords(removeAt - now)}`, tone: "expired" };
}
