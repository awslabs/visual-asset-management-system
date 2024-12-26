/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Establishes global css, fetches server-side resource,
 * configures amplify and initialized react app.
 */
import React, { Suspense, useEffect } from "react";
import ReactDOM from "react-dom";
import "./styles/index.scss";
import reportWebVitals from "./reportWebVitals";
import Auth from "./FedAuth/Auth";
import { default as vamsConfig } from "./config";

window.LOG_LEVEL = "DEBUG";

console.log('vamsConfig', vamsConfig);

// Needed for overrides in custom amplify lib.
window.DISABLE_COGNITO = vamsConfig.DISABLE_COGNITO;

const App = React.lazy(() => import("./App"));
ReactDOM.render(
    <React.StrictMode>
        <Auth>
            <Suspense fallback={<div />}>
                <App />
            </Suspense>
        </Auth>
    </React.StrictMode>,
    document.getElementById("root")
);

reportWebVitals();
