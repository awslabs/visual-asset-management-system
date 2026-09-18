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
 *    does not exist and a grant that would not have matched it. The rule now guards
 *    `pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId` (see "system GenAI metadata pipeline
 *    validation" below); the two configuration keys that carried the original field are rejected
 *    outright.
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

describe("retired pipeline configuration keys", () => {
    const RETIRED = [
        "useGenAiMetadata3dLabeling",
        "useConversionCadMeshMetadataExtraction",
    ] as const;

    test.each(RETIRED)(
        "a config.json carrying app.pipelines.%s is rejected, naming the replacement and the update guide",
        (key) => {
            const run = resolve((c) => {
                c.app.pipelines[key] = { enabled: false };
            });
            expect(run).toThrow(
                new RegExp(`app\\.pipelines\\.${key} is not a supported configuration option`)
            );
            expect(run).toThrow(/app\.pipelines\.useSystemGenAiMetadata/);
            expect(run).toThrow(/update-the-solution\.md#v26-to-v27/);
        }
    );

    test("presence is the condition, not the value: an enabled block is rejected the same way", () => {
        expect(
            resolve((c) => {
                c.app.pipelines.useGenAiMetadata3dLabeling = {
                    enabled: true,
                    bedrockModelId: "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
                };
            })
        ).toThrow(/useGenAiMetadata3dLabeling is not a supported configuration option/);
    });

    test("no shipped template carries either key", () => {
        for (const name of ["commercial", "govcloud", "eusovereign"]) {
            const template = JSON.parse(
                realReadFileSync(
                    path.resolve(__dirname, `../../config/config.template.${name}.json`),
                    "utf-8"
                )
            );
            for (const key of RETIRED) {
                expect(template.app.pipelines).not.toHaveProperty(key);
            }
        }
    });

    test("the restricted templates ship the system GenAI pipeline disabled and vector search off", () => {
        // The pairing that makes their model ids safe: they are only validated when enabled.
        for (const name of ["govcloud", "eusovereign"]) {
            const template = JSON.parse(
                realReadFileSync(
                    path.resolve(__dirname, `../../config/config.template.${name}.json`),
                    "utf-8"
                )
            );
            expect(template.app.pipelines.useSystemGenAiMetadata.enabled).toBe(false);
            expect(template.app.vectorSearch.enabled).toBe(false);
        }
    });

    test("the govcloud template names a us-gov. analysis model; the EU Sovereign template names none", () => {
        const govcloud = JSON.parse(
            realReadFileSync(
                path.resolve(__dirname, "../../config/config.template.govcloud.json"),
                "utf-8"
            )
        );
        const eusovereign = JSON.parse(
            realReadFileSync(
                path.resolve(__dirname, "../../config/config.template.eusovereign.json"),
                "utf-8"
            )
        );
        expect(govcloud.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId).toMatch(
            /^us-gov\./
        );
        expect(eusovereign.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId).toBe("");
        expect(eusovereign.app.vectorSearch.embeddingModelId).toBe("");
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

/** Everything a European Sovereign Cloud config needs to reach the rule under test. */
function euSovereignPartition(c: any) {
    restrictedPartition(c);
    c.env.region = "eusc-de-east-1";
}

/** Vector search on, with an analysis model id every partition accepts (a bare in-Region id). */
function vectorSearchOn(c: any) {
    c.app.vectorSearch.enabled = true;
    c.app.pipelines.useSystemGenAiMetadata.enabled = true;
    c.app.pipelines.useSystemGenAiMetadata.autoRegisterWithVAMS = true;
    c.app.pipelines.useSystemGenAiMetadata.autoRegisterAutoTriggerOnFileUpload = true;
    c.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId =
        "anthropic.claude-sonnet-4-5-20250929-v1:0";
}

describe("vector search validation", () => {
    let warn: jest.SpyInstance;
    beforeEach(() => {
        warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
    });
    afterEach(() => warn.mockRestore());
    const warnings = () => warn.mock.calls.map((call) => String(call[0])).join("\n");

    test("is rejected in the European Sovereign Cloud, naming the partition", () => {
        expect(
            resolve((c) => {
                euSovereignPartition(c);
                vectorSearchOn(c);
            })
        ).toThrow(/DynamoDB vector search is not available in the European Sovereign Cloud/);
    });

    test("is accepted in GovCloud, with a warning about manual model access", () => {
        // Control for the EUSC rule: it is keyed on the partition, not on app.govCloud.enabled.
        expect(
            resolve((c) => {
                restrictedPartition(c);
                vectorSearchOn(c);
            })
        ).not.toThrow();
        expect(warnings()).toContain("app.vectorSearch.enabled is true in a GovCloud deployment");
    });

    test("the shipped commercial template gets no GovCloud warning", () => {
        expect(resolve(() => undefined)).not.toThrow();
        expect(warnings()).not.toContain("GovCloud deployment");
    });

    test("requires the system GenAI metadata pipeline", () => {
        expect(
            resolve((c) => {
                c.app.pipelines.useSystemGenAiMetadata.enabled = false;
            })
        ).toThrow(
            /app\.vectorSearch\.enabled requires app\.pipelines\.useSystemGenAiMetadata\.enabled/
        );
    });

    test("requires the pipeline's registration AND upload trigger, and switches neither on", () => {
        // Explicit rejection rather than auto-mutation, per the policy stated above vpcRequiringFeatures.
        const pattern = /autoRegisterWithVAMS and autoRegisterAutoTriggerOnFileUpload to be true/;
        expect(
            resolve((c) => {
                c.app.pipelines.useSystemGenAiMetadata.autoRegisterAutoTriggerOnFileUpload = false;
            })
        ).toThrow(pattern);
        expect(
            resolve((c) => {
                c.app.pipelines.useSystemGenAiMetadata.autoRegisterWithVAMS = false;
            })
        ).toThrow(pattern);
    });

    test("the pipeline may run with its trigger disarmed while vector search is off", () => {
        expect(
            resolve((c) => {
                c.app.vectorSearch.enabled = false;
                c.app.pipelines.useSystemGenAiMetadata.autoRegisterAutoTriggerOnFileUpload = false;
            })
        ).not.toThrow();
    });

    test("an empty embedding model id is rejected", () => {
        expect(
            resolve((c) => {
                c.app.vectorSearch.embeddingModelId = "   ";
            })
        ).toThrow(/app\.vectorSearch\.embeddingModelId is empty/);
    });

    test.each([[0], [4097], [1.5], ["1024"], [-1]])(
        "embeddingDimensions %p is rejected",
        (value) => {
            expect(
                resolve((c) => {
                    c.app.vectorSearch.embeddingDimensions = value;
                })
            ).toThrow(/embeddingDimensions must be an integer between 1 and 4096/);
        }
    );

    test.each([[1], [256], [4096]])("embeddingDimensions %p is accepted", (value) => {
        expect(
            resolve((c) => {
                c.app.vectorSearch.embeddingDimensions = value;
            })
        ).not.toThrow(/embeddingDimensions/);
    });

    test.each([[0], [1], [1001], [2.5], ["5"]])(
        "indexingConcurrency %p is rejected with the message that names the bound",
        (value) => {
            // CDK's own synth check on the ESM misses 0 and non-integers and names the CDK prop.
            expect(
                resolve((c) => {
                    c.app.vectorSearch.indexingConcurrency = value;
                })
            ).toThrow(
                /vectorSearch\.indexingConcurrency must be an integer between 2 and 1000 \(Lambda SQS event-source MaximumConcurrency bound\)/
            );
        }
    );

    test.each([[2], [1000]])("indexingConcurrency %p (a bound) is accepted", (value) => {
        expect(
            resolve((c) => {
                c.app.vectorSearch.indexingConcurrency = value;
            })
        ).not.toThrow(/indexingConcurrency/);
    });

    test("the value checks are skipped while vector search is disabled", () => {
        expect(
            resolve((c) => {
                c.app.vectorSearch.enabled = false;
                c.app.vectorSearch.embeddingModelId = "";
                c.app.vectorSearch.embeddingDimensions = 0;
                c.app.vectorSearch.indexingConcurrency = 0;
            })
        ).not.toThrow();
    });
});

describe("system GenAI metadata pipeline validation", () => {
    /** Isolates the pipeline rules from the vector-search rules that depend on the pipeline. */
    const vectorSearchOff = (c: any) => {
        c.app.vectorSearch.enabled = false;
    };
    let warn: jest.SpyInstance;
    beforeEach(() => {
        warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
    });
    afterEach(() => warn.mockRestore());
    const warnings = () => warn.mock.calls.map((call) => String(call[0])).join("\n");

    describe.each(["maxInputFileSizeMb", "maxPointCloudPoints"])("lambdaLimits.%s", (field) => {
        const message = new RegExp(
            `pipelines\\.useSystemGenAiMetadata\\.lambdaLimits\\.${field} must be a positive integer`
        );

        test.each([[0], [-5], [1.5], ["2048"]])("%p is rejected, naming the field", (value) => {
            expect(
                resolve((c) => {
                    vectorSearchOff(c);
                    c.app.pipelines.useSystemGenAiMetadata.lambdaLimits[field] = value;
                })
            ).toThrow(message);
        });

        test("1 is accepted", () => {
            expect(
                resolve((c) => {
                    vectorSearchOff(c);
                    c.app.pipelines.useSystemGenAiMetadata.lambdaLimits[field] = 1;
                })
            ).not.toThrow(message);
        });

        test("is not checked while the pipeline is disabled", () => {
            expect(
                resolve((c) => {
                    vectorSearchOff(c);
                    c.app.pipelines.useSystemGenAiMetadata.enabled = false;
                    c.app.pipelines.useSystemGenAiMetadata.lambdaLimits[field] = 0;
                })
            ).not.toThrow();
        });
    });

    test("the shipped commercial analysis model is accepted (control)", () => {
        expect(resolve(() => undefined)).not.toThrow(/bedrockAnalysisModelId/);
    });

    test("an empty analysis model id is rejected when the pipeline is enabled", () => {
        expect(
            resolve((c) => {
                vectorSearchOff(c);
                c.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId = "";
            })
        ).toThrow(
            /pipelines\.useSystemGenAiMetadata is enabled but bedrockAnalysisModelId is empty/
        );
    });

    test('a "global." profile is rejected in GovCloud, naming the prefix', () => {
        expect(
            resolve((c) => {
                restrictedPartition(c);
                vectorSearchOff(c);
                c.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId =
                    "global.anthropic.claude-haiku-4-5-20251001-v1:0";
            })
        ).toThrow(
            /"global\." cross-Region inference-profile prefix exists only in the commercial partition/
        );
    });

    test.each([
        ["global.anthropic.claude-haiku-4-5-20251001-v1:0", "global."],
        ["us.anthropic.claude-sonnet-4-5-20250929-v1:0", "us."],
    ])("%s is rejected in the EU Sovereign Cloud, naming the %s prefix", (id, prefix) => {
        expect(
            resolve((c) => {
                euSovereignPartition(c);
                vectorSearchOff(c);
                c.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId = id;
            })
        ).toThrow(
            new RegExp(
                `"${prefix.replace(
                    ".",
                    "\\."
                )}" cross-Region inference-profile prefix exists only in the commercial partition`
            )
        );
    });

    test('a "us." profile is accepted in GovCloud, with a warning to verify it exists there', () => {
        // The GovCloud inference-profile prefix is unverified and the Claude Sonnet 4.5 model card
        // lists the GovCloud source Regions under the `us.` Geo id, so `us.` is let through in GovCloud
        // with a warning rather than rejected.
        expect(
            resolve((c) => {
                restrictedPartition(c);
                vectorSearchOff(c);
                c.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId =
                    "us.anthropic.claude-sonnet-4-5-20250929-v1:0";
            })
        ).not.toThrow();
        expect(warnings()).toContain(
            "verify that this inference profile exists in your GovCloud account"
        );
    });

    test('a "us." profile in the commercial partition gets no GovCloud warning (control)', () => {
        resolve((c) => {
            vectorSearchOff(c);
            c.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId =
                "us.anthropic.claude-sonnet-4-5-20250929-v1:0";
        })();
        expect(warnings()).not.toContain(
            "verify that this inference profile exists in your GovCloud account"
        );
    });

    test('a "us-gov." profile is accepted in GovCloud', () => {
        expect(
            resolve((c) => {
                restrictedPartition(c);
                vectorSearchOff(c);
                c.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId =
                    "us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0";
            })
        ).not.toThrow(/bedrockAnalysisModelId/);
    });

    test('a "us-gov." profile is rejected in the commercial partition', () => {
        expect(
            resolve((c) => {
                vectorSearchOff(c);
                c.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId =
                    "us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0";
            })
        ).toThrow(
            /"us-gov\." cross-Region inference-profile prefix exists only in the AWS GovCloud \(US\) partition/
        );
    });

    test.each([
        ["commercial", (c: any) => undefined],
        ["GovCloud", restrictedPartition],
        ["EU Sovereign Cloud", euSovereignPartition],
    ])("a bare in-Region model id is accepted in %s", (_name, partition) => {
        expect(
            resolve((c) => {
                partition(c);
                vectorSearchOff(c);
                c.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId =
                    "amazon.nova-2-lite-v1:0";
            })
        ).not.toThrow();
    });

    test("an Anthropic model id warns about the one-time use-case form", () => {
        // The shipped commercial template names Claude Haiku 4.5, so this warning is printed by every
        // commercial synth until the form is submitted; it never fails the synth.
        expect(resolve(() => undefined)).not.toThrow();
        expect(warnings()).toContain("Anthropic requires a one-time use-case form");
    });

    test("a non-Anthropic model id does not get that warning", () => {
        resolve((c) => {
            c.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId =
                "amazon.nova-2-lite-v1:0";
        })();
        expect(warnings()).not.toContain("Anthropic requires a one-time use-case form");
    });

    test("an Anthropic id on a disabled pipeline still warns (the rule reads the id, not the pipeline state)", () => {
        // The condition is the id alone. The shipped govcloud template is this case.
        resolve((c) => {
            vectorSearchOff(c);
            c.app.pipelines.useSystemGenAiMetadata.enabled = false;
        })();
        expect(warnings()).toContain("Anthropic requires a one-time use-case form");
    });

    test("useFargateRenderer requires a VPC, and the error names the sub-flag rather than the pipeline", () => {
        expect(
            resolve((c) => {
                c.app.pipelines.useSystemGenAiMetadata.useFargateRenderer = true;
            })
        ).toThrow(/require a VPC: pipelines\.useSystemGenAiMetadata\.useFargateRenderer\./);
    });

    test("the pipeline itself never requires a VPC", () => {
        // The shipped commercial template enables the pipeline with useGlobalVpc.enabled false.
        expect(resolve(() => undefined)).not.toThrow(/require a VPC/);
    });

    test("useFargateRenderer on a disabled pipeline requires no VPC (the render branch exists only in an enabled pipeline)", () => {
        // The VPC builder keys the renderer's endpoints on `enabled && useFargateRenderer`; the config
        // gate agrees, so a disabled pipeline with the sub-flag left on deploys without a VPC.
        expect(
            resolve((c) => {
                vectorSearchOff(c);
                c.app.pipelines.useSystemGenAiMetadata.enabled = false;
                c.app.pipelines.useSystemGenAiMetadata.useFargateRenderer = true;
            })
        ).not.toThrow(/require a VPC/);
    });

    describe("bedrockGuardrail", () => {
        const withGuardrail = (identifier: string, version: string) => (c: any) => {
            vectorSearchOff(c);
            c.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail = {
                guardrailIdentifier: identifier,
                guardrailVersion: version,
            };
        };

        test("accepts both fields set", () => {
            expect(resolve(withGuardrail("kb4v3hkqvi6f", "1"))).not.toThrow();
        });

        test("accepts both fields empty", () => {
            expect(resolve(withGuardrail("", ""))).not.toThrow();
        });

        test.each([
            ["identifier only", "kb4v3hkqvi6f", ""],
            ["version only", "", "DRAFT"],
        ])("rejects %s", (_label, identifier, version) => {
            expect(resolve(withGuardrail(identifier, version))).toThrow(
                /bedrockGuardrail requires both guardrailIdentifier and guardrailVersion, or neither/
            );
        });

        test("a half-set pair is rejected on a disabled pipeline too", () => {
            expect(
                resolve((c) => {
                    withGuardrail("kb4v3hkqvi6f", "")(c);
                    c.app.pipelines.useSystemGenAiMetadata.enabled = false;
                })
            ).toThrow(/bedrockGuardrail requires both/);
        });

        test("backfills an absent block to two empty strings and a created guardrail", () => {
            const config = resolve((c) => {
                vectorSearchOff(c);
                delete c.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail;
            })();
            expect(config.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail).toEqual({
                guardrailIdentifier: "",
                guardrailVersion: "",
                create: { enabled: true, promptAttackInputStrength: "LOW", piiFilter: "anonymize" },
            });
        });

        test.each(["DRAFT", "1", "42", "99999999"])("accepts guardrailVersion %s", (version) => {
            expect(resolve(withGuardrail("kb4v3hkqvi6f", version))).not.toThrow();
        });

        test.each([
            ["an ARN", "arn:aws:bedrock:us-east-1:123456789012:guardrail/kb4v3hkqvi6f"],
            ["a name", "my-guardrail"],
            ["an upper-case id", "KB4V3HKQVI6F"],
            ["a short id", "kb4v3hkqvi6"],
            ["a long id", "kb4v3hkqvi6f0"],
        ])(
            "rejects guardrailIdentifier that is %s: the IAM grant composes the ARN from the id",
            (_label, identifier) => {
                expect(resolve(withGuardrail(identifier, "1"))).toThrow(
                    /guardrailIdentifier must be the guardrail's 12-character id .* not its ARN or name/
                );
            }
        );

        test.each([
            ["a lower-case draft", "draft"],
            ["zero", "0"],
            ["a negative number", "-1"],
            ["a decimal", "1.0"],
            ["a nine-digit number", "100000000"],
            ["an alias", "latest"],
        ])("rejects guardrailVersion that is %s", (_label, version) => {
            expect(resolve(withGuardrail("kb4v3hkqvi6f", version))).toThrow(
                /guardrailVersion must be "DRAFT" or a published version number/
            );
        });

        test("the format checks apply on a disabled pipeline too, like the pair check", () => {
            expect(
                resolve((c) => {
                    withGuardrail("my-guardrail", "1")(c);
                    c.app.pipelines.useSystemGenAiMetadata.enabled = false;
                })
            ).toThrow(/guardrailIdentifier must be the guardrail's 12-character id/);
        });

        test("an enabled pipeline that neither creates nor names a guardrail deploys with a warning that records the deviation", () => {
            resolve((c) => {
                withGuardrail("", "")(c);
                c.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail.create = {
                    enabled: false,
                    promptAttackInputStrength: "LOW",
                    piiFilter: "anonymize",
                };
            })();
            expect(warnings()).toContain(
                "pipelines.useSystemGenAiMetadata is enabled without a bedrockGuardrail"
            );
            expect(warnings()).toContain("prompt-attack");
            expect(warnings()).toContain("bedrockGuardrail.create.enabled");
        });

        test("no such warning with a created or an operator-owned guardrail, or on a disabled pipeline without one", () => {
            // An empty pair with no create block: the guardrail is created by default.
            resolve(withGuardrail("", ""))();
            expect(warnings()).not.toContain("enabled without a bedrockGuardrail");
            warn.mockClear();
            resolve(withGuardrail("kb4v3hkqvi6f", "1"))();
            expect(warnings()).not.toContain("enabled without a bedrockGuardrail");
            warn.mockClear();
            resolve((c) => {
                withGuardrail("", "")(c);
                c.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail.create = {
                    enabled: false,
                    promptAttackInputStrength: "LOW",
                    piiFilter: "anonymize",
                };
                c.app.pipelines.useSystemGenAiMetadata.enabled = false;
            })();
            expect(warnings()).not.toContain("enabled without a bedrockGuardrail");
        });
    });
});
