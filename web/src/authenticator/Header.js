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
    const bannerMessageHtml = config.bannerHtmlMessage;

    const [useContentSecurityPolicy] = useState(
        contentSecurityPolicy !== undefined && contentSecurityPolicy !== ""
    );

    const [useBannerMessageHtml] = useState(
        bannerMessageHtml !== undefined && bannerMessageHtml !== ""
    );

    function sanitizeHtml(html) {
        // Escape HTML to prevent XSS
        const escapedHtml = html.replace(/&/g, '&amp;')
                                .replace(/</g, '&lt;')
                                .replace(/>/g, '&gt;')
                                .replace(/"/g, '&quot;')
                                .replace(/'/g, '&#039;');

        // Use a regular expression to only allow <strong>, <u>, and <em> tags
        const sanitizedHtml = escapedHtml.replace(
            /(<\/?(?!strong|u|em)\b[^>]*>)/gi, 
            ''
        );

        return sanitizedHtml;
    }

    const sanitizedBannerMessage = sanitizeHtml(bannerMessageHtml);

    return (
        <>
            {" "}
            {useContentSecurityPolicy && (
                <head>
                    <meta httpEquiv="Content-Security-Policy" content={contentSecurityPolicy} />
                </head>
            )}

            {useBannerMessageHtml && (
                <div style={{
                    position: "fixed",
                    backgroundColor: "rgba(231, 94, 64, 1)", 
                    top: 0,
                    left: 0,
                    width: "100vw",
                    zIndex: "100", 
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "white",
                    wordWrap: "break-word", // Enable word wrapping
                    overflowWrap: "break-word", // For cross-browser compatibility
                    textAlign: "center" // Center the text
                }}>
                    <div dangerouslySetInnerHTML={{ __html: sanitizedBannerMessage }} />
                </div>
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
