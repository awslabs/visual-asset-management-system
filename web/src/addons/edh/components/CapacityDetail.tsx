/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { type CapacityDetailFields, describeCapacity } from "../capacityReservations";

/**
 * The row's Capacity detail beneath its instance type, or nothing for an on-demand launch. Shared by
 * My Workstations and the admin Sessions tab.
 */
const CapacityDetail: React.FC<{ row: CapacityDetailFields }> = ({ row }) => {
    const text = describeCapacity(row);
    return text ? (
        <span className="block text-xs text-text-secondary">{`Capacity: ${text}`}</span>
    ) : null;
};

export default CapacityDetail;
