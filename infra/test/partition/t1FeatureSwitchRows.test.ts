/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The `VECTORSEARCH` feature switch reaches the web interface and the CLI through one DynamoDB row per
 * feature, written by an AwsCustomResource in the feature-switch nested stack. The row is what the
 * search page and `vamscli search nlp` gate on, so it must be emitted exactly when
 * `app.vectorSearch.enabled` is true — in the shipped commercial template, in no restricted template,
 * and in a GovCloud deployment that turns the feature on.
 */

import { expectAbsent, SynthResult, synthTemplate, TemplateName } from "../support/templateSynth";

jest.setTimeout(600_000);

/** Feature names written by the feature-switch custom resources, read out of their Create payloads. */
function featureRows(synth: SynthResult): string[] {
    return synth
        .ofType("Custom::AWS")
        .map((r) => SynthResult.flatten(r.properties.Create))
        .flatMap((create) => {
            const match = /"featureName":\{"S":"([A-Z0-9_]+)"\}/.exec(create);
            return match ? [match[1]] : [];
        });
}

describe("the VECTORSEARCH feature row", () => {
    test("is emitted by the shipped commercial template", () => {
        expect(featureRows(synthTemplate("commercial"))).toContain("VECTORSEARCH");
    });

    test.each<TemplateName>(["govcloud", "eusovereign"])(
        "%s: is absent, and the template still emits feature rows",
        (name) => {
            const rows = featureRows(synthTemplate(name));
            expectAbsent(
                "VECTORSEARCH feature row",
                rows.filter((r) => r === "VECTORSEARCH"),
                { description: `${name} feature rows (GOVCLOUD among them)`, count: rows.length }
            );
            // The positive control by name: both restricted templates set app.govCloud.enabled.
            expect(rows).toContain("GOVCLOUD");
        }
    );

    test("commercial with vector search switched off: absent, while NOOPENSEARCH-style rows remain", () => {
        const rows = featureRows(
            synthTemplate("commercial", {
                mutate: (c: any) => {
                    c.app.vectorSearch.enabled = false;
                },
                mutateKey: "feature-vectorsearch-off",
            })
        );
        expectAbsent(
            "VECTORSEARCH feature row",
            rows.filter((r) => r === "VECTORSEARCH"),
            { description: "commercial feature rows", count: rows.length }
        );
        expect(rows).toContain("AUTHPROVIDER_COGNITO");
    });

    test("govcloud with vector search switched on: present (the switch follows the flag, not the partition)", () => {
        const rows = featureRows(
            synthTemplate("govcloud", {
                mutate: (c: any) => {
                    c.app.vectorSearch.enabled = true;
                    c.app.pipelines.useSystemGenAiMetadata.enabled = true;
                },
                mutateKey: "feature-vectorsearch-govcloud",
            })
        );
        expect(rows).toContain("VECTORSEARCH");
        expect(rows).toContain("GOVCLOUD");
    });

    test("the row is written once (the nested stack deduplicates the feature list)", () => {
        const rows = featureRows(synthTemplate("commercial"));
        expect(rows.filter((r) => r === "VECTORSEARCH")).toHaveLength(1);
    });
});
