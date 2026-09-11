/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Internal API Gateway endpoint constants/helpers (not customer-facing config).
 *
 * The backend serves every route under a fixed REST API stage. This mirrors the internal
 * CDK constant `API_GATEWAY_STAGE_NAME` in `infra/config/config.ts` and rarely changes.
 * Consumed by the auth bootstrap (`FedAuth/Auth.tsx`) and the amplify-config fetch
 * (`services/APIService.ts`); kept out of `config.ts`, which is reserved for customer config.
 */
export const API_GATEWAY_STAGE = "api";

/**
 * Ensure an API base URL ends with the API Gateway stage segment ("/api/").
 *
 * Accepts a base URL (with or without a trailing slash, with or without the stage) and
 * returns it normalized to end with exactly one "/api/". If the path already ends with the
 * stage segment it is returned unchanged (aside from ensuring a single trailing slash), so a
 * base that already includes the stage (the amplify-config value, or a URL the user already
 * appended "/api" to) is never double-stamped.
 */
export function ensureApiStage(baseUrl: string): string {
    if (!baseUrl) {
        return baseUrl;
    }
    // Split off any query/hash so they don't get stranded before the appended stage.
    const hashIndex = baseUrl.search(/[?#]/);
    const suffix = hashIndex === -1 ? "" : baseUrl.slice(hashIndex);
    let base = hashIndex === -1 ? baseUrl : baseUrl.slice(0, hashIndex);

    // Work on the path only (preserve scheme/host); tolerate a non-URL relative base too.
    const withoutTrailingSlash = base.replace(/\/+$/, "");
    const endsWithStage = new RegExp(`(^|/)${API_GATEWAY_STAGE}$`).test(withoutTrailingSlash);
    base = endsWithStage
        ? `${withoutTrailingSlash}/`
        : `${withoutTrailingSlash}/${API_GATEWAY_STAGE}/`;
    return base + suffix;
}
