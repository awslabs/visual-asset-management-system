/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export const featuresEnabled = {
    LOCATIONSERVICES: "LOCATIONSERVICES",
    NOOPENSEARCH: "NOOPENSEARCH",
    ALLOWUNSAFEEVAL: "ALLOWUNSAFEEVAL",
    CLOUDFRONTDEPLOY: "CLOUDFRONTDEPLOY",
};

/**
 * Coerce a feature-switch value into a string array so consumers can safely call
 * `.includes(...)`. The secure-config API is expected to return an array, but a
 * malformed or legacy response (boolean, undefined, comma-separated string) must
 * not crash the pages that read it. Returns an empty array for any non-array,
 * except a non-empty string which is split on commas.
 */
export function normalizeFeaturesEnabled(value: any): string[] {
    if (Array.isArray(value)) {
        return value.filter((v): v is string => typeof v === "string");
    }
    if (typeof value === "string" && value.trim() !== "") {
        return value
            .split(",")
            .map((v) => v.trim())
            .filter((v) => v !== "");
    }
    return [];
}
