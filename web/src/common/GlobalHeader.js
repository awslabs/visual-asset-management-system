/* eslint-disable jsx-a11y/alt-text */
/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Cache } from "aws-amplify";
import React, { useState } from "react";

export function GlobalHeader() {
    const config = Cache.getItem("config");
    const contentSecurityPolicy = config.contentSecurityPolicy;

    const [useContentSecurityPolicy] = useState(
        contentSecurityPolicy !== undefined && contentSecurityPolicy !== ""
    );

    return (
        <>
            {" "}
            {useContentSecurityPolicy && (
                <head>
                    <meta httpEquiv="Content-Security-Policy" content={contentSecurityPolicy} />
                </head>
            )}
        </>
    );
}
