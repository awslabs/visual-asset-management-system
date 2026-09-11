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
import reportWebVitals from "./reportWebVitals";
import Auth from "./FedAuth/Auth";
import config from "./config";

(window as any).LOG_LEVEL = "INFO";

// Set browser tab title from config
document.title = config.APP_TITLE;

const App = React.lazy(() => import("./App"));
const container = document.getElementById("root");
const root = createRoot(container!);
root.render(
    <React.StrictMode>
        <Auth>
            <Suspense fallback={<div />}>
                <App />
            </Suspense>
        </Auth>
    </React.StrictMode>
);

reportWebVitals();
