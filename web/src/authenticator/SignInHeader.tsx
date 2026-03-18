/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Heading, useTheme } from "@aws-amplify/ui-react";
import config from "../config";
import logoDarkImageSrc from "../../logo_dark.png";
import logoWhiteImageSrc from "../../logo_white.png";

export function SignInHeader() {
    const { tokens } = useTheme();
    const isDark = document.body.classList.contains("awsui-dark-mode");

    return (
        <Heading level={3} padding={`${tokens.space.xl} ${tokens.space.xl} 0`}>
            <img
                style={{ width: "100%", maxWidth: "390px", paddingTop: 0 }}
                src={isDark ? logoWhiteImageSrc : logoDarkImageSrc}
                alt={`${config.APP_NAME} Logo`}
            />
        </Heading>
    );
}
