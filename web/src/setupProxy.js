/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// This file is automatically picked up by create-react-app's webpack dev server
// It allows us to configure middleware, including setting headers for SharedArrayBuffer support

module.exports = function (app) {
    // Set COOP and COEP headers for SharedArrayBuffer support
    app.use((req, res, next) => {
        // Set headers for all responses
        res.setHeader("Cross-Origin-Embedder-Policy", "credentialless");
        res.setHeader("Cross-Origin-Opener-Policy", "same-origin");

        // Also set CORP for resources
        res.setHeader("Cross-Origin-Resource-Policy", "cross-origin");

        next();
    });

    console.log("✓ COOP/COEP headers configured for SharedArrayBuffer support");
};
