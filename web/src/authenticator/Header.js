/* eslint-disable jsx-a11y/alt-text */
/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import loginBgImageSrc from "../resources/img/login_bg.png";
import { Cache } from "aws-amplify";
import React, { useState } from "react";

export function Header() {
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
            <div
                style={{
                    position: "fixed",
                    backgroundColor: "#16191f",
                    top: 0,
                    left: 0,
                    width: "100vw",
                    height: "100vh",
                    zIndex: "-200",
                }}
            />
            <img
                src={loginBgImageSrc}
                style={{
                    position: "fixed",
                    top: 0,
                    width: "100vw",
                    left: 0,
                    zIndex: "-100",
                }}
            />
        </>
    );
}
