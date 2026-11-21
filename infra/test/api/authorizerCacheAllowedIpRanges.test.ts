/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The REST API authorizer caches no decision when `allowedIpRanges` is set.
 *
 * The authorizer refuses a caller outside `app.authProvider.authorizerOptions.allowedIpRanges`, so its
 * decision depends on the client address. With ranges set both schemes carry
 * `authorizerResultTtlInSeconds: 0`; without them the 30 s and 900 s caches stay. Checked on the
 * synthesized API definition of every shipped template.
 */

import { SynthResult, synthTemplate, TemplateName } from "../support/templateSynth";

const TEMPLATES: TemplateName[] = ["commercial", "govcloud", "eusovereign"];
const RANGES = [
    ["192.0.2.0", "192.0.2.255"],
    ["198.51.100.10", "198.51.100.10"],
];

const withRanges = (name: TemplateName) =>
    synthTemplate(name, {
        mutate: (c: any) => {
            c.app.authProvider.authorizerOptions.allowedIpRanges = RANGES;
        },
        mutateKey: "authorizer-allowed-ip-ranges",
    });

function authorizerTtls(s: SynthResult): { authed: unknown; anon: unknown } {
    const apis = s.ofType("AWS::ApiGateway::RestApi");
    expect(apis).toHaveLength(1);
    const schemes = apis[0].properties.Body.components.securitySchemes;
    return {
        authed: schemes.VamsAuthorizer["x-amazon-apigateway-authorizer"]
            .authorizerResultTtlInSeconds,
        anon: schemes.VamsAnonymousAuthorizer["x-amazon-apigateway-authorizer"]
            .authorizerResultTtlInSeconds,
    };
}

describe.each(TEMPLATES)("%s template: authorizer result cache", (name) => {
    it("is off on both schemes when allowedIpRanges is set", () => {
        expect(authorizerTtls(withRanges(name))).toEqual({ authed: 0, anon: 0 });
    });

    it("keeps 30 s and 900 s with the shipped empty list", () => {
        expect(authorizerTtls(synthTemplate(name))).toEqual({ authed: 30, anon: 900 });
    });
});
