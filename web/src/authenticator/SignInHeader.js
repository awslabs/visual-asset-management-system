/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Heading, useTheme } from "@aws-amplify/ui-react";
import logoDarkImageSrc from "../resources/veerum/img/logo_dark.png";

export function SignInHeader() {
    const { tokens } = useTheme();

    return (
        <Heading level={3} padding={`${tokens.space.xl} ${tokens.space.xl} 0`}>
            <img
                style={{ width: "100%" }}
                src={logoDarkImageSrc}
                alt="Visual Asset Management System Logo"
            />
        </Heading>
    );
}
