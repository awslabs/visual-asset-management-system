/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { toastErrorMessage } from "../../../features/orchestration/components/ToastProvider";

/** `Could not refresh: <message>. The list shows the last answer.` */
export const refreshFailedText = (error: unknown) =>
    `Could not refresh: ${toastErrorMessage(error).replace(
        /\.$/,
        ""
    )}. The list shows the last answer.`;

/**
 * The line an EDH list shows above its rows when a later read of a list it already shows fails:
 * TanStack Query keeps the last answer in `data` and sets `error`, so the rows and their actions
 * stay and this line says they may be out of date. A list with no answer yet (`error` and no
 * `data`) shows its own error panel instead.
 */
const RefreshFailedNotice: React.FC<{ error: unknown }> = ({ error }) => (
    <div role="status" className="px-4 py-2 text-sm text-vams-error">
        {refreshFailedText(error)}
    </div>
);

export default RefreshFailedNotice;
