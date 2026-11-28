/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "../../../services/apiClient";
import { toTuple } from "../../../features/orchestration/api/client";
import type {
    CapacityReservationView,
    CatalogLeasePolicy,
    EdhCatalogImage,
    EdhInstanceType,
    EdhListResponse,
} from "../types";

const CATALOG_IMAGES_PATH = "addon/edh/catalog/images";
const CATALOG_INSTANCE_TYPES_PATH = "addon/edh/catalog/instance-types";
const CATALOG_LEASE_PATH = "addon/edh/catalog/lease";
const CATALOG_CAPACITY_RESERVATIONS_PATH = "addon/edh/catalog/capacity-reservations";

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

/**
 * The capacity reservations the caller may launch into: registered, enabled, permitted and active.
 * The wizard filters the one list by the chosen instance type and image, so no filter is sent; a
 * failed EC2 read answers an empty list and the launch stays on-demand.
 */
export async function listEdhCapacityReservations(): Promise<
    [boolean, CapacityReservationView[] | string]
> {
    return toTuple(async () => {
        const resp: EdhListResponse<CapacityReservationView> = await apiClient.get(
            CATALOG_CAPACITY_RESERVATIONS_PATH
        );
        return resp?.Items ?? [];
    });
}
