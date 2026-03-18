/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import config from "../config";

/**
 * Amplify Authenticator footer slot — intentionally empty.
 * Do not add content here; it renders inside the Cognito login form box.
 */
export function Footer() {
    return <></>;
}

/**
 * Page-level footer with copyright text.
 * Rendered at the bottom of the page in App.tsx and Auth.tsx login pages.
 * Content is configurable via config.ts (APP_NAME and FOOTER_COPYRIGHT).
 */
export function PageFooter() {
    if (!config.FOOTER_COPYRIGHT && !config.APP_NAME) return null;

    return (
        <footer
            id="appFooter"
            style={{
                textAlign: "center",
                padding: "8px 0",
                fontSize: "11px",
                color: "var(--vams-text-secondary)",
                borderTop: "1px solid var(--vams-border-default)",
            }}
        >
            {config.APP_NAME}
            {config.APP_NAME && config.FOOTER_COPYRIGHT && <br />}
            {config.FOOTER_COPYRIGHT}
        </footer>
    );
}
