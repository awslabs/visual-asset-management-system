/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Establishes global css, fetches server-side resource,
 * configures amplify and initialized react app.
 */
import React, { Suspense, useEffect } from "react";
import { createRoot } from "react-dom/client";
import "./styles/index.scss";
import "./styles/theme.css";
import "./styles/tailwind.css";
import Auth from "./FedAuth/Auth";
import config from "./config";
import LoadingScreen from "./components/loading/LoadingScreen";
import { APP_LOAD_SLOW_NOTICE_MS, lazyWithRetry } from "./utils/chunkLoadRecovery";

(window as any).LOG_LEVEL = "INFO";

// Set browser tab title from config
document.title = config.APP_TITLE;

const loadApp = () => import("./App");
const App = lazyWithRetry(loadApp);

// Start downloading the application once the page has loaded, so it arrives while the sign-in page is
// shown. The lazy component reuses the in-flight module fetch, and a failure here is left to its retry.
window.addEventListener(
    "load",
    () => {
        loadApp().catch(() => undefined);
    },
    { once: true }
);

const container = document.getElementById("root");
const root = createRoot(container!);
root.render(
    <React.StrictMode>
        <Auth>
            <Suspense fallback={<LoadingScreen slowNoticeAfterMs={APP_LOAD_SLOW_NOTICE_MS} />}>
                <App />
            </Suspense>
        </Auth>
    </React.StrictMode>
);
