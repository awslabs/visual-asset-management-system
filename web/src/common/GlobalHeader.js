/* eslint-disable jsx-a11y/alt-text */
/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Cache } from "aws-amplify";
import React, { useState } from "react";
import sanitizeHtml from 'sanitize-html';

export function GlobalHeader() {
    const config = Cache.getItem("config");
    const contentSecurityPolicy = config.contentSecurityPolicy;
    const bannerMessageHtml = config.bannerHtmlMessage;

    const [useContentSecurityPolicy] = useState(
        contentSecurityPolicy !== undefined && contentSecurityPolicy !== ""
    );

    const [useBannerMessageHtml] = useState(
        bannerMessageHtml !== undefined && bannerMessageHtml !== ""
    );

    const sanitizedBannerMessage = sanitizeHtml(bannerMessageHtml, {
        allowedTags: ['b', 'em', 'strong', 'u'],
    });

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
                    backgroundColor: "rgba(231, 94, 64, 1)",
                    width: "100vw",
                    color: "white",
                    wordWrap: "break-word", // Allow long words to break
                    overflowWrap: "break-word", // For compatibility with older browsers
                    textAlign: "center"
                }}>
                    <div dangerouslySetInnerHTML={{ __html: sanitizedBannerMessage }} />
                </div>
            )}
        </>
    );
}
