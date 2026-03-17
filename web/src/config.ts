/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

interface VAMSConfig {
    APP_TITLE: string;
    CUSTOMER_LOGO?: string;
    DEV_API_ENDPOINT: string;
}
const config: VAMSConfig = {
    APP_TITLE: "Amazon VAMS",
    DEV_API_ENDPOINT: "https://97u6t166yd.execute-api.us-west-2.amazonaws.com/", // Can point to either remote or local API
    // CUSTOMER_LOGO // defines a alternate logo
};

export default config;
