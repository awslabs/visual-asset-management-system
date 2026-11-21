/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The large-file queue consumer holds every DynamoDB action `common/uploadStatus.py` uses on the
 * asset uploads table, and nothing more there.
 *
 * The consumer (`backend/backend/handlers/assets/sqsUploadFileLarge.py`) records each queued file's
 * outcome and its completion audit claim on the upload record through `uploadStatus`. A redelivery
 * whose claim is already taken reads the record (`async_file_status`, a `GetItem`) to tell a tracked
 * file from an untracked one. With `UpdateItem` alone that read is denied, the message dead-letters,
 * and the file stays PENDING on the upload until the record expires.
 *
 * The needed actions are read from the helper module's source rather than listed here, so a helper
 * that starts using another table call fails this test until the grant follows it.
 */

import * as fs from "fs";
import * as path from "path";
import { synthTemplate, lambdaByHandler, statementsOf, actionsOf } from "../support/templateSynth";

const HANDLER = "handlers.assets.sqsUploadFileLarge.lambda_handler";
const UPLOAD_STATUS = path.join(__dirname, "../../../backend/backend/common/uploadStatus.py");

/** boto3 Table method -> IAM action, for the calls a table helper can make. */
const TABLE_CALLS: Record<string, string> = {
    get_item: "dynamodb:GetItem",
    update_item: "dynamodb:UpdateItem",
    put_item: "dynamodb:PutItem",
    delete_item: "dynamodb:DeleteItem",
    query: "dynamodb:Query",
    scan: "dynamodb:Scan",
};

/** The IAM actions the helper module's `table.<call>(` sites need. */
function actionsUploadStatusUses(): string[] {
    const source = fs.readFileSync(UPLOAD_STATUS, "utf8");
    const used = new Set<string>();
    for (const match of source.matchAll(/\btable\.(\w+)\(/g)) {
        const action = TABLE_CALLS[match[1]];
        if (action) used.add(action);
    }
    return [...used].sort();
}

const onUploadsTable = (st: any): boolean =>
    st.Effect === "Allow" && /AssetUploadsStorage/.test(JSON.stringify(st.Resource ?? ""));

describe("large-file queue consumer: upload table grant follows uploadStatus", () => {
    test("[control] the helper module's table calls are found", () => {
        // A scan that matched nothing would make the grant assertion below vacuous.
        const used = actionsUploadStatusUses();
        expect(used).toEqual(expect.arrayContaining(["dynamodb:GetItem", "dynamodb:UpdateItem"]));
    });

    test("commercial: the role holds exactly the actions uploadStatus uses, on the table ARN", () => {
        const s = synthTemplate("commercial");
        const statements = statementsOf(s, lambdaByHandler(s, HANDLER)).filter(onUploadsTable);
        expect(statements.length).toBeGreaterThan(0);
        const granted = [...new Set(statements.flatMap(actionsOf))].sort();
        expect(granted).toEqual(actionsUploadStatusUses());
        for (const st of statements) {
            expect(JSON.stringify(st.Resource)).not.toMatch(/\/index\//);
            expect(JSON.stringify(st.Resource)).not.toContain("*");
        }
    });
});
