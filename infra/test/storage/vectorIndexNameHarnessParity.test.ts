/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Pins the vector index name the T1 tests expect to the one getConfig() derives.
 *
 * templateSynth.ts fills `vectorIndexName` itself because it never calls getConfig(), and the T1
 * storage tests derive the name each arm should carry from the arm's `vectorSearch` block through
 * test/support/vectorIndexName.ts. Both go through the `deriveVectorIndexName` export of config.ts;
 * this file proves that re-deriving from `app.vectorSearch` alone reproduces what getConfig()
 * assigns for the same template, so a getConfig() that started naming the index from anything else
 * fails here rather than leaving every T1 IndexName assertion comparing the harness with itself.
 * getConfig() is authoritative: when this file fails, the derivation inputs changed in config.ts and
 * test/support/vectorIndexName.ts must follow — never the reverse.
 *
 * The harness fill has to run AFTER the arm's mutator. The restricted templates ship with vector
 * search off, so the only way a govcloud or eusovereign synth ever carries the index is a mutator
 * that turns it on (the partition tests' vector-search-enabled hybrid arms do exactly that), and a
 * fill that ran before the mutator would name the index from the shipped block — or, with no fill at
 * all, hand the synth `IndexName: undefined`, which `toBe(vectorIndexNameFor(...))` alone cannot tell
 * from a correct name when both sides are undefined. `buildConfig()` is exported so the fill is
 * asserted here without a synth.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import { newTestApp } from "../support/testApp";
import { RESTRICTED_TEMPLATES, TemplateName, buildConfig } from "../support/templateSynth";
import { slugModelId, vectorIndexNameFor } from "../support/vectorIndexName";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

const TEMPLATES: Record<TemplateName, any> = {
    commercial: commercialTemplate,
    govcloud: govcloudTemplate,
    eusovereign: eusovereignTemplate,
};

/** The mutation the T1 ON arms apply to a template that ships with vector search off. */
const enableVectorSearch = (c: any) => {
    c.app.vectorSearch.enabled = true;
    if (!c.app.vectorSearch.embeddingModelId) {
        c.app.vectorSearch.embeddingModelId = "amazon.titan-embed-text-v2:0";
    }
};

/** Builds a config.json from the commercial template, applies `mutate`, and runs getConfig(). */
function resolve(mutate: (c: any) => void): Config.Config {
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
    return Config.getConfig(newTestApp());
}

afterEach(() => {
    // Restored to delegating rather than cleared, or a later synth in this process reads nothing.
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

describe("the T1 harness derives the vector index name getConfig() derives", () => {
    test("shipped commercial template", () => {
        const config = resolve(() => undefined);
        // Control: the commercial template ships with vector search on, so a name must exist.
        expect(config.app.vectorSearch.enabled).toBe(true);
        expect(config.vectorIndexName).toMatch(/^vec-/);
        expect(vectorIndexNameFor(config.app.vectorSearch)).toBe(config.vectorIndexName);
    });

    test("a different model id and dimension count", () => {
        const shipped = resolve(() => undefined).vectorIndexName;
        const config = resolve((c) => {
            c.app.vectorSearch.embeddingModelId = "cohere.embed-multilingual-v3";
            c.app.vectorSearch.embeddingDimensions = 512;
        });
        // Control: the mutation changed the name, so agreement below is not agreement on a constant.
        expect(config.vectorIndexName).not.toBe(shipped);
        expect(vectorIndexNameFor(config.app.vectorSearch)).toBe(config.vectorIndexName);
    });
});

describe("slug rule: lowercase, runs of [^a-z0-9] become one hyphen, ends trimmed", () => {
    test("collapses punctuation runs and trims the ends", () => {
        expect(slugModelId("amazon.titan-embed-text-v2:0")).toBe("amazon-titan-embed-text-v2-0");
        expect(slugModelId("..Cohere__Embed:v3:")).toBe("cohere-embed-v3");
    });

    test("index name shape", () => {
        expect(
            vectorIndexNameFor({
                embeddingModelId: "amazon.titan-embed-text-v2:0",
                embeddingDimensions: 1024,
            })
        ).toBe("vec-amazon-titan-embed-text-v2-0-1024");
        expect(vectorIndexNameFor(undefined)).toBeUndefined();
    });
});

describe("templateSynth.buildConfig() fills vectorIndexName after the mutator has run", () => {
    test.each(RESTRICTED_TEMPLATES)(
        "%s: a mutator enabling vector search yields a defined name from the mutated block",
        (name) => {
            // Control: the template ships with vector search off, so the mutator is what turns it on.
            expect(TEMPLATES[name].app.vectorSearch.enabled).toBe(false);
            const built = buildConfig(name, enableVectorSearch);
            expect(built.app.vectorSearch.enabled).toBe(true);
            expect(built.vectorIndexName).toMatch(/^vec-/);
            expect(built.vectorIndexName).toBe(vectorIndexNameFor(built.app.vectorSearch));
        }
    );

    test("the name follows the mutated model and width, not the shipped ones", () => {
        const shipped = buildConfig("commercial").vectorIndexName;
        const built = buildConfig("commercial", (c) => {
            c.app.vectorSearch.embeddingModelId = "cohere.embed-multilingual-v3";
            c.app.vectorSearch.embeddingDimensions = 512;
        });
        // A fill that ran before the mutator would still carry the shipped name here.
        expect(shipped).toMatch(/^vec-/);
        expect(built.vectorIndexName).toBe("vec-cohere-embed-multilingual-v3-512");
        expect(built.vectorIndexName).not.toBe(shipped);
    });
});
