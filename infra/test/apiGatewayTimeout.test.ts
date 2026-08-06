/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Bounds for the REST API integration timeout option
 * (app.api.apiGatewayRest.apiGatewayTimeoutTime).
 *
 * The timeout is validated in getConfig(), which reads config.json from disk and requires a
 * full CDK app, so these tests exercise the bound constants and the same predicate getConfig()
 * applies rather than invoking getConfig() itself. buildOpenApiSpec.test.ts covers the value
 * reaching the integration spec.
 */

import {
    API_GATEWAY_DEFAULT_TIMEOUT_SECONDS,
    API_GATEWAY_MAX_TIMEOUT_SECONDS,
} from "../config/config";

/** The rejection predicate from getConfig()'s apiGatewayTimeoutTime validation. */
function isRejected(value: number): boolean {
    return (
        !Number.isInteger(value) ||
        value < API_GATEWAY_DEFAULT_TIMEOUT_SECONDS ||
        value > API_GATEWAY_MAX_TIMEOUT_SECONDS
    );
}

describe("apiGatewayTimeoutTime bounds", () => {
    it("defaults to the API Gateway default integration timeout of 29 seconds", () => {
        // The default must remain 29 so an existing deployment that does not set the option
        // keeps the stock API Gateway behavior and needs no quota increase.
        expect(API_GATEWAY_DEFAULT_TIMEOUT_SECONDS).toBe(29);
    });

    it("caps at 300 seconds", () => {
        expect(API_GATEWAY_MAX_TIMEOUT_SECONDS).toBe(300);
    });

    it("accepts the inclusive bounds and values between them", () => {
        for (const v of [29, 30, 60, 120, 299, 300]) {
            expect(isRejected(v)).toBe(false);
        }
    });

    it("rejects values below the 29-second floor", () => {
        for (const v of [28, 1, 0, -5]) {
            expect(isRejected(v)).toBe(true);
        }
    });

    it("rejects values above the 300-second ceiling", () => {
        for (const v of [301, 900]) {
            expect(isRejected(v)).toBe(true);
        }
    });

    it("rejects non-integer second values", () => {
        for (const v of [29.5, 100.1, NaN]) {
            expect(isRejected(v)).toBe(true);
        }
    });
});

describe("config templates", () => {
    // Every shipped template must carry the option explicitly at the default, so an operator
    // copying a template sees the field and its stock value rather than relying on the
    // getConfig() backfill.
    const templates = ["commercial", "govcloud", "eusovereign"];

    it.each(templates)("config.template.%s.json sets the default timeout", (name) => {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const cfg = require(`../config/config.template.${name}.json`);
        expect(cfg.app.api.apiGatewayRest.apiGatewayTimeoutTime).toBe(
            API_GATEWAY_DEFAULT_TIMEOUT_SECONDS
        );
    });
});
