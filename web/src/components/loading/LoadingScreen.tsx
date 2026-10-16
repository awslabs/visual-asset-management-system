/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import Alert from "@cloudscape-design/components/alert";
import Button from "@cloudscape-design/components/button";
import Spinner from "@cloudscape-design/components/spinner";
import React, { useEffect, useState } from "react";
import { reloadPage } from "../../utils/chunkLoadRecovery";

interface LoadingScreenProps {
    /**
     * When set, a notice offering a reload appears once loading has taken this many milliseconds. A slow
     * download is still progressing, so the notice leaves the decision to the user rather than failing it.
     */
    slowNoticeAfterMs?: number;
}

const LoadingScreen = ({ slowNoticeAfterMs }: LoadingScreenProps) => {
    const [slow, setSlow] = useState(false);

    useEffect(() => {
        if (slowNoticeAfterMs === undefined) {
            return;
        }
        const timer = setTimeout(() => setSlow(true), slowNoticeAfterMs);
        return () => clearTimeout(timer);
    }, [slowNoticeAfterMs]);

    return (
        <div
            aria-live="polite"
            aria-label="Loading page content."
            style={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                height: "100%",
                flexFlow: "column",
            }}
        >
            <Spinner size="large" />
            <p>One moment please ...</p>
            {slow && (
                <div style={{ maxWidth: "480px" }} data-testid="loading-slow-notice">
                    <Alert
                        type="info"
                        header="This is taking longer than usual"
                        action={<Button onClick={() => reloadPage()}>Reload</Button>}
                    >
                        The application is still downloading. A slow network connection can delay
                        it; you can keep waiting or reload the page.
                    </Alert>
                </div>
            )}
        </div>
    );
};

export default LoadingScreen;
