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

import { synthAllTemplates, synthTemplate, Resource, SynthResult } from "../support/templateSynth";

const HANDLER = "handlers.assets.assetFiles.lambda_handler";

/** The one Lambda whose handler is `handler`; fails when there are none or several. */
function lambdaByHandler(s: SynthResult, handler: string): Resource {
    const found = s.where(
        "AWS::Lambda::Function",
        (r) => SynthResult.flatten(r.properties.Handler) === handler
    );
    expect(found).toHaveLength(1);
    return found[0];
}

/** Actions of one IAM statement, normalised to an array of strings. */
const actionsOf = (statement: any): string[] =>
    ([] as unknown[]).concat(statement.Action ?? []).map(String);

/**
 * Every IAM statement attached to the function's execution role: inline `AWS::IAM::Policy`, the
 * `AWS::IAM::ManagedPolicy` overflow, and role-level policies.
 */
function statementsOf(s: SynthResult, fn: Resource): any[] {
    const role = fn.properties.Role;
    expect(role?.["Fn::GetAtt"]?.[1]).toBe("Arn");
    const roleId = role["Fn::GetAtt"][0];
    const attached = (r: Resource) =>
        r.stack === fn.stack &&
        ((r.properties.Roles ?? []) as any[]).some((ref) => ref?.Ref === roleId);
    const out: any[] = [
        ...s.where("AWS::IAM::Policy", attached),
        ...s.where("AWS::IAM::ManagedPolicy", attached),
    ].flatMap((p) => (p.properties.PolicyDocument?.Statement ?? []) as any[]);
    const roleResource = s
        .ofType("AWS::IAM::Role")
        .find((r) => r.stack === fn.stack && r.logicalId === roleId);
    for (const p of roleResource?.properties.Policies ?? []) {
        out.push(...(p.PolicyDocument?.Statement ?? []));
    }
    return out;
}

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
