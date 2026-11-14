/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The ingestAsset Lambda reads the asset uploads table, and does not write it.
 *
 * Before the complete stage of `POST /ingest-asset` creates a missing asset, the handler
 * (`backend/backend/handlers/assets/ingestAsset.py`, `verify_upload_can_complete`) reads the upload
 * record with `GetItem`, so a completion the upload step would refuse creates nothing. Without the
 * grant the read is denied and every such completion fails with "Error verifying upload.". The
 * record is written and deleted only by the upload Lambda the handler invokes, so the role holds no
 * write on the table. Checked in every shipped template.
 */

import { synthAllTemplates, lambdaByHandler, statementsOf } from "../support/templateSynth";

const HANDLER = "handlers.assets.ingestAsset.lambda_handler";
const WRITE_ACTIONS = /dynamodb:(PutItem|UpdateItem|DeleteItem|BatchWriteItem|\*)/;

const onUploadsTable = (st: any): boolean =>
    st.Effect === "Allow" && /AssetUploadsStorage/.test(JSON.stringify(st.Resource ?? ""));

describe("ingestAsset reads the asset uploads table", () => {
    const synths = synthAllTemplates();

    for (const [name, s] of Object.entries(synths)) {
        describe(name, () => {
            const statements = statementsOf(s, lambdaByHandler(s, HANDLER));

            test("the role grants dynamodb:GetItem on the table", () => {
                expect(
                    statements.some(
                        (st) =>
                            onUploadsTable(st) && /dynamodb:GetItem/.test(JSON.stringify(st.Action))
                    )
                ).toBe(true);
            });

            test("the role grants no write on the table", () => {
                expect(
                    statements
                        .filter(onUploadsTable)
                        .filter((st) => WRITE_ACTIONS.test(JSON.stringify(st.Action)))
                ).toEqual([]);
            });
        });
    }
});
