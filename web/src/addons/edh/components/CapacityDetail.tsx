/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { describeCapacity } from "../capacityReservations";
import type { WorkstationRecord } from "../types";

type CapacityFields = Partial<
    Pick<
        WorkstationRecord,
        "capacityReservationId" | "capacityReservationLabel" | "capacityReservationFallback"
    >
>;

/**
 * The row's Capacity detail beneath its instance type, or nothing for an on-demand launch. Shared by
 * My Workstations and the admin Sessions tab.
 */
const CapacityDetail: React.FC<{ row: CapacityFields }> = ({ row }) => {
    const text = describeCapacity(row);
    return text ? (
        <span className="block text-xs text-text-secondary">{`Capacity: ${text}`}</span>
    ) : null;
};

export default CapacityDetail;
