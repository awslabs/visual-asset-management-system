/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Establishes global css, fetches server-side resource,
 * configures amplify and initialized react app.
 */
import React, { Suspense } from "react";
import ReactDOM from "react-dom";
import "./styles/index.scss";
import reportWebVitals from "./reportWebVitals";
// import ConfigLoader from "./ConfigLoader";
import VAMSAuth from "./FedAuth/VAMSAuth";

window.LOG_LEVEL = "INFO";

const App = React.lazy(() => import("./App"));
ReactDOM.render(
    <React.StrictMode>
        <VAMSAuth>
            <Suspense fallback={<div />}>
                <App />
            </Suspense>
        </VAMSAuth>
    </React.StrictMode>,
    document.getElementById("root"),
);

reportWebVitals();
