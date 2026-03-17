/* eslint-disable jsx-a11y/alt-text */
/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { appCache } from "../services/appCache";
import React, { useState } from "react";
import sanitizeHtml from "sanitize-html";

export function GlobalHeader({ authorizationHeader = false }) {
    const config = appCache.getItem("config");
    const contentSecurityPolicy = config.contentSecurityPolicy;
    const bannerMessageHtml = config.bannerHtmlMessage;

    //console.log(`config: ${JSON.stringify(config, null, 2)}`)

    // Skip CSP meta tag injection during local development. The production CSP is
    // generated for the deployed domain (CloudFront/ALB) and its 'self' directive
    // does not match localhost, causing spurious CSP violations in dev browsers.
    // In production, CSP is enforced at the infrastructure level (CloudFront
    // response headers or ALB listener attributes), so this meta tag is only
    // needed as a fallback for non-standard deployments.
    const isDevelopment = process.env.NODE_ENV === "development";
    const [useContentSecurityPolicy] = useState(
        !isDevelopment && contentSecurityPolicy !== undefined && contentSecurityPolicy !== ""
    );

    const [useBannerMessageHtml] = useState(
        bannerMessageHtml !== undefined && bannerMessageHtml !== ""
    );

    const sanitizedBannerMessage = sanitizeHtml(bannerMessageHtml, {
        allowedTags: ["b", "em", "strong", "u"],
    });

    return (
        <>
            {" "}
            {useContentSecurityPolicy && (
                <head>
                    <meta httpEquiv="Content-Security-Policy" content={contentSecurityPolicy} />
                </head>
            )}
            {useBannerMessageHtml &&
                !authorizationHeader && ( //Used for non-authorization pages
                    <div
                        style={{
                            position: "sticky",
                            top: 56,
                            left: 0,
                            zIndex: "900",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            backgroundColor: "rgba(231, 94, 64, 1)",
                            width: "100vw",
                            color: "white",
                            wordWrap: "break-word", // Allow long words to break
                            overflowWrap: "break-word", // For compatibility with older browsers
                            textAlign: "center",
                        }}
                    >
                        <div dangerouslySetInnerHTML={{ __html: sanitizedBannerMessage }} />
                    </div>
                )}
            {authorizationHeader &&
                authorizationHeader && ( //Used for authorization pages
                    <div
                        style={{
                            position: "sticky",
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
                            textAlign: "center", // Center the text
                        }}
                    >
                        <div dangerouslySetInnerHTML={{ __html: sanitizedBannerMessage }} />
                    </div>
                )}
        </>
    );
}
