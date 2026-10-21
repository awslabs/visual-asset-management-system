/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Add-on hooks for the core tests every add-on has to extend. A core test calls one generic hook,
 * and an add-on that contributes to what the test checks adds its own step here.
 */

import { enableEdhContainers } from "../edh/support/edhSynth";

/**
 * Turn on every add-on that registers API routes, with every optional feature that adds routes of
 * its own, so a synth from a shipped template exposes each add-on route the backend declares
 * (test/api/apiRouteBackendCdkParity.test.ts). The shipped templates leave every add-on disabled.
 */
export function enableRouteContributingAddons(c: any): void {
    // The Physna add-on registers GET /addon/physna/viewer and ships disabled.
    const physna = c.app.addons?.usePhysnaSync;
    if (physna) {
        physna.enabled = true;
        physna.tenantId = "00000000-0000-4000-8000-000000000000";
        physna.apiBaseEndpoint = "https://app-api.physna.com/v3/";
        physna.authTokenEndpoint =
            "https://physna-app.auth.us-east-2.amazoncognito.com/oauth2/token";
        physna.authType = "cognito";
        physna.clientId = "synth-only-client-id";
        physna.clientSecret = "synth-only-client-secret";
        physna.credentialsSecretArn = "";
    }
    // The EDH Workstations add-on registers the /addon/edh/* routes and ships disabled; its container
    // routes exist only with the containers feature on, so the hook enables both.
    enableEdhContainers(c);
    // The AMI recipe routes exist only with amiBuilds on.
    c.app.addons.useEdhWorkstations.amiBuilds.enabled = true;
}
