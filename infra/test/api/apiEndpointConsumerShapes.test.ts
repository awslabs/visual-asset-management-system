/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A client-facing API endpoint carries its stage; an ORIGIN is a bare host. Both, deliberately.
 *
 * Amazon API Gateway reads the first path segment as the deployment stage. A client configured with a
 * bare execute-api host therefore names a stage that does not exist, and every request is answered
 * `403 {"message":"Forbidden"}` before any authorizer runs — indistinguishable from a permission
 * denial, and `auth login` still succeeds because it talks to Amazon Cognito directly. Publishing the
 * bare host as a stack output is how that gets into a client's configuration.
 *
 * The construct exposes both forms for good reason, so this is NOT "one member is wrong" (owner
 * question 89, NEW-LEAD-05):
 *
 *   apiEndpoint         bare hostname  -> `cloudfrontOrigins.HttpOrigin(...)`, the ALB listener rules'
 *                                        `host:`, and `security.ts` which prefixes the scheme itself.
 *                                        A URL here breaks the origin.
 *   invokeUrlWithStage  https://host/stage -> anything a client will call.
 *
 * Both directions are asserted, because fixing this by switching every consumer to the URL form would
 * break the CloudFront and ALB fronts — a worse failure than the one being fixed, and silent at synth.
 */

import * as fs from "fs";
import * as path from "path";

const LIB = path.join(__dirname, "..", "..", "lib");
const CORE_STACK = path.join(LIB, "core-stack.ts");
const CLOUDFRONT = path.join(
    LIB,
    "nestedStacks",
    "staticWebApp",
    "constructs",
    "cloudfront-s3-website-construct.ts"
);
const ALB = path.join(
    LIB,
    "nestedStacks",
    "staticWebApp",
    "constructs",
    "alb-s3-website-albDeploy-construct.ts"
);

const read = (p: string) => fs.readFileSync(p, "utf-8");

describe("apiEndpoint vs invokeUrlWithStage, by consumer", () => {
    it("the files this reasons about exist", () => {
        // Control: a moved file would otherwise make every assertion below pass over empty strings.
        for (const f of [CORE_STACK, CLOUDFRONT, ALB]) expect(fs.existsSync(f)).toBe(true);
    });

    it("the client-facing stack output publishes the stage-inclusive URL", () => {
        const text = read(CORE_STACK);
        const block = /new cdk\.CfnOutput\([^)]*"APIGatewayEndpointOutput"[\s\S]{0,400}?\}\);/.exec(
            text
        );
        expect(block).not.toBeNull();
        expect(block![0]).toContain("invokeUrlWithStage");
        // And specifically NOT the bare host, which is the value that produces a stage-less client.
        expect(block![0]).not.toMatch(/value:\s*`?\$\{?\s*apiNestedStack\.apiEndpoint\s*\}?`?\s*,/);
    });

    it("the CloudFront origin still receives a bare host", () => {
        // HttpOrigin takes a DOMAIN NAME. Passing a URL here yields an invalid origin, and nothing at
        // synth time says so — this is the arm that stops the fix being applied too widely.
        const text = read(CLOUDFRONT);
        expect(text).toMatch(/new cloudfrontOrigins\.HttpOrigin\(\s*apiUrl/);
        expect(text).not.toMatch(/new cloudfrontOrigins\.HttpOrigin\(\s*`https:\/\//);
    });

    it("the ALB listener rules still match on a bare host", () => {
        const text = read(ALB);
        expect(text).toMatch(/host:\s*`\$\{props\.apiUrl\}`/);
    });

    it("StaticWeb is still passed the bare host, not the URL", () => {
        const text = read(CORE_STACK);
        expect(text).toMatch(/apiUrl:\s*apiNestedStack\.apiEndpoint/);
        expect(text).not.toMatch(/apiUrl:\s*apiNestedStack\.invokeUrlWithStage/);
    });

    it("the construct derives the stage URL from the host rather than duplicating it", () => {
        // Keeps the two forms from drifting: if the host changes, the URL follows.
        const construct = read(
            path.join(
                LIB,
                "nestedStacks",
                "apiLambda",
                "constructs",
                "rest-api-gateway-construct.ts"
            )
        );
        expect(construct).toMatch(
            /this\.invokeUrlWithStage\s*=\s*`https:\/\/\$\{this\.apiEndpoint\}\/\$\{this\.stageName\}`/
        );
    });
});
