/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `app.compliance.autoLoadDefaultSchema` requires `app.metadataSchema.autoLoadDefaultAssetSchema`.
 *
 * The seeded `default-compliance-schema` holds one metadata rule that references the GLOBAL
 * `defaultAsset` metadata schema, and only `autoLoadDefaultAssetSchema` seeds that schema. The two
 * flags are independent switches in unrelated config blocks, and nothing at deploy time joins them: the
 * compliance seed writes its row whether or not the metadata schema exists. At runtime the evaluation
 * engine skips a metadata rule's required-field and type checks when the referenced schema resolves to
 * nothing, so the dangling seed reports every asset compliant while checking nothing — a silent wrong
 * outcome rather than an error, which is what makes it a `getConfig()` rule (root Rule 3).
 *
 * The rule is asserted on both sides of the mirror. `getConfig()` is the deployment's authority, and
 * the ConfigBuilder's `validation.ts` is hand-ported from it with no drift check of its own
 * (`configBuilderSync.test.ts` covers `schema.ts` and `defaults.ts` only). A builder that approves a
 * configuration `cdk synth` rejects is worse than one with no rule, because the operator has been told
 * the config is valid — so the same flag combinations are driven through both and required to agree.
 *
 * Every rejection asserts on the MESSAGE, not merely that something threw: a config can be invalid
 * several ways at once, and "it throws" passes while the rule under test does nothing.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";
import { RULES } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

/** A deployable config.json from the commercial template with `mutate` applied. */
function configFrom(mutate: (c: any) => void): any {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    mutate(config);
    return config;
}

/** Serves `config` as config.json and returns a thunk that runs getConfig() against it. */
function resolve(config: any): () => Config.Config {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

const RULE_ID = "compliance-default-schema-requires-default-asset-metadata-schema";
const MESSAGE =
    /app\.compliance\.autoLoadDefaultSchema is true but app\.metadataSchema\.autoLoadDefaultAssetSchema is false/;

/** The ConfigBuilder rule under test, or undefined when the port is missing. */
const builderRule = RULES.find((rule) => rule.id === RULE_ID);

/**
 * Every combination of the two flags, plus the absent-field shapes `getConfig()` backfills.
 * `violates` is the expected verdict on BOTH sides of the mirror.
 */
const CASES: Array<{ name: string; mutate: (c: any) => void; violates: boolean }> = [
    {
        name: "both true (the shipped default)",
        mutate: (c) => {
            c.app.compliance.autoLoadDefaultSchema = true;
            c.app.metadataSchema.autoLoadDefaultAssetSchema = true;
        },
        violates: false,
    },
    {
        name: "compliance seed on, default asset metadata schema off",
        mutate: (c) => {
            c.app.compliance.autoLoadDefaultSchema = true;
            c.app.metadataSchema.autoLoadDefaultAssetSchema = false;
        },
        violates: true,
    },
    {
        name: "compliance seed off, default asset metadata schema off",
        mutate: (c) => {
            c.app.compliance.autoLoadDefaultSchema = false;
            c.app.metadataSchema.autoLoadDefaultAssetSchema = false;
        },
        violates: false,
    },
    {
        name: "compliance seed off, default asset metadata schema on",
        mutate: (c) => {
            c.app.compliance.autoLoadDefaultSchema = false;
            c.app.metadataSchema.autoLoadDefaultAssetSchema = true;
        },
        violates: false,
    },
    {
        // getConfig() backfills a missing app.compliance block with autoLoadDefaultSchema: true, so an
        // older config.json that predates the block is still held to the rule.
        name: "compliance block absent (backfilled to seed), default asset metadata schema off",
        mutate: (c) => {
            delete c.app.compliance;
            c.app.metadataSchema.autoLoadDefaultAssetSchema = false;
        },
        violates: true,
    },
    {
        // A missing metadataSchema block is backfilled with every flag true, so the seed has its schema.
        name: "metadataSchema block absent (backfilled to seed everything)",
        mutate: (c) => {
            c.app.compliance.autoLoadDefaultSchema = true;
            delete c.app.metadataSchema;
        },
        violates: false,
    },
    {
        // Only the whole block is backfilled. A present block with the flag missing leaves it undefined,
        // which the seeding construct reads as "do not seed" — so the compliance seed would dangle.
        name: "metadataSchema block present, autoLoadDefaultAssetSchema missing",
        mutate: (c) => {
            c.app.compliance.autoLoadDefaultSchema = true;
            delete c.app.metadataSchema.autoLoadDefaultAssetSchema;
        },
        violates: true,
    },
];

afterEach(() => {
    // Restored to delegating rather than cleared, or the synth harness in any later test in this
    // process reads nothing instead of failing outright.
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

describe("getConfig(): the default compliance schema needs the default asset metadata schema", () => {
    test("[control] the commercial template passes unchanged", () => {
        // The cheapest way to get an added throw wrong is to reject a configuration VAMS ships.
        expect(resolve(configFrom(() => undefined))).not.toThrow();
    });

    test("[control] the template really ships both flags true", () => {
        // The premise of the control above. Were either shipped false, "passes unchanged" would be
        // asserting about a different rule.
        expect((commercialTemplate as any).app.compliance.autoLoadDefaultSchema).toBe(true);
        expect((commercialTemplate as any).app.metadataSchema.autoLoadDefaultAssetSchema).toBe(
            true
        );
    });

    test.each(CASES.filter((c) => c.violates))("rejects: $name", ({ mutate }) => {
        expect(resolve(configFrom(mutate))).toThrow(MESSAGE);
    });

    test.each(CASES.filter((c) => !c.violates))("accepts: $name", ({ mutate }) => {
        expect(resolve(configFrom(mutate))).not.toThrow();
    });

    test("the message says which flag to change", () => {
        // Naming only the failing combination leaves the operator to guess which side to move; the
        // message must offer both resolutions.
        const message = (() => {
            try {
                resolve(
                    configFrom((c) => {
                        c.app.metadataSchema.autoLoadDefaultAssetSchema = false;
                    })
                )();
                return "";
            } catch (e) {
                return (e as Error).message;
            }
        })();
        expect(message).toMatch(/^Configuration Error: /);
        expect(message).toContain("Set app.metadataSchema.autoLoadDefaultAssetSchema to true");
        expect(message).toContain("set app.compliance.autoLoadDefaultSchema to false");
    });
});

describe("ConfigBuilder validation.ts mirrors the rule", () => {
    test("[control] the rule is ported, as an error naming both fields", () => {
        expect(builderRule).toBeDefined();
        expect(builderRule!.severity).toBe("error");
        expect(builderRule!.fieldPaths).toEqual(
            expect.arrayContaining([
                "app.compliance.autoLoadDefaultSchema",
                "app.metadataSchema.autoLoadDefaultAssetSchema",
            ])
        );
        expect(builderRule!.message).toMatch(MESSAGE);
    });

    test.each(CASES)("agrees with getConfig(): $name", ({ mutate, violates }) => {
        // The builder evaluates the config.json as authored (its applyDerived() forces nothing), which
        // is exactly what the mocked readFileSync hands getConfig() above — so the same object is the
        // input on both sides.
        expect(builderRule!.appliesWhen(configFrom(mutate))).toBe(violates);
    });

    test("[control] the mirror can disagree", () => {
        // Without this, "agrees on every case" is satisfied by a predicate that returns false for
        // everything — which is exactly what an unported rule looks like.
        const verdicts = new Set(
            CASES.map(({ mutate }) => builderRule!.appliesWhen(configFrom(mutate)))
        );
        expect(verdicts).toContain(true);
        expect(verdicts).toContain(false);
    });
});
