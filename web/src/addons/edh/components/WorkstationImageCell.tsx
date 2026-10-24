/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useId } from "react";
import { newerImageNotice, workstationImageName, type WorkstationImageFields } from "../labels";

interface WorkstationImageCellProps {
    row: WorkstationImageFields;
    /** The name the live catalog lists for the row's image id; absent when it lists none. */
    catalogName?: string | null;
}

/**
 * The Image cell of My Workstations: the image name (see `workstationImageName`) with the image id
 * beneath it, the id alone when nothing names the image, and the `Newer image: v<n>` badge on a live
 * workstation whose recipe has a newer build. The badge says it in words; its sentence is the hover
 * title and, through a visually hidden element, the description a screen reader announces with it.
 */
const WorkstationImageCell: React.FC<WorkstationImageCellProps> = ({ row, catalogName }) => {
    const descriptionId = useId();
    const name = workstationImageName(row, catalogName);
    const notice = newerImageNotice(row);
    return (
        <div className="text-sm space-y-1">
            {name === row.imageId ? (
                <div className="font-mono text-xs">{row.imageId}</div>
            ) : (
                <div>
                    <div>{name}</div>
                    <div className="text-text-secondary font-mono text-xs">{row.imageId}</div>
                </div>
            )}
            {notice && (
                <>
                    <span
                        title={notice.description}
                        aria-describedby={descriptionId}
                        className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-800 dark:bg-blue-900/30 dark:text-blue-200"
                    >
                        {notice.label}
                    </span>
                    <span id={descriptionId} className="sr-only">
                        {notice.description}
                    </span>
                </>
            )}
        </div>
    );
};

export default WorkstationImageCell;
