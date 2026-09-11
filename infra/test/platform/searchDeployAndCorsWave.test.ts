/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Four findings whose common shape is a deployment that succeeds while leaving something unusable.
 *
 *  * **S1-INFRA-054** — the OpenSearch schema-deploy custom resource pushed an `ERROR` entry into its
 *    results and then returned `Status: "SUCCESS"`. A failed index creation therefore produced a green
 *    deployment on a cluster with no usable index, where every search returns nothing — which looks
 *    exactly like a deployment that has no content yet. Recovery needs the indexes deleted and
 *    recreated plus a full reindex.
 *
 *  * **S1-INFRA-055** — the reindex trigger declared no dependency on that custom resource, and
 *    CloudFormation orders two custom resources with no declared dependency arbitrarily (Rule 9). The
 *    reindexer reads the index names and endpoint from the SSM parameters the schema-deploy resource
 *    writes, so running first it either fills the previous release's index — a successful upgrade with
 *    an empty search — or fails against an index that does not exist and rolls the stack back.
 *
 *  * **S1-INFRA-013** — the CORS preflight stopped returning `Access-Control-Max-Age`, so a browser
 *    re-issues `OPTIONS` before every cross-origin request. That roughly doubles the request count the
 *    API sees, adds a round trip to every call, and each preflight counts against the stage throttle.
 *
 *  * **S1-INFRA-056** — the bucket-versioning probe in the S3-asset-buckets custom resource caught
 *    every exception and recorded `isVersioningEnabled=false`. An `AccessDenied` on a customer-owned
 *    bucket therefore registered a versioned bucket as unversioned, silently disabling file version
 *    history for its assets, and every later deployment re-read it to the same wrong answer.
 *
 * The two custom-resource handlers are asserted on their SOURCE, because both ship as code the
 * synthesized template only references — an inline Python string in one case and a bundled Node
 * function in the other — so there is no emitted property that carries the behaviour. The assertions
 * are written against the decision each handler makes rather than against a phrase, and each has a
 * paired control so it cannot pass against a handler that lost the capability entirely.
 */

import * as fs from "fs";
import * as path from "path";
import { SynthResult, synthTemplate, TemplateName } from "../support/templateSynth";

const read = (relative: string) =>
    fs.readFileSync(path.resolve(__dirname, "..", relative), "utf-8");

describe("schema-deploy reports a failed index creation (S1-INFRA-054)", () => {
    const source = read(
        "../lib/nestedStacks/searchAndIndexing/constructs/schemaDeploy/deployschema.ts"
    );

    test("the handler still has the three outcomes it distinguishes", () => {
        // The control. Every assertion below is about how an ERROR is treated, so they are all
        // satisfied by a handler that no longer produces one.
        expect(source).toContain('status: "ERROR"');
        expect(source).toContain('status: "CREATED"');
        expect(source).toContain('status: "EXISTS"');
    });

    test("an ERROR result produces a FAILED response", () => {
        expect(source).toContain('Status: "FAILED"');
        expect(source).toMatch(/results\.filter\(\s*\(r\)\s*=>\s*r\.status === "ERROR"\s*\)/);
    });

    test("the failure reason names the indexes that failed", () => {
        // A bare FAILED tells the operator only that something went wrong in a resource whose logs are
        // several clicks away.
        expect(source).toMatch(/Index creation failed for \$\{failed\.length\}/);
        expect(source).toMatch(/\$\{r\.index\}/);
    });

    test("an index that already EXISTS is still a success", () => {
        // The resource is idempotent by design and every redeploy takes that path for every index, so
        // treating EXISTS as a failure would fail every deployment after the first.
        const failureCheck = source.slice(source.indexOf("const failed = results.filter"));
        expect(failureCheck).not.toContain('"EXISTS"');
    });

    test("the deferred-index-creation branch returns before the failure check", () => {
        // That branch deliberately writes the SSM parameters and skips index creation, so it must not be
        // caught by a check that treats a missing index as a fault.
        const deferIndex = source.indexOf("deferIndexCreation");
        const failureIndex = source.indexOf("const failed = results.filter");
        expect(deferIndex).toBeGreaterThan(-1);
        expect(failureIndex).toBeGreaterThan(deferIndex);
    });
});

describe("the reindex trigger runs after schema-deploy (S1-INFRA-055)", () => {
    const builder = read("../lib/nestedStacks/searchAndIndexing/searchBuilder-nestedStack.ts");

    test("both OpenSearch constructs expose their schema-deploy resource", () => {
        // The mechanism the dependency rests on. Without it the builder has nothing to depend on and
        // the assertion below would be satisfied by a dependency on some other construct.
        for (const flavour of ["opensearch-serverless", "opensearch-provisioned"]) {
            const source = read(`../lib/nestedStacks/searchAndIndexing/constructs/${flavour}.ts`);
            expect(source).toContain("public schemaDeployResource: CustomResource;");
            expect(source).toContain("this.schemaDeployResource = new CustomResource");
        }
    });

    test("the builder captures it in BOTH flavours, not just one", () => {
        // A deployment uses one or the other, so a capture in a single branch leaves the race in place
        // for every deployment on the other.
        expect(builder).toContain("schemaDeployResource = aoss.schemaDeployResource;");
        expect(builder).toContain("schemaDeployResource = aos.schemaDeployResource;");
    });

    test("the trigger declares the dependency", () => {
        expect(builder).toMatch(
            /reindexTrigger\.node\.addDependency\(\s*schemaDeployResource\s*\)/
        );
    });

    test("the dependency is declared on the resource, not on the provider", () => {
        // Depending on the provider Lambda would order the trigger after the FUNCTION existing rather
        // than after it having run, which is not the property needed.
        const dependencyLine = builder.slice(
            builder.indexOf("reindexTrigger.node.addDependency"),
            builder.indexOf("reindexTrigger.node.addDependency") + 120
        );
        expect(dependencyLine).not.toContain("Provider");
        expect(dependencyLine).not.toContain("serviceToken");
    });
});

describe("the CORS preflight is cacheable (S1-INFRA-013)", () => {
    const TEMPLATES: TemplateName[] = ["commercial", "govcloud", "eusovereign"];

    /**
     * The inline OpenAPI document the REST API is built from, as JSON text.
     *
     * Serialized rather than flattened. `SynthResult.flatten()` concatenates an object's VALUES and
     * drops its keys, which is right for resolving an ARN out of an `Fn::Join` but wrong here: the thing
     * being asserted is the presence of a response-parameter KEY, and a flattened document contains
     * every value and not one key name. An earlier version of this test read the flattened form and
     * reported the header missing while the emitted document carried it.
     */
    function openApiDocument(synth: SynthResult): string {
        const apis = synth.ofType("AWS::ApiGateway::RestApi");
        expect(apis.length).toBe(1);
        return JSON.stringify((apis[0].properties as any).Body ?? {});
    }

    test.each(TEMPLATES)("%s: OPTIONS returns Access-Control-Max-Age", (templateName) => {
        const document = openApiDocument(synthTemplate(templateName));
        // Control: the preflight itself must be present, or "it carries Max-Age" is satisfied by an API
        // with no OPTIONS method at all.
        expect(document).toContain("Access-Control-Allow-Origin");
        expect(document).toContain("method.response.header.Access-Control-Max-Age");
    });

    test("the value is a positive number of seconds, quoted as API Gateway requires", () => {
        // API Gateway needs the mapping value wrapped in single quotes; an unquoted number is rejected
        // at deploy time rather than at synth.
        const document = openApiDocument(synthTemplate("commercial"));
        const match = /"method\.response\.header\.Access-Control-Max-Age":\s*"'(\d+)'"/.exec(
            document
        );
        expect(match).not.toBeNull();
        expect(Number(match![1])).toBeGreaterThan(0);
        // Chromium caps the preflight cache at 2 hours, so a longer value buys nothing and reads as a
        // misunderstanding.
        expect(Number(match![1])).toBeLessThanOrEqual(7200);
    });

    test("the header is declared on the 200 response as well as mapped", () => {
        // A response parameter that is mapped but not declared is dropped, so both halves are needed
        // and only one of them is visible in the integration block.
        const source = read("../lib/nestedStacks/apiLambda/constructs/buildOpenApiSpec.ts");
        const responseHeaders = source.slice(
            source.indexOf('description: "CORS preflight"'),
            source.indexOf("x-amazon-apigateway-integration")
        );
        expect(responseHeaders).toContain("Access-Control-Max-Age");
    });
});

describe("an unreadable bucket-versioning setting fails the deployment (S1-INFRA-056)", () => {
    const source = read(
        "../lib/nestedStacks/storage/customResources/populateS3AssetBucketsTable.ts"
    );

    /** The inline Python the custom resource actually runs. */
    const inlinePython = (() => {
        const match = /lambda\.Code\.fromInline\(`([\s\S]*?)`\)/.exec(source);
        expect(match).not.toBeNull();
        // A template interpolation would mean the extracted text is not what ships.
        expect(match![1]).not.toContain("${");
        return match![1];
    })();

    test("the probe exists and still answers False for an unversioned bucket", () => {
        // The control, and the behaviour that must survive: a bucket that was never versioned answers
        // with no Status field and is legitimately reported as not enabled.
        expect(inlinePython).toContain("def check_bucket_versioning");
        expect(inlinePython).toContain("response.get('Status') == 'Enabled'");
    });

    test("AccessDenied is raised rather than recorded as not-enabled", () => {
        expect(inlinePython).toContain("from botocore.exceptions import ClientError");
        expect(inlinePython).toContain("except ClientError as e:");
        expect(inlinePython).toContain("AccessDenied");
    });

    test("no exception path returns False any more", () => {
        // The defect was `return False` inside the handler, which turned every failure into an answer.
        const probe = inlinePython.slice(
            inlinePython.indexOf("def check_bucket_versioning"),
            inlinePython.indexOf("def lambda_handler")
        );
        expect(probe).toContain("raise");
        const exceptBlock = probe.slice(probe.indexOf("except ClientError"));
        expect(exceptBlock).not.toMatch(/return\s+False/);
    });

    test("the message names the grant the operator has to add", () => {
        // The failure is a permissions problem on a bucket VAMS does not own, so the actionable detail
        // is the specific action, not that something went wrong.
        expect(inlinePython).toContain("s3:GetBucketVersioning");
    });
});
