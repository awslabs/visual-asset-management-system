/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Four `getConfig()` rules for configuration that used to deploy and fail later.
 *
 *  * **S1-INFRA-037** — the OIDC placeholder guard rejected two of five shipped values. `clientId`
 *    ("vams-oidc-client") and `cognitoDomainPrefix` ("vams") passed a non-empty check, so a deployment
 *    validated cleanly and federated sign-in failed at the identity provider, where the reported cause
 *    is a rejected client rather than an unedited file.
 *
 *  * **S1-INFRA-099** — every template ships `ecrContainerImageURI` as the literal placeholder, and it
 *    is passed straight to `ContainerImage.fromRegistry`. An unedited value cost a deploy plus a failed
 *    execution to discover.
 *
 *  * **S1-INFRA-035** — the restricted templates pinned a `global.` Bedrock cross-Region inference
 *    profile, which is commercial-only, and nothing validated it. The IAM grant is derived by stripping
 *    the prefix and understood only the commercial ones, so a GovCloud deployment got both a model that
 *    does not exist and a grant that would not have matched it.
 *
 *  * **S1-INFRA-069** — the Physna endpoints were checked for parseability alone. `new URL()` accepts
 *    `http://` and `http://169.254.169.254/`, and the add-on sends the Physna OAuth client secret to
 *    the token endpoint as HTTP Basic credentials.
 *
 * Every case asserts on the MESSAGE, not merely that something threw. These configurations have several
 * ways to be invalid at once — enabling a pipeline can trip a VPC rule, for instance — so "it throws"
 * would pass while the rule under test did nothing.
 */

import * as path from "path";
import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { oidcSettings } from "../../config/oidc-config";
import { newTestApp } from "../support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

/** Builds a config.json from the commercial template, applies `mutate`, and calls getConfig(). */
function resolve(mutate: (c: any) => void): () => Config.Config {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    mutate(config);
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

/** A container image URI that looks real, for the "accepted" side of the placeholder rules. */
const REAL_IMAGE_URI = "123456789012.dkr.ecr.us-east-1.amazonaws.com/vendor/product:1.0.0";

/**
 * Everything a GovCloud-partition config needs in order to reach the rule under test.
 *
 * `getConfig()` validates in order, and the restricted-partition rules run BEFORE the Bedrock one — so
 * without this the assertion sees "GovCloud does not support Cloudfront deployments" and a test written
 * as `.toThrow()` with no pattern would have passed on the wrong error.
 */
function restrictedPartition(c: any) {
    // The REGION, not the partition field: getConfig() derives config.env.partition from the region
    // (`region_info.RegionInfo.get(...).partition`), so a partition set in config.json is overwritten
    // and a test that set it would silently exercise the commercial branch.
    c.env.region = "us-gov-west-1";
    c.app.govCloud.enabled = true;
    c.app.useGlobalVpc.enabled = true;
    c.app.useCloudFront.enabled = false;
    c.app.useAlb.enabled = true;
    c.app.useLocationService.enabled = false;
    // OpenSearch Serverless is not offered in either restricted partition, and next-generation
    // Serverless is rejected outright — another rule that runs before the Bedrock one.
    c.app.openSearch.useServerless.enabled = false;
    c.app.openSearch.useServerless.nextGen = false;
    c.app.openSearch.useProvisioned.enabled = true;
}

afterEach(() => {
    // Restored to delegating rather than cleared, or the synth harness in any later test in this
    // process reads nothing instead of failing outright.
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

describe("the shipped configuration still validates", () => {
    test("the commercial template passes getConfig() unchanged", () => {
        // The control for every rule below. Each one is an added `throw`, and the cheapest way to get
        // them all wrong is to reject a configuration VAMS ships — which this catches immediately.
        expect(resolve(() => undefined)).not.toThrow();
    });
});

describe("OIDC placeholder rejection (S1-INFRA-037)", () => {
    /** Enables OIDC federation, which is what makes getConfig() read oidcSettings. */
    const enableOidc = (c: any) => {
        c.app.authProvider.useCognito.enabled = true;
        c.app.authProvider.useCognito.useOidc = true;
        c.app.authProvider.useCognito.useSaml = false;
    };

    test("the shipped oidc-config really does hold the values under test", () => {
        // The premise. If someone edits oidc-config.ts these assertions become about nothing, and the
        // rejection tests below would pass because the file happens to be valid rather than because the
        // guard works.
        expect(oidcSettings.clientId).toBe("vams-oidc-client");
        expect(oidcSettings.cognitoDomainPrefix).toBe("vams");
        expect(oidcSettings.issuerUrl).toContain("your-idp.example.com");
    });

    test("enabling OIDC with the shipped file is rejected, and the message names the fields", () => {
        expect(resolve(enableOidc)).toThrow(/placeholder values for: .*clientId/);
    });

    test("the message names cognitoDomainPrefix too, not just the first offender", () => {
        // A guard that stopped at the first match would leave the operator editing one field at a time.
        expect(resolve(enableOidc)).toThrow(/cognitoDomainPrefix/);
    });

    test("issuerUrl and clientSecretArn are still rejected", () => {
        // The two the original guard covered — they must not have been lost in widening it.
        expect(resolve(enableOidc)).toThrow(/issuerUrl/);
        expect(resolve(enableOidc)).toThrow(/clientSecretArn/);
    });

    test("name and displayName as shipped are NOT rejected", () => {
        // Deliberately not treated as placeholders: "ExternalOIDC" and "SSO" are usable values for a
        // provider name and a sign-in button label, so requiring different ones would add friction and
        // catch no misconfiguration. Asserted so the decision is visible rather than an omission.
        const message = (() => {
            try {
                resolve(enableOidc)();
                return "";
            } catch (e) {
                return (e as Error).message;
            }
        })();
        expect(message).toContain("placeholder values for");
        expect(message).not.toContain("displayName");
        expect(message).not.toMatch(/\bname\b,/);
    });
});

describe("container image placeholder rejection (S1-INFRA-099)", () => {
    const cases: Array<[string, (c: any) => void, RegExp]> = [
        [
            "useRapidPipeline.useEcs",
            (c) => {
                c.app.useGlobalVpc.enabled = true;
                c.app.pipelines.useRapidPipeline.enabled = true;
                c.app.pipelines.useRapidPipeline.useEcs.enabled = true;
            },
            /useRapidPipeline\.useEcs is enabled but ecrContainerImageURI/,
        ],
        [
            "useRapidPipeline.useEks",
            (c) => {
                c.app.useGlobalVpc.enabled = true;
                c.app.pipelines.useRapidPipeline.enabled = true;
                c.app.pipelines.useRapidPipeline.useEks.enabled = true;
            },
            /useRapidPipeline\.useEks is enabled but ecrContainerImageURI/,
        ],
        [
            "useModelOps",
            (c) => {
                c.app.useGlobalVpc.enabled = true;
                c.app.pipelines.useModelOps.enabled = true;
            },
            /useModelOps is enabled but ecrContainerImageURI/,
        ],
    ];

    describe.each(cases)("%s", (_name, enable, expected) => {
        test("the shipped placeholder is rejected and named", () => {
            expect(resolve(enable)).toThrow(expected);
            expect(resolve(enable)).toThrow(/<ACCOUNTID>/);
        });

        test("a real image URI is accepted", () => {
            // The other side: the rule must not reject a configured deployment.
            expect(
                resolve((c) => {
                    enable(c);
                    // Set on all three, since `enable` may switch on more than one.
                    c.app.pipelines.useRapidPipeline.useEcs.ecrContainerImageURI = REAL_IMAGE_URI;
                    c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI = REAL_IMAGE_URI;
                    c.app.pipelines.useModelOps.ecrContainerImageURI = REAL_IMAGE_URI;
                })
            ).not.toThrow(/ecrContainerImageURI/);
        });

        test("an empty value is rejected too, and says so", () => {
            expect(
                resolve((c) => {
                    enable(c);
                    c.app.pipelines.useRapidPipeline.useEcs.ecrContainerImageURI = "";
                    c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI = "";
                    c.app.pipelines.useModelOps.ecrContainerImageURI = "";
                })
            ).toThrow(/The value is empty/);
        });
    });

    test("a DISABLED pipeline keeps its placeholder without complaint", () => {
        // The backwards-compatibility requirement, and why the rule is per pipeline: all three ship
        // disabled with the placeholder in place, so a blanket check would reject every shipped config.
        expect(resolve(() => undefined)).not.toThrow(/ecrContainerImageURI/);
    });
});

describe("Bedrock model id validation (S1-INFRA-035)", () => {
    const enableGenAi = (c: any) => {
        c.app.useGlobalVpc.enabled = true;
        c.app.pipelines.useGenAiMetadata3dLabeling.enabled = true;
    };

    test("a commercial inference profile is accepted in the commercial partition", () => {
        // The control: the shipped commercial value must keep working.
        expect(
            resolve((c) => {
                enableGenAi(c);
                c.env.partition = "aws";
                c.app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId =
                    "global.anthropic.claude-sonnet-4-5-20250929-v1:0";
            })
        ).not.toThrow(/bedrockModelId/);
    });

    test("a commercial inference profile is rejected in a restricted partition", () => {
        expect(
            resolve((c) => {
                enableGenAi(c);
                restrictedPartition(c);
                c.app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId =
                    "global.anthropic.claude-sonnet-4-20250514-v1:0";
            })
        ).toThrow(/exists only in the commercial partition/);
    });

    test('the "us." prefix is rejected there as well', () => {
        expect(
            resolve((c) => {
                enableGenAi(c);
                restrictedPartition(c);
                c.app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId =
                    "us.anthropic.claude-sonnet-4-20250514-v1:0";
            })
        ).toThrow(/exists only in the commercial partition/);
    });

    test('a "us-gov." prefix is accepted in GovCloud', () => {
        // The escape hatch has to work, or the rule is a way of writing "this pipeline is unavailable".
        expect(
            resolve((c) => {
                enableGenAi(c);
                restrictedPartition(c);
                c.app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId =
                    "us-gov.anthropic.claude-sonnet-4-20250514-v1:0";
            })
        ).not.toThrow(/bedrockModelId/);
    });

    test("an empty model id is rejected when the pipeline is enabled", () => {
        // Which is the state the restricted templates now ship in, so enabling the pipeline there
        // fails at synth naming the field rather than at the first job.
        expect(
            resolve((c) => {
                enableGenAi(c);
                c.app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId = "";
            })
        ).toThrow(/bedrockModelId is empty/);
    });

    test("the restricted templates ship it empty and the pipeline disabled", () => {
        // The pairing that makes emptying the value safe: it is only required when enabled.
        for (const name of ["govcloud", "eusovereign"]) {
            const template = JSON.parse(
                realReadFileSync(
                    path.resolve(__dirname, `../../config/config.template.${name}.json`),
                    "utf-8"
                )
            );
            const genAi = template.app.pipelines.useGenAiMetadata3dLabeling;
            expect(genAi.bedrockModelId).toBe("");
            expect(genAi.enabled).toBe(false);
        }
    });
});

describe("Physna outbound endpoint validation (S1-INFRA-069)", () => {
    /** Enables the add-on with everything else it requires already valid. */
    const enablePhysna = (c: any, overrides: Record<string, string> = {}) => {
        const physna = c.app.addons.usePhysnaSync;
        physna.enabled = true;
        physna.tenantId = "3f6c1b52-9d24-4a7e-8b0f-1c2d3e4f5a6b";
        physna.credentialsSecretArn =
            "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams/physna-AbCdEf";
        Object.assign(physna, overrides);
    };

    test("the shipped endpoints are accepted", () => {
        // Control: both shipped values are public https URLs and must keep passing.
        expect(resolve((c) => enablePhysna(c))).not.toThrow(/apiBaseEndpoint|authTokenEndpoint/);
    });

    test("a plain http endpoint is rejected, naming cleartext", () => {
        expect(
            resolve((c) => enablePhysna(c, { apiBaseEndpoint: "http://app-api.physna.com/v3/" }))
        ).toThrow(/must use https/);
    });

    test("the instance metadata address is rejected", () => {
        expect(
            resolve((c) =>
                enablePhysna(c, { authTokenEndpoint: "https://169.254.169.254/oauth2/token" })
            )
        ).toThrow(/loopback, link-local, or private address/);
    });

    test.each([
        ["https://10.1.2.3/oauth2/token"],
        ["https://192.168.5.5/oauth2/token"],
        ["https://172.16.0.9/oauth2/token"],
        ["https://127.0.0.1/oauth2/token"],
        ["https://localhost/oauth2/token"],
        ["https://token.internal/oauth2/token"],
    ])("a private or loopback host is rejected: %s", (endpoint) => {
        expect(resolve((c) => enablePhysna(c, { authTokenEndpoint: endpoint }))).toThrow(
            /loopback, link-local, or private address/
        );
    });

    test("a public address that merely looks similar is accepted", () => {
        // 172.32 is outside the private 172.16/12 range, and 11.x is public. A rule written with a
        // looser pattern would reject these, which is a different defect from the one being fixed.
        expect(
            resolve((c) =>
                enablePhysna(c, { authTokenEndpoint: "https://172.32.0.1/oauth2/token" })
            )
        ).not.toThrow(/loopback, link-local, or private/);
        expect(
            resolve((c) => enablePhysna(c, { authTokenEndpoint: "https://11.0.0.1/oauth2/token" }))
        ).not.toThrow(/loopback, link-local, or private/);
    });

    test("the api base endpoint is checked too, not only the token endpoint", () => {
        // The token endpoint carries the credential, but the api base receives the bearer token, so a
        // fix applied to one is not a fix.
        expect(
            resolve((c) => enablePhysna(c, { apiBaseEndpoint: "https://10.0.0.5/v3/" }))
        ).toThrow(/apiBaseEndpoint.*loopback, link-local, or private/s);
    });
});
