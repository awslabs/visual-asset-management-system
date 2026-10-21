/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "../../../services/apiClient";
import { toTuple } from "../../../features/orchestration/api/client";
import type {
    CatalogLeasePolicy,
    EdhCatalogImage,
    EdhInstanceType,
    EdhListResponse,
} from "../types";

const CATALOG_IMAGES_PATH = "addon/edh/catalog/images";
const CATALOG_INSTANCE_TYPES_PATH = "addon/edh/catalog/instance-types";
const CATALOG_LEASE_PATH = "addon/edh/catalog/lease";

export async function listEdhImages(): Promise<[boolean, EdhCatalogImage[] | string]> {
    return toTuple(async () => {
        const resp: EdhListResponse<EdhCatalogImage> = await apiClient.get(CATALOG_IMAGES_PATH);
        return resp?.Items ?? [];
    });
}

export async function listEdhInstanceTypes(): Promise<[boolean, EdhInstanceType[] | string]> {
    return toTuple(async () => {
        const resp: EdhListResponse<EdhInstanceType> = await apiClient.get(
            CATALOG_INSTANCE_TYPES_PATH
        );
        return resp?.Items ?? [];
    });
}

/** The lease terms a launch is subject to; the route carries them under `lease`. */
export async function getEdhLeasePolicy(): Promise<
    [boolean, { lease: CatalogLeasePolicy } | string]
> {
    return toTuple(() => apiClient.get(CATALOG_LEASE_PATH));
}
