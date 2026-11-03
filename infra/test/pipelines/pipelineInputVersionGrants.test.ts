/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every pipeline role that reads asset-bucket objects can read them at a version.
 *
 * A pipeline reads its input file at the S3 version the workflow recorded at launch (`inputVersionId`
 * on the container definition). That read is a GetObject or HeadObject with a VersionId, which Amazon
 * S3 authorizes as `s3:GetObjectVersion`, not `s3:GetObject`. A role granted only `s3:GetObject` on an
 * asset bucket passes every test that reads the current object and then fails with AccessDenied on the
 * first run that names a version.
 *
 * Lambda roles get their asset-bucket reads from `grantReadPermissionsToAllAssetBuckets` /
 * `grantReadWritePermissionsToAllAssetBuckets` (CDK `grantRead`, `s3:GetObject*`), which already covers
 * it. Container, job and pod roles are built from explicit statements over
 * `getS3AssetBucketRecords()`, so this test reads every such statement in the pipeline constructs and
 * requires the version action wherever `s3:GetObject` is granted. Durable (root CLAUDE.md Rule 13): a
 * construct added later is scanned too.
 */

import * as fs from "fs";
import * as path from "path";

const PIPELINES_DIR = path.join(__dirname, "..", "..", "lib", "nestedStacks", "pipelines");

function constructFiles(dir: string): string[] {
    return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return constructFiles(full);
        return entry.name.endsWith(".ts") && path.basename(dir) === "constructs" ? [full] : [];
    });
}

/** Resolve a bare identifier used as `actions: name` to the array literal it was declared with. */
function resolveActions(source: string, block: string): string {
    const named = /actions:\s*(?:\[\s*\.\.\.)?([A-Za-z_][A-Za-z0-9_]*)/.exec(block);
    if (!named) return block;
    const declaration = new RegExp(`const\\s+${named[1]}\\s*=\\s*\\[([^\\]]*)\\]`).exec(source);
    return declaration ? `${block}\n${declaration[1]}` : block;
}

/**
 * The text of each IAM statement built per asset-bucket record: from a `getS3AssetBucketRecords()` or
 * `assetBucketRecords.forEach` call up to the end of the first `resources:` list after it.
 */
function assetBucketStatements(source: string): string[] {
    const blocks: string[] = [];
    const starts = /getS3AssetBucketRecords\(\)\s*\.map\(|assetBucketRecords\.forEach\(/g;
    let match: RegExpExecArray | null;
    while ((match = starts.exec(source)) !== null) {
        const rest = source.slice(match.index);
        const resources = rest.indexOf("resources:");
        if (resources < 0) continue;
        const end = rest.indexOf("]", resources);
        blocks.push(rest.slice(0, end + 1));
    }
    return blocks;
}

describe("pipeline roles can read an asset-bucket object at a version", () => {
    const files = constructFiles(PIPELINES_DIR);

    test("[control] the scan finds the per-asset-bucket statements", () => {
        const scanned = files.filter((file) => assetBucketStatements(fs.readFileSync(file, "utf8")).length > 0);
        // Thumbnail, Potree, coordinate transform, metadata labeling, CAD STEP agent, splat, image to 3D,
        // the Cosmos family, GR00T, video SOP/BOM, RapidPipeline (ECS and EKS) and ModelOps at least.
        expect(scanned.length).toBeGreaterThanOrEqual(15);
    });

    test.each(files.map((file) => [path.relative(PIPELINES_DIR, file), file]))(
        "%s grants s3:GetObjectVersion wherever it grants s3:GetObject on an asset bucket",
        (_name, file) => {
            const source = fs.readFileSync(file as string, "utf8");
            for (const block of assetBucketStatements(source)) {
                const actions = resolveActions(source, block);
                if (/"s3:GetObject"/.test(actions)) {
                    expect(actions).toMatch(/"s3:GetObjectVersion"|"s3:GetObject\*"/);
                }
            }
        }
    );
});
