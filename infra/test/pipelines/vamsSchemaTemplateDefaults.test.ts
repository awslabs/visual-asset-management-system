/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A multi-template `requireTemplate` bundle either marks a default, or is a recorded exemption.
 *
 * Execute auto-selects a pipeline's default template. With several templates and none marked, a run
 * that names no `templateId` is REJECTED — so the workflow is not runnable through a zero-argument
 * execute. Verified live on a deployment: `conversion-3d-basic`, `rapid-pipeline` and
 * `3dRecon-splat-toolbox` each launched only once the id was named explicitly.
 *
 * Owner question 82 settled that this is legitimate for MUTUALLY EXCLUSIVE templates — a target format
 * has no defensible default, and choosing one for the caller would silently emit the wrong artifact —
 * and asked for the exemption to be recorded rather than assumed. So the rule is not "every bundle
 * marks a default"; it is "every bundle is one shape or the other, and the second is listed here".
 *
 * That is what makes this durable rather than a pin: a bundle that grows a second template without
 * either marking a default or being added below is a bundle whose runnability changed by accident, and
 * it fails here instead of at execute time.
 */

import * as fs from "fs";
import * as path from "path";

const PIPELINES_DIR = path.join(__dirname, "..", "..", "..", "backendPipelines");

/**
 * Bundles that deliberately ship several templates with NO default, because the templates are mutually
 * exclusive outputs. Each entry records WHY, so the exemption can be re-judged rather than inherited.
 */
const NO_DEFAULT_BY_DESIGN: Record<string, string> = {
    "conversion/3dBasic":
        "convert-to-glb / -gltf / -obj / -stl are alternative target formats; no default target is defensible",
    "multi/rapidPipeline": "rapid-pipeline-to-glb / -to-gltf are alternative target formats",
    "3dRecon/splatToolbox":
        "splat-objects vs splat-environments-360 are different capture geometries, not variations of one",
    "multi/modelOps":
        "model-ops-to-glb / -to-gltf / -to-usdz are alternative target formats (outputType .glb/.gltf/.usdz)",
};

interface Bundle {
    /** Path relative to backendPipelines, e.g. "conversion/3dBasic". */
    owner: string;
    dir: string;
    pipeline: any;
    templates: { file: string; body: any }[];
}

/** Every vamsSchema bundle, at any nesting depth. */
function bundles(): Bundle[] {
    const out: Bundle[] = [];
    const walk = (dir: string) => {
        let entries: fs.Dirent[];
        try {
            entries = fs.readdirSync(dir, { withFileTypes: true });
        } catch {
            return;
        }
        for (const e of entries) {
            if (!e.isDirectory()) continue;
            if (["node_modules", "__pycache__", ".pytest_cache", "src"].includes(e.name)) continue;
            const full = path.join(dir, e.name);
            const pipelineJson = path.join(full, "pipeline.json");
            if (fs.existsSync(pipelineJson)) {
                const templatesDir = path.join(full, "templates");
                const templates: { file: string; body: any }[] = [];
                if (fs.existsSync(templatesDir)) {
                    for (const f of fs.readdirSync(templatesDir)) {
                        if (!f.endsWith(".json")) continue;
                        templates.push({
                            file: f,
                            body: JSON.parse(fs.readFileSync(path.join(templatesDir, f), "utf-8")),
                        });
                    }
                }
                // The owner is the pipeline directory: two levels under backendPipelines, except the
                // Isaac Lab bundles which nest a further level (vamsSchema/training, /evaluation).
                const rel = path.relative(PIPELINES_DIR, full).split(path.sep);
                const idx = rel.indexOf("vamsSchema");
                const owner = (idx > 0 ? rel.slice(0, idx) : rel.slice(0, 2)).join("/");
                out.push({
                    owner,
                    dir: full,
                    pipeline: JSON.parse(fs.readFileSync(pipelineJson, "utf-8")),
                    templates,
                });
            }
            walk(full);
        }
    };
    walk(PIPELINES_DIR);
    return out;
}

const ALL = bundles();

describe("vamsSchema template defaults", () => {
    it("finds the bundles at all", () => {
        // Control. A walk that returned nothing would make every rule below pass vacuously — and the
        // nesting is exactly where a glob-based version of this went wrong before, missing the Isaac Lab
        // bundles under vamsSchema/{training,evaluation}/.
        expect(ALL.length).toBeGreaterThanOrEqual(10);
        const withTemplates = ALL.filter((b) => b.templates.length > 0);
        expect(withTemplates.length).toBeGreaterThanOrEqual(5);
    });

    it("reaches the more deeply nested Isaac Lab bundles", () => {
        // Named specifically: they are the ones a `vamsSchema/templates/*.json` glob silently skips.
        const isaac = ALL.filter((b) => b.owner.includes("isaacLabTraining"));
        expect(isaac.length).toBeGreaterThanOrEqual(2);
    });

    it("every multi-template requireTemplate bundle marks a default or is a recorded exemption", () => {
        const offenders: string[] = [];
        for (const b of ALL) {
            const requires = b.pipeline?.systemConfig?.requireTemplate === true;
            if (!requires || b.templates.length < 2) continue;
            const hasDefault = b.templates.some((t) => t.body?.isDefault === true);
            if (hasDefault) continue;
            if (NO_DEFAULT_BY_DESIGN[b.owner]) continue;
            offenders.push(
                `${b.owner}: ${b.templates.length} templates, requireTemplate=true, none isDefault`
            );
        }
        expect(offenders).toEqual([]);
    });

    it("a bundle may not mark more than one default", () => {
        // The other way the choice becomes ambiguous, and cheap to state alongside.
        const offenders: string[] = [];
        for (const b of ALL) {
            const defaults = b.templates.filter((t) => t.body?.isDefault === true);
            if (defaults.length > 1) {
                offenders.push(`${b.owner}: ${defaults.map((d) => d.file).join(", ")}`);
            }
        }
        expect(offenders).toEqual([]);
    });

    it("every recorded exemption still applies to a real, still-defaultless bundle", () => {
        // Keeps the exemption list from rotting into a permanent allowance. If someone later marks a
        // default on one of these, or renames the directory, the entry must go — otherwise the list
        // silently exempts a bundle that no longer needs it, which is how this check would stop
        // catching the case it exists for.
        const stale: string[] = [];
        for (const owner of Object.keys(NO_DEFAULT_BY_DESIGN)) {
            const matches = ALL.filter((b) => b.owner === owner);
            if (matches.length === 0) {
                stale.push(`${owner}: no such bundle (renamed or removed?)`);
                continue;
            }
            for (const b of matches) {
                if (b.templates.some((t) => t.body?.isDefault === true)) {
                    stale.push(`${owner}: now marks a default, so the exemption is unnecessary`);
                }
                if (b.templates.length < 2) {
                    stale.push(`${owner}: has ${b.templates.length} template(s), so no ambiguity`);
                }
            }
        }
        expect(stale).toEqual([]);
    });

    /**
     * No bundle tag is required-without-default.
     *
     * This rule was settled as part of `S4-PIPELINES-065` and recorded only in the fix registry's
     * notes — nothing enforced it. It was then violated during the fix wave: `CHECKPOINT_PATH` and
     * `CHECKPOINT_FOLDER` were marked `required: true` with no default, on the reasoning that
     * evaluating with no checkpoint is meaningless. That reasoning was wrong — both pipelines resolve a
     * blank checkpoint by discovery, and both templates' own `inputInstructions` invite the blank value
     * — and nothing in the suite caught it, because the rule lived in prose.
     *
     * Two independent reasons the shape is wrong, either sufficient:
     *
     *  - **A required tag with no default cannot be satisfied by a zero-argument execute.** Every
     *    caller — a trigger, a script, the execute wizard — must name it, so a template that grows one
     *    silently stops being runnable the automatic way.
     *  - **`validate_tag_schema` already refuses the mirror case** (an optional integer/number/boolean
     *    with no default, "no blank form"), so the bundle format is designed around every tag having a
     *    usable value without the caller supplying one.
     */
    it("no template tag is required with no default", () => {
        const offenders: string[] = [];
        let tagsSeen = 0;
        for (const b of ALL) {
            for (const t of b.templates) {
                for (const tag of (t.body?.tagSchema ?? []) as any[]) {
                    tagsSeen += 1;
                    const hasDefault =
                        tag?.default !== undefined && tag?.default !== null && tag?.default !== "";
                    if (tag?.required === true && !hasDefault) {
                        offenders.push(
                            `${b.owner}/${t.file}: ${
                                tag?.tagKey
                            } is required with default=${JSON.stringify(tag?.default)}`
                        );
                    }
                }
            }
        }
        // Control: an empty tag set would satisfy the rule while checking nothing, and the bundles do
        // carry tag schemas — the Isaac Lab and gr00t ones alone declare ten.
        expect(tagsSeen).toBeGreaterThanOrEqual(10);
        expect(offenders).toEqual([]);
    });
});
