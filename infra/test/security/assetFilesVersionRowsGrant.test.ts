/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The assetFiles Lambda can delete the version snapshot rows of the files it deletes.
 *
 * A file delete (`DELETE /database/{databaseId}/assets/{assetId}/deleteFile`) removes the deleted
 * files' rows from the asset file versions table with a `batch_writer` delete
 * (`backend/backend/handlers/assets/assetFiles.py`, `delete_file_version_records`), which sends
 * `dynamodb:BatchWriteItem`. With read access only, the write is denied and the rows stay, so an
 * asset version keeps listing a file whose Amazon S3 versions are gone. The role holds that one write
 * action on the table and no other (`PutItem`, `UpdateItem`, `DeleteItem`). Checked in every shipped
 * template and in a customer managed key cell, where the deployment key's encrypt permission must
 * also be on the role.
 */

import {
    synthAllTemplates,
    synthTemplate,
    lambdaByHandler,
    statementsOf,
    actionsOf,
    SynthResult,
} from "../support/templateSynth";

const HANDLER = "handlers.assets.assetFiles.lambda_handler";

const onFileVersionsTable = (st: any): boolean =>
    st.Effect === "Allow" &&
    /AssetFileVersionsStorageTableV2/.test(JSON.stringify(st.Resource ?? ""));

function tableActions(s: SynthResult): string[] {
    const statements = statementsOf(s, lambdaByHandler(s, HANDLER)).filter(onFileVersionsTable);
    return statements.flatMap(actionsOf);
}

function expectVersionRowGrant(s: SynthResult): void {
    const actions = tableActions(s);
    // The delete's batch write, and the reads the routes already make.
    expect(actions).toContain("dynamodb:BatchWriteItem");
    expect(actions).toContain("dynamodb:Query");
    // No other write on this table.
    for (const write of ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]) {
        expect(actions).not.toContain(write);
    }
}

describe("assetFiles can delete rows of the asset file versions table, and write nothing else to it", () => {
    const synths = synthAllTemplates();

    for (const [name, s] of Object.entries(synths)) {
        test(`${name}: dynamodb:BatchWriteItem and the reads, no other write`, () => {
            expectVersionRowGrant(s);
        });
    }

    test("the write is granted on the asset file versions table ARN alone: no index, no wildcard, not V1", () => {
        const s = synths.commercial;
        const statements = statementsOf(s, lambdaByHandler(s, HANDLER));
        const batchWrites = statements.filter(
            (st) => st.Effect === "Allow" && actionsOf(st).includes("dynamodb:BatchWriteItem")
        );
        const onTable = batchWrites.filter(onFileVersionsTable);
        expect(onTable.map(actionsOf)).toEqual([["dynamodb:BatchWriteItem"]]);
        const resource = JSON.stringify(onTable[0].Resource);
        expect(resource).not.toMatch(/\/index\//);
        expect(resource).not.toContain("*");
        const onV1 = batchWrites.filter((st) =>
            /AssetFileVersionsStorageTable(?!V2)/.test(JSON.stringify(st.Resource ?? ""))
        );
        expect(onV1).toEqual([]);
    });

    test("customer managed key cell: the same grant, and the role can encrypt with the deployment key", () => {
        const s = synthTemplate("commercial", {
            mutateKey: "asset-files-version-rows-grant-cmk-on",
            mutate: (c) => {
                c.app.useKmsCmkEncryption.enabled = true;
            },
        });
        expectVersionRowGrant(s);
        const statements = statementsOf(s, lambdaByHandler(s, HANDLER));
        expect(
            statements.some(
                (st) =>
                    st.Effect === "Allow" &&
                    actionsOf(st).includes("kms:Encrypt") &&
                    actionsOf(st).includes("kms:Decrypt")
            )
        ).toBe(true);
    });
});
