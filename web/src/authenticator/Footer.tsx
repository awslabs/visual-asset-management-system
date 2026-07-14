/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";
import config from "../config";
import { getVamsVersion } from "../services/APIService";

/**
 * Amplify Authenticator footer slot — intentionally empty.
 * Do not add content here; it renders inside the Cognito login form box.
 */
export function Footer() {
    return <></>;
}

/**
 * Page-level footer with copyright text and the backend VAMS version.
 * Rendered at the bottom of the page in App.tsx and Auth.tsx login pages.
 * Content is configurable via config.ts (APP_NAME and FOOTER_COPYRIGHT).
 * The version is read from the anonymous "/api/version" endpoint.
 */
export function PageFooter() {
    const [version, setVersion] = useState<string | null>(null);

    useEffect(() => {
        let active = true;
        // The version is a non-essential display detail: never let a failed lookup
        // surface an error on the page. getVamsVersion resolves to null on failure,
        // and the extra .catch guards against any unexpected rejection.
        getVamsVersion()
            .then((v) => {
                if (active) setVersion(v);
            })
            .catch(() => {
                /* silently ignore — footer simply omits the version */
            });
        return () => {
            active = false;
        };
    }, []);

    if (!config.FOOTER_COPYRIGHT && !config.APP_NAME && !version) return null;

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
            {config.APP_NAME && version
                ? `${config.APP_NAME} - Version ${version}`
                : config.APP_NAME || (version && `Version ${version}`)}
            {(config.APP_NAME || version) && config.FOOTER_COPYRIGHT && <br />}
            {config.FOOTER_COPYRIGHT}
        </footer>
    );
}
