/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

interface VAMSConfig {
    APP_TITLE: string;
    CUSTOMER_LOGO?: string;
}

const config: VAMSConfig = {
    APP_TITLE: "Amazon VAMS",
    // CUSTOMER_LOGO // defines a alternate logo
};

export default config;
