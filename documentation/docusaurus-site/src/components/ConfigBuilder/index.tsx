/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * SSR boundary for the config builder.
 *
 * The builder is fully client-side (it uses Blob/clipboard/document and the
 * Docusaurus color-mode hook). Wrapping it in <BrowserOnly> ensures Docusaurus
 * never evaluates it during the static build, and renders a lightweight
 * placeholder until hydration. This is a component entry, not a re-export
 * barrel (consumers import this file directly).
 */

import React from "react";
import BrowserOnly from "@docusaurus/BrowserOnly";

export default function ConfigBuilderApp() {
    return (
        <BrowserOnly fallback={<div>Loading configuration builder…</div>}>
            {() => {
                // Lazy require keeps the builder out of the SSR bundle entirely.
                const ConfigBuilder = require("./ConfigBuilder").default;
                return <ConfigBuilder />;
            }}
        </BrowserOnly>
    );
}
