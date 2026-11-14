/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The ingestAsset Lambda holds exactly `dynamodb:GetItem` on the asset uploads table.
 *
 * Before the complete stage of `POST /ingest-asset` creates a missing asset, the handler
 * (`backend/backend/handlers/assets/ingestAsset.py`, `verify_upload_can_complete`) reads the upload
 * record with `GetItem`. Without the grant the read is denied and every such completion fails with
 * "Error verifying upload.". The role holds nothing more on the table: no Scan, Query or batch read,
 * no write (the record is written and deleted only by the upload Lambda the handler invokes), and
 * nothing on the table's indexes. Checked in every shipped template and in a customer managed key
 * cell, where the deployment key's decrypt permission (`kms:Decrypt`) must also be on the role.
 */

import {
    synthAllTemplates,
    synthTemplate,
    lambdaByHandler,
    statementsOf,
    SynthResult,
} from "../support/templateSynth";

const HANDLER = "handlers.assets.ingestAsset.lambda_handler";

const onUploadsTable = (st: any): boolean =>
    st.Effect === "Allow" && /AssetUploadsStorage/.test(JSON.stringify(st.Resource ?? ""));

const actionsOf = (st: any): string[] => [].concat(st.Action ?? []);

function expectExactGetItem(s: SynthResult): void {
    const statements = statementsOf(s, lambdaByHandler(s, HANDLER));
    const grants = statements.filter(onUploadsTable);
    // Exactly one statement, one action, on the table ARN only.
    expect(grants.map(actionsOf)).toEqual([["dynamodb:GetItem"]]);
    expect(JSON.stringify(grants[0].Resource)).not.toMatch(/\/index\//);
    expect(JSON.stringify(grants[0].Resource)).not.toContain("*");
}

describe("ingestAsset reads the asset uploads table with GetItem only", () => {
    const synths = synthAllTemplates();

    for (const [name, s] of Object.entries(synths)) {
        test(`${name}: exactly dynamodb:GetItem on the table, no index, no write`, () => {
            expectExactGetItem(s);
        });
    }

    test("customer managed key cell: the same grant, and the role can decrypt with the deployment key", () => {
        const s = synthTemplate("commercial", {
            mutateKey: "ingest-uploads-grant-cmk-on",
            mutate: (c) => {
                c.app.useKmsCmkEncryption.enabled = true;
            },
        });
        expectExactGetItem(s);
        const statements = statementsOf(s, lambdaByHandler(s, HANDLER));
        expect(
            statements.some((st) => st.Effect === "Allow" && actionsOf(st).includes("kms:Decrypt"))
        ).toBe(true);
    });
});
