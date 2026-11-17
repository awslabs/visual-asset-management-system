/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useEffect, useState } from "react";
import { checkWebRoutesAllowed } from "../services/webRoutesCheck";

/**
 * Whether the API's web-route decision (`POST /auth/routes`) allows the caller to open `path`.
 * False while the decision loads and when it fails, so a link to the page shows only after the API
 * said yes. The web app derives nothing itself; the same decision gates the page when it opens.
 */
export const useWebRouteAllowed = (path: string): boolean => {
    const [allowed, setAllowed] = useState(false);
    useEffect(() => {
        let stale = false;
        checkWebRoutesAllowed([{ method: "GET", route__path: path }])
            .then((routes) => {
                if (!stale) setAllowed(routes.length > 0);
            })
            .catch(() => {
                if (!stale) setAllowed(false);
            });
        return () => {
            stale = true;
        };
    }, [path]);
    return allowed;
};
