/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The GenAI 3D labeling and mesh/CAD metadata extraction pipelines were removed in favour of the
 * system GenAI metadata pipeline. Their identifiers survive in exactly the places that reject or
 * migrate them; anywhere else is a resurrection. Walks the source roots and asserts every hit is
 * on the allow-list.
 */

import * as fs from "fs";
import * as path from "path";

const REPO = path.join(__dirname, "..", "..", "..");
const ROOTS = [
    "infra",
    "backend",
    "backendPipelines",
    "web/src",
    "documentation/docusaurus-site/src",
];
const EXTENSIONS = new Set([".ts", ".tsx", ".js", ".py", ".json", ".md", ".yaml", ".yml"]);
const SKIP_DIRS = new Set(["node_modules", "cdk.out", "build", "dist", ".venv", "__pycache__"]);
const RETIRED = [
    "metadata3dLabeling",
    "Metadata3dLabeling",
    "useGenAiMetadata3dLabeling",
    "genai-metadata-3d-labeling",
    "metadata-3d-labeling",
    "meshCadMetadata",
    "MeshCadMetadata",
    "useConversionCadMeshMetadataExtraction",
    "metadata-extraction-cad-mesh",
];
/**
 * file (repo-relative, forward slashes) → substrings at least one of which every hit line carries.
 * `*` = any line of that file.
 */
const ALLOW: Record<string, string[]> = {
    // The rejectedPipelineKeys literals only.
    "infra/config/config.ts": [
        '"useGenAiMetadata3dLabeling",',
        '"useConversionCadMeshMetadataExtraction",',
    ],
    // The rejection arms.
    "infra/test/config/configValidationHardening.test.ts": ["*"],
    "infra/test/config/trackedConfigJson.test.ts": ["not.toHaveProperty"],
    // The ConfigBuilder's rejection rule.
    "documentation/docusaurus-site/src/components/ConfigBuilder/validation.ts": ["*"],
    // Historical ids of an earlier migration.
    "infra/deploymentDataMigration/v2.5_to_v2.6/upgrade/v2.5_to_v2.6_migration.py": [
        "genai-metadata-3d-labeling",
        "metadata-extraction-cad-mesh",
    ],
    // The migration that archives, deletes and reports on the retired ids.
    "infra/deploymentDataMigration/v2.6_to_v2.7/upgrade/v2.6_to_v2.7_migration.py": [
        "genai-metadata-3d-labeling",
        "metadata-extraction-cad-mesh",
    ],
    "infra/deploymentDataMigration/v2.6_to_v2.7/upgrade/v2.6_to_v2.7_migration_config.json": [
        "genai-metadata-3d-labeling",
        "metadata-extraction-cad-mesh",
    ],
    "infra/deploymentDataMigration/v2.6_to_v2.7/upgrade/v2.6_to_v2.7_migration_README.md": [
        "genai-metadata-3d-labeling",
        "metadata-extraction-cad-mesh",
        "useGenAiMetadata3dLabeling",
        "useConversionCadMeshMetadataExtraction",
    ],
    // Pins the upgrade guide's `### v2.6 to v2.7` section to the rejected configuration keys.
    "infra/test/platform/migrationTooling.test.ts": [
        "useGenAiMetadata3dLabeling",
        "useConversionCadMeshMetadataExtraction",
    ],
    "infra/test/platform/retiredPipelineIdentifiers.test.ts": ["*"],
};

function* walk(dir: string): Generator<string> {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        if (entry.isDirectory()) {
            if (!SKIP_DIRS.has(entry.name)) yield* walk(path.join(dir, entry.name));
        } else if (EXTENSIONS.has(path.extname(entry.name))) {
            yield path.join(dir, entry.name);
        }
    }
}

describe("retired pipeline identifiers", () => {
    const offenders: string[] = [];
    const allowed: string[] = [];
    for (const root of ROOTS) {
        for (const file of walk(path.join(REPO, root))) {
            const rel = path.relative(REPO, file).split(path.sep).join("/");
            const lines = fs.readFileSync(file, "utf8").split(/\r?\n/);
            lines.forEach((line, i) => {
                if (!RETIRED.some((id) => line.includes(id))) return;
                const rule = ALLOW[rel];
                const ok = rule && (rule.includes("*") || rule.some((s) => line.includes(s)));
                (ok ? allowed : offenders).push(`${rel}:${i + 1}: ${line.trim()}`);
            });
        }
    }

    test("appear only on the allow-list", () => {
        expect(offenders).toEqual([]);
    });

    test("[control] the allow-listed surfaces still exist", () => {
        // A silently deleted rejection surface would make the rule above pass over nothing.
        for (const file of Object.keys(ALLOW)) {
            expect({ file, present: allowed.some((hit) => hit.startsWith(`${file}:`)) }).toEqual({
                file,
                present: true,
            });
        }
    });

    test("[control] the walk covers every root and the identifier set is intact", () => {
        for (const root of ROOTS) {
            expect(fs.existsSync(path.join(REPO, root))).toBe(true);
        }
        expect(RETIRED).toHaveLength(9);
    });
});
