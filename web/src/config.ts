/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * VAMS Web Application Configuration
 *
 * This is the single configuration file for customizing the web application.
 * Organizations should modify these values to match their branding and environment.
 */
interface VAMSConfig {
    /** Application title displayed in the browser tab and login pages */
    APP_TITLE: string;

    /** Short application name used in the footer and UI references */
    APP_NAME: string;

    /** Footer copyright text. Set to empty string to hide the footer. */
    FOOTER_COPYRIGHT: string;

    /**
     * Optional custom logo URL for the sidebar navigation header.
     * If set, this logo appears at the top of the left navigation panel.
     * Supports any image URL (relative path or absolute URL).
     * Leave undefined to use the default VAMS logo.
     */
    CUSTOMER_LOGO?: string;

    /**
     * API endpoint for local development.
     * Set to empty string to use the same origin (production default).
     * Set to a URL like "http://localhost:8002/" or an API Gateway URL for dev testing.
     */
    DEV_API_ENDPOINT: string;
}

const config: VAMSConfig = {
    APP_TITLE: "VAMS - Visual Asset Management System",
    APP_NAME: "Visual Asset Management System",
    FOOTER_COPYRIGHT:
        "\u00A9 2026, Amazon Web Services, Inc. or its affiliates. All rights reserved.",
    // CUSTOMER_LOGO: "/path/to/custom-logo.png",
    DEV_API_ENDPOINT: "", // Can point to either remote or local API
};

export default config;
