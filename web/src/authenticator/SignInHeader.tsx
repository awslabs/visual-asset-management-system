/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Heading, useTheme } from "@aws-amplify/ui-react";
import logoDarkImageSrc from "../resources/img/logo_dark.svg";
import logoWhiteImageSrc from "../resources/img/logo_white.png";

export function SignInHeader() {
    const { tokens } = useTheme();
    const isDark = document.body.classList.contains("awsui-dark-mode");

    return (
        <Heading level={3} padding={`${tokens.space.xl} ${tokens.space.xl} 0`}>
            <img
                style={{ width: "100%", paddingTop: 0 }}
                src={isDark ? logoWhiteImageSrc : logoDarkImageSrc}
                alt="Visual Asset Management System Logo"
            />
        </Heading>
    );
}
