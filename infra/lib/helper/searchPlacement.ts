/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { ConfigPublic } from "../../config/config";

/**
 * Whether the search-domain Lambda functions (search, indexers, reindexer, vector search) run inside
 * the VPC. A provisioned OpenSearch domain and a private Serverless collection are reachable only from
 * the VPC, and `useForAllLambdas` places every Lambda there; everything else stays outside so the
 * functions reach their services over public endpoints without a NAT gateway.
 */
export function searchLambdasInVpc(config: ConfigPublic): boolean {
    return (
        config.app.openSearch.useProvisioned.enabled ||
        (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas) ||
        (config.app.openSearch.useServerless.enabled &&
            !config.app.openSearch.useServerless.allowPublic)
    );
}
