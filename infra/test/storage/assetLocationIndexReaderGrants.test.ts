/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every function that can query the asset table's location index (`AssetLocationGSI`) may do so (#1207 D5).
 *
 * The backend reads the index through `common/assetLocations.py` (`resolve_owner`, `find_conflicts`, the
 * nested-location lookup, the reindex partition load) and the helpers built on it. A handler that reaches one
 * of those reads needs `dynamodb:Query` on `<asset table>/index/AssetLocationGSI` (or `index/*`); without it
 * the call fails with AccessDeniedException once the index is ready, as every workflow launch did when
 * executeWorkflow started reading the nested locations. Which handlers reach the index is read from the
 * backend source by `backend/tests/common/locationIndexReaders.py` (a static call-graph reading), so a new
 * reader is covered without editing this file. The check runs on the commercial and GovCloud templates and on
 * a commercial template with the add-ons whose functions read the index turned on.
 */

import { execFileSync } from "child_process";
import * as path from "path";
import {
    Resource,
    SynthResult,
    TemplateName,
    actionsOf,
    resolveReference,
    statementsOf,
    synthTemplate,
} from "../support/templateSynth";

jest.setTimeout(900_000);

const REPO_ROOT = path.join(__dirname, "..", "..", "..");
const ANALYZER = path.join(REPO_ROOT, "backend", "tests", "common", "locationIndexReaders.py");
const INDEX_SUFFIXES = ["/index/AssetLocationGSI", "/index/*"];

let readerCache: Set<string> | undefined;

/**
 * Every handler (`module.function` of a `handlers.` module) whose code can query the location index, from
 * the backend reading; read once per run.
 */
function indexReaders(): Set<string> {
    if (!readerCache) {
        readerCache = new Set(
            JSON.parse(execFileSync("python3", [ANALYZER], { encoding: "utf8", input: "" }))
        );
    }
    return readerCache;
}

const addonsOn = (c: any) => {
    c.app.addons.usePhysnaSync.enabled = true;
    c.app.addons.usePhysnaSync.tenantId = "11111111-2222-3333-4444-555555555555";
    c.app.addons.useGarnetFramework.enabled = true;
    c.app.addons.useGarnetFramework.garnetApiEndpoint = "https://garnet.example.com";
    c.app.addons.useGarnetFramework.garnetApiToken = "placeholder";
    c.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl =
        "https://sqs.us-east-1.amazonaws.com/123456789012/garnet-ingest";
};

const CELLS: [string, () => SynthResult][] = [
    ["commercial", () => synthTemplate("commercial" as TemplateName)],
    ["govcloud", () => synthTemplate("govcloud" as TemplateName)],
    [
        "commercial with Physna and Garnet on",
        () =>
            synthTemplate("commercial", {
                mutate: addonsOn,
                mutateKey: "location-index-readers-addons",
            }),
    ],
];

const functionsOf = (s: SynthResult): Resource[] =>
    s.where("AWS::Lambda::Function", (r) => typeof r.properties.Handler === "string");

/** Whether one IAM resource value names the asset table's location index (or every index of it). */
function namesTheIndex(s: SynthResult, stack: string, resource: any): boolean {
    const join = resource?.["Fn::Join"];
    if (!join || join[0] !== "") return false;
    const parts = join[1] as any[];
    if (parts.length !== 2 || typeof parts[1] !== "string" || !INDEX_SUFFIXES.includes(parts[1])) {
        return false;
    }
    const table = resolveReference(s, stack, parts[0]);
    return table?.type === "AWS::DynamoDB::Table" && /^AssetStorageTable/.test(table.logicalId);
}

/** Whether the function's role may query the asset table's location index. */
function mayQueryTheIndex(s: SynthResult, fn: Resource): boolean {
    return statementsOf(s, fn).some(
        (st) =>
            st.Effect === "Allow" &&
            actionsOf(st).some((a) => a === "dynamodb:Query" || a === "dynamodb:*") &&
            [st.Resource].flat().some((r) => namesTheIndex(s, fn.stack, r))
    );
}

describe("functions that read the asset location index may query it", () => {
    const seen = new Set<string>();

    test("the backend reading finds the known readers (it is not vacuous)", () => {
        const all = [...indexReaders()];
        for (const handler of [
            "handlers.workflows.executeWorkflow.lambda_handler",
            "handlers.assets.assetFiles.lambda_handler",
            "handlers.assets.zipDownloadService.lambda_handler",
            "handlers.workflows.sfn.workflowTriggerDispatch.lambda_handler",
            "handlers.indexing.sqsBucketSync.lambda_handler_created",
        ]) {
            expect(all).toContain(handler);
        }
        // A handler that never touches asset locations is not one
        expect(all).not.toContain("handlers.auth.routes.lambda_handler");
    });

    test.each(CELLS)(
        "%s: every reader's role holds dynamodb:Query on AssetLocationGSI",
        (_, synth) => {
            const s = synth();
            const fns = functionsOf(s);
            const readers = indexReaders();
            const checked = fns.filter((fn) => readers.has(fn.properties.Handler));
            // Control: the reading matched synthesized functions, so an empty list of denials means each
            // reader holds the grant rather than that none was found.
            expect(checked.length).toBeGreaterThan(5);
            checked.forEach((fn) => seen.add(fn.properties.Handler));
            const missing = checked
                .filter((fn) => !mayQueryTheIndex(s, fn))
                .map((fn) => `${fn.properties.Handler} (${fn.stack}/${fn.logicalId})`);
            expect(missing).toEqual([]);
        }
    );

    test("every reader handler of the backend is a function of one of the synthesized cells", () => {
        const handlers = [...indexReaders()].filter((h) => /\.lambda_handler\w*$/.test(h));
        expect(handlers.length).toBeGreaterThan(5);
        expect(handlers.filter((h) => !seen.has(h))).toEqual([]);
    });
});
