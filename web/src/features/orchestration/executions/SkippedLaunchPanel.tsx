/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { Link } from "react-router-dom";
import type { ExecutionSkip } from "../types";

const SCOPE_LABELS: Record<string, string> = {
    asset: "Per asset",
    assetFile: "Per input file",
    assetFileVersion: "Per input file version",
};

const formatWhen = (value: string) => (value ? new Date(value).toLocaleString() : "N/A");

/**
 * The detail page's panel for a SKIPPED record: the file-upload trigger launches the workflow's
 * concurrency restriction refused while another run held the lock. Everything shown comes from the
 * details API's `skip` block. The blocking run is linked only when the API sent its id, which it does
 * only for a caller allowed to read that run.
 */
const SkippedLaunchPanel: React.FC<{ skip: ExecutionSkip }> = ({ skip }) => {
    const launches =
        skip.count === 1 ? "1 file-upload launch was" : `${skip.count} file-upload launches were`;
    return (
        <section
            aria-labelledby="skipped-launch-heading"
            data-testid="skipped-launch-panel"
            className="orch-outline mt-4 p-3 bg-gray-50 dark:bg-gray-800/40 border border-gray-200 dark:border-gray-700 rounded"
        >
            <h2
                id="skipped-launch-heading"
                className="text-sm font-semibold text-text-primary mb-1"
            >
                Skipped launches
            </h2>
            <p className="text-sm text-text-secondary">
                {launches} skipped and did not run. {skip.reason}
            </p>
            <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-1 mt-2 text-sm">
                <div>
                    <dt className="inline font-medium text-text-primary">Restriction: </dt>
                    <dd className="inline">{SCOPE_LABELS[skip.scope] || skip.scope || "N/A"}</dd>
                </div>
                <div>
                    <dt className="inline font-medium text-text-primary">Skipped launches: </dt>
                    <dd className="inline">{skip.count}</dd>
                </div>
                <div>
                    <dt className="inline font-medium text-text-primary">First skipped: </dt>
                    <dd className="inline">{formatWhen(skip.firstSkippedDate)}</dd>
                </div>
                <div>
                    <dt className="inline font-medium text-text-primary">Last skipped: </dt>
                    <dd className="inline">{formatWhen(skip.lastSkippedDate)}</dd>
                </div>
                {skip.blockingExecutionId && (
                    <div className="md:col-span-2">
                        <dt className="inline font-medium text-text-primary">Blocked by: </dt>
                        <dd className="inline">
                            <Link
                                to={`/executions/${skip.blockingExecutionId}`}
                                className="font-mono text-blue-600 dark:text-blue-400 hover:underline"
                            >
                                {skip.blockingExecutionId}
                            </Link>
                        </dd>
                    </div>
                )}
            </dl>
            {skip.files.length > 0 && (
                <div className="mt-2">
                    <p className="text-sm font-medium text-text-primary">Files</p>
                    <ul className="text-sm font-mono list-disc ml-5">
                        {skip.files.map((f) => (
                            <li
                                key={`${f.databaseId}:${f.assetId}:${f.inputAssetFileKey}:${
                                    f.versionId || ""
                                }`}
                            >
                                {f.inputAssetFileKey}
                                {f.versionId ? ` (version ${f.versionId})` : ""}
                            </li>
                        ))}
                    </ul>
                    {skip.filesTruncated && (
                        <p className="text-sm text-text-secondary mt-1">
                            Not every skipped file is listed here. The Inputs section lists the rest
                            that were recorded.
                        </p>
                    )}
                </div>
            )}
        </section>
    );
};

export default SkippedLaunchPanel;
