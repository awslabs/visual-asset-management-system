/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Documentation contract for vector search, system pipelines, and the consolidated GenAI metadata
 * pipeline. Each block pairs a page with the source of truth it restates, so a later code change fails
 * here instead of leaving a page quietly wrong:
 *
 * - the sidebar, page set, and relative links resolve (Docusaurus reports this only at build time);
 * - every feature-flag table lists every `VAMS_APP_FEATURES` member;
 * - the spelled-out built-in pipeline count equals the table's row count;
 * - every config flag the pipeline table names exists in `config.ts`;
 * - every `ext_*` / `genai_*` literal the pipeline source writes is a row in the pipeline page's field
 *   tables and an entry in its importable schema JSON;
 * - the changelog, the revision history, and `package.json` agree on the version.
 *
 * Every string assertion is a substring match, which an empty or renamed file satisfies, so each block
 * opens with a control that reads the file and pins a heading.
 */

import * as fs from "fs";
import * as path from "path";

const REPO = path.join(__dirname, "..", "..", "..");
const SITE = path.join(REPO, "documentation", "docusaurus-site");
const DOCS = path.join(SITE, "docs");

const readDoc = (rel: string): string => fs.readFileSync(path.join(DOCS, rel), "utf8");
const readRepo = (rel: string): string => fs.readFileSync(path.join(REPO, rel), "utf8");
const docExists = (rel: string): boolean => fs.existsSync(path.join(DOCS, rel));

/**
 * The retired pipelines' identifiers, assembled from parts: `retiredPipelineIdentifiers.test.ts` forbids their
 * literal spelling anywhere under infra/, this file included, so the negative assertions below build them.
 */
const RETIRED_LABELING_STEM = ["Metadata", "3d", "Labeling"].join("");
const RETIRED_LABELING_KEY = "useGenAi" + RETIRED_LABELING_STEM;
const RETIRED_CAD_MESH_KEY = ["useConversion", "CadMesh", "MetadataExtraction"].join("");

/** One Markdown section, from its heading to the next heading of the same or a higher level. */
function section(text: string, heading: string): string {
    const start = text.indexOf(heading);
    if (start < 0) return "";
    const level = (heading.match(/^#+/) ?? ["##"])[0].length;
    const lines = text.slice(start + heading.length).split("\n");
    const out: string[] = [];
    for (const line of lines) {
        const m = line.match(/^(#+)\s/);
        if (m && m[1].length <= level) break;
        out.push(line);
    }
    return out.join("\n");
}

/** Every .md/.mdx page under docs/, as a posix path relative to docs/. */
function walkDocs(dir: string = DOCS): string[] {
    const out: string[] = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) out.push(...walkDocs(full));
        else if (/\.mdx?$/.test(entry.name)) {
            out.push(path.relative(DOCS, full).split(path.sep).join("/"));
        }
    }
    return out.sort();
}

/** Doc ids named in sidebars.ts — every quoted string containing a slash. */
function sidebarDocIds(): string[] {
    const src = fs.readFileSync(path.join(SITE, "sidebars.ts"), "utf8");
    return [...src.matchAll(/"([a-z0-9-]+(?:\/[a-z0-9-]+)+)"/g)].map((m) => m[1]);
}

/** Body rows of the first Markdown table in `text` (header and separator dropped). */
function tableRows(text: string): string[] {
    const rows = text.split("\n").filter((l) => /^\|/.test(l));
    return rows.filter((l) => !/^\|\s*:?-{3,}/.test(l)).slice(1);
}

const ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
];
const TENS = ["", "", "twenty", "thirty", "forty", "fifty"];

/** The English word for 0..59, hyphenated above nineteen ("twenty-three"). */
function numberWord(n: number): string {
    if (n < 20) return ONES[n];
    const rest = n % 10;
    return rest === 0 ? TENS[Math.floor(n / 10)] : `${TENS[Math.floor(n / 10)]}-${ONES[rest]}`;
}

/** The string values of the VAMS_APP_FEATURES enum members, parsed from the CDK source. */
function featureEnumMembers(): string[] {
    const src = readRepo(path.join("infra", "common", "vamsAppFeatures.ts"));
    const body = src.match(/export enum VAMS_APP_FEATURES\s*\{([\s\S]*?)\}/);
    if (!body) throw new Error("VAMS_APP_FEATURES enum not found");
    return [...body[1].matchAll(/=\s*"([A-Z0-9_]+)"/g)].map((m) => m[1]);
}

/** The 63 promoted keys the pipeline page documents (the unprefixed `location` is asserted separately). */
const REGISTRY_EXT_KEYS = [
    "ext_dimensions",
    "ext_bounds_min",
    "ext_bounds_max",
    "ext_units",
    "ext_size_category",
    "ext_crs",
    "ext_ifc_schema",
    "ext_project_name",
    "ext_color_mode",
    "ext_camera",
    "ext_resolution",
    "ext_video_codec",
    "ext_audio_codec",
    "ext_title",
    "ext_artist",
    "ext_album",
    "ext_author",
    "ext_language",
    "ext_encoding",
    "ext_columns",
    "ext_geometry_types",
    "ext_extent_max",
    "ext_volume",
    "ext_surface_area",
    "ext_vertex_count",
    "ext_face_count",
    "ext_triangle_count",
    "ext_mesh_count",
    "ext_material_count",
    "ext_texture_count",
    "ext_object_count",
    "ext_solid_count",
    "ext_edge_count",
    "ext_assembly_count",
    "ext_point_count",
    "ext_storey_count",
    "ext_element_count",
    "ext_geometric_error",
    "ext_tile_count",
    "ext_width",
    "ext_height",
    "ext_duration_seconds",
    "ext_frame_rate",
    "ext_bitrate_kbps",
    "ext_channels",
    "ext_sample_rate",
    "ext_year",
    "ext_page_count",
    "ext_line_count",
    "ext_word_count",
    "ext_row_count",
    "ext_column_count",
    "ext_feature_count",
    "ext_watertight",
    "ext_has_textures",
    "ext_has_uv",
    "ext_has_vertex_colors",
    "ext_has_animation",
    "ext_has_armature",
    "ext_has_color",
    "ext_has_text",
    "ext_captured_at",
    "ext_created_at",
];

/** The GenAI keys: 20 file-level (including the three segment counts) plus the two asset-level keys. */
const REGISTRY_GENAI_KEYS = [
    "genai_title",
    "genai_description",
    "genai_keywords",
    "genai_category",
    "genai_subcategory",
    "genai_style",
    "genai_materials",
    "genai_colors",
    "genai_primary_color",
    "genai_objects",
    "genai_complexity",
    "genai_orientation",
    "genai_size_estimate",
    "genai_text_summary",
    "genai_model",
    "genai_generated_at",
    "genai_source_modalities",
    "genai_segment_count",
    "genai_segment_interval_seconds",
    "genai_content_chunk_count",
    "genai_asset_keywords",
    "genai_asset_categories",
];

/** Every `"ext_…"` / `"genai_…"` string literal in a Python source text (a bare `"ext_"` prefix does not match). */
function pipelineOwnedKeys(pySource: string): string[] {
    return [
        ...new Set([...pySource.matchAll(/"((?:ext|genai)_[a-z0-9_]+)"/g)].map((m) => m[1])),
    ].sort();
}

/** key → type from the field tables: rows shaped `| \`ext_x\` | \`number\` | …` (prose mentions are not rows). */
function documentedFieldTypes(fieldsWritten: string): Record<string, string> {
    const out: Record<string, string> = {};
    for (const m of fieldsWritten.matchAll(
        /^\|\s*`((?:ext|genai)_[a-z0-9_]+|location)`\s*\|\s*`([a-z_]+)`\s*\|/gm
    )) {
        out[m[1]] = m[2];
    }
    return out;
}

/** key → type from every ```json block in the section that is a field-definition array. */
function schemaFieldTypes(fieldsWritten: string): Record<string, string> {
    const out: Record<string, string> = {};
    for (const block of fieldsWritten.matchAll(/```json\n([\s\S]*?)\n```/g)) {
        const parsed = JSON.parse(block[1]) as unknown;
        if (!Array.isArray(parsed)) continue;
        for (const field of parsed as Array<{
            metadataFieldKeyName: string;
            metadataFieldValueType: string;
        }>) {
            expect(out).not.toHaveProperty(field.metadataFieldKeyName); // duplicate names are rejected by the API
            out[field.metadataFieldKeyName] = field.metadataFieldValueType;
        }
    }
    return out;
}

describe("pipelines/system-pipelines.md — the SYSTEM category page", () => {
    test("the page exists and carries its headings", () => {
        expect(docExists("pipelines/system-pipelines.md")).toBe(true);
        const text = readDoc("pipelines/system-pipelines.md");
        expect(text).toContain("# System pipelines");
        expect(text).toContain("## What a system record allows");
        expect(text).toContain("## Deployments own system records");
        expect(text).toContain("## Shipped system pipelines");
    });

    test("the allowed-operations table covers every operation the backend guards rule on", () => {
        const table = section(
            readDoc("pipelines/system-pipelines.md"),
            "## What a system record allows"
        );
        for (const op of [
            "Pipeline update",
            "Pipeline archive or restore",
            "Template add or delete",
            "Template update",
            "Workflow update",
            "Workflow archive",
            "Trigger update",
            "Trigger add or delete",
            "Execute, read, list",
        ]) {
            expect(table).toContain(op);
        }
        expect(table).toContain("`configBody`");
        expect(table).toContain("`tagSchema`");
        expect(table).toContain("`webFormJson`");
    });

    test("the refusal messages quoted on the page are the backend's literals", () => {
        const guards = readRepo(
            path.join("backend", "backend", "common", "workflows", "systemRecords.py")
        );
        const table = section(
            readDoc("pipelines/system-pipelines.md"),
            "## What a system record allows"
        );
        for (const literal of [
            'System pipelines are read-only; only "enabled" may be changed.',
            'System workflows are read-only; only "enabled" may be changed.',
            "Templates of system pipelines cannot be added or deleted.",
            "Triggers of system workflows cannot be added or deleted.",
        ]) {
            expect(guards).toContain(literal);
            expect(table).toContain(literal);
        }
    });

    test("the page names the two shipped system pipelines by id and category, as their bundles declare them", () => {
        const shipped = section(
            readDoc("pipelines/system-pipelines.md"),
            "## Shipped system pipelines"
        );
        expect(shipped).toContain("`system-genai-metadata`");
        expect(shipped).toContain("`SYSTEM - GenAI`");
        expect(shipped).toContain("`preview-3d-thumbnail`");
        expect(shipped).toContain("`SYSTEM - Preview`");
        const genAi = JSON.parse(
            readRepo(
                path.join(
                    "backendPipelines",
                    "system",
                    "genAiMetadata",
                    "vamsSchema",
                    "pipeline.json"
                )
            )
        ) as { category: string; isSystem: boolean };
        const thumb = JSON.parse(
            readRepo(
                path.join(
                    "backendPipelines",
                    "preview",
                    "3dThumbnail",
                    "vamsSchema",
                    "pipeline.json"
                )
            )
        ) as { category: string; isSystem: boolean };
        expect(genAi.isSystem).toBe(true);
        expect(thumb.isSystem).toBe(true);
        expect(shipped).toContain("`" + genAi.category + "`");
        expect(shipped).toContain("`" + thumb.category + "`");
    });

    test("the page states the toggle paths and the durable off switch", () => {
        const text = readDoc("pipelines/system-pipelines.md");
        expect(text).toContain("workflow trigger set");
        expect(text).toContain("--disable");
        expect(text).toContain("`autoRegisterAutoTriggerOnFileUpload`");
        expect(text).toContain("`vectorSearch.enabled`");
    });
});

describe("pipelines/system-genai-metadata.md — the consolidated pipeline page", () => {
    test("the page exists and carries its headings", () => {
        expect(docExists("pipelines/system-genai-metadata.md")).toBe(true);
        const text = readDoc("pipelines/system-genai-metadata.md");
        for (const h of [
            "# SYSTEM - GenAI Metadata Generation Pipeline",
            "## Supported files and how each is analyzed",
            "## Outputs",
            "## Fields written",
            "## Embeddings and the `vector.embedding.ready` event",
            "## Prerequisites",
            "### Amazon Bedrock guardrail",
            "## Configuration",
            "## Template",
            "## Failure behaviour",
            "## Third-party components",
        ]) {
            expect(text).toContain(h);
        }
    });

    test("the file-class matrix names every fileClass the classifier emits", () => {
        const classifier = readRepo(
            path.join("backendPipelines", "system", "genAiMetadata", "lambda", "fileClassifier.py")
        );
        const matrix = section(
            readDoc("pipelines/system-genai-metadata.md"),
            "## Supported files and how each is analyzed"
        );
        const classes = [
            "image",
            "video",
            "audio",
            "document",
            "text",
            "data",
            "tiles3d",
            "mesh",
            "usd",
            "cad",
            "pointcloud",
            "splat",
            "ifc",
            "other",
        ];
        for (const cls of classes) {
            expect(classifier).toContain('"' + cls + '"'); // control: the class is one the source emits
            expect(matrix).toContain("`" + cls + "`");
        }
        expect(matrix).toContain("`sys_geo`");
        // The office formats the classifier admits for their text are marked on the matrix.
        expect(classifier).toMatch(
            /ADDITIONAL_EXTENSIONS\s*=\s*\(\s*"\.docx",\s*"\.xlsx",\s*"\.pptx"\s*\)/
        );
        for (const s of ["‡", "`.docx`", "`.pptx`", "`.xlsx`", "`ADDITIONAL_EXTENSIONS`"])
            expect(matrix).toContain(s);
    });

    test("the outputs section names the two layers, the asset file, and the results files", () => {
        const outputs = section(readDoc("pipelines/system-genai-metadata.md"), "## Outputs");
        for (const s of [
            "`<relativePath>.attribute.json`",
            "`<relativePath>.metadata.json`",
            "`asset.metadata.json`",
            "`ext_*`",
            "`genai_*`",
            "`location`",
            "`replace_all`",
            "`analysis-summary.json`",
            "`execution.status.json`",
            "`segments/<segmentKey>.json`",
        ]) {
            expect(outputs).toContain(s);
        }
    });

    test("the fields-written section carries its three tables and its subsections", () => {
        const fw = section(readDoc("pipelines/system-genai-metadata.md"), "## Fields written");
        for (const s of [
            "### Attribute groups",
            "### Promoted metadata",
            "### Generated metadata",
            "### Location",
            "### Editing the classification vocabulary",
            ":::warning[Databases that restrict metadata to schemas]",
            "`WRITE_EXTRACTED_METADATA`",
            "`EXTRACT_GEO_LOCATION`",
            "`classificationVocabulary`",
            "`allowUnlisted`",
            "--config-body-file",
            "restrictMetadataOutsideSchemas",
            "is not defined in the metadata schema",
            "vamscli metadata-schema create",
            "vamscli pipeline template update",
        ]) {
            expect(fw).toContain(s);
        }
    });

    test("the schema-restriction warning quotes the metadata service's refusal text", () => {
        const validation = readRepo(
            path.join("backend", "backend", "common", "metadataSchemaValidation.py")
        );
        expect(validation).toContain("is not defined in the metadata schema");
        expect(validation).toContain(
            "Only schema-defined fields are allowed when restrictMetadataOutsideSchemas is enabled"
        );
        const fw = section(readDoc("pipelines/system-genai-metadata.md"), "## Fields written");
        expect(fw).toContain(
            "is not defined in the metadata schema. Only schema-defined fields are allowed when restrictMetadataOutsideSchemas is enabled."
        );
    });

    test("the fields-written tables list every sys_* group, every documented ext_*/genai_* key, and location", () => {
        const fw = section(readDoc("pipelines/system-genai-metadata.md"), "## Fields written");
        for (const group of [
            "sys_file",
            "sys_image",
            "sys_media",
            "sys_document",
            "sys_text",
            "sys_data",
            "sys_geo",
            "sys_tiles3d",
            "sys_geometry",
            "sys_statistics",
            "sys_format",
            "sys_visual",
            "sys_scene",
            "sys_cad",
            "sys_pointcloud",
            "sys_ifc",
        ]) {
            expect(section(fw, "### Attribute groups")).toContain("| `" + group + "`");
        }
        const documented = documentedFieldTypes(fw);
        for (const key of REGISTRY_EXT_KEYS) expect(documented).toHaveProperty(key);
        for (const key of REGISTRY_GENAI_KEYS) expect(documented).toHaveProperty(key);
        expect(documented.location).toBe("geojson");
        expect(documented.ext_dimensions).toBe("xyz");
        expect(documented.genai_description).toBe("multiline_string");
        expect(documented.genai_generated_at).toBe("date");
        // Control: the row extractor reads table rows only, so a key that appears nowhere is not "documented".
        expect(documented).not.toHaveProperty("ext_made_up_control");
    });

    test("the schema JSON blocks parse, use accepted value types, and list exactly the keys the tables document", () => {
        const fw = section(readDoc("pipelines/system-genai-metadata.md"), "## Fields written");
        const schema = schemaFieldTypes(fw);
        expect(Object.keys(schema).length).toBeGreaterThan(60); // control: the blocks were found and parsed
        for (const type of Object.values(schema)) {
            expect([
                "string",
                "multiline_string",
                "number",
                "boolean",
                "date",
                "xyz",
                "geojson",
            ]).toContain(type);
        }
        expect(schema).toEqual(documentedFieldTypes(fw));
    });

    test("every ext_*/genai_* key the pipeline source writes is documented in the tables and the schema blocks", () => {
        const lambdaDir = path.join("backendPipelines", "system", "genAiMetadata", "lambda");
        const source =
            readRepo(path.join(lambdaDir, "metadataCatalog.py")) +
            "\n" +
            readRepo(path.join(lambdaDir, "generateMetadata.py")) +
            "\n" +
            readRepo(path.join(lambdaDir, "generateEmbedding.py"));
        const written = pipelineOwnedKeys(source);
        expect(written).toContain("ext_dimensions"); // control: the literal extractor finds the catalogue's keys
        expect(written).toContain("genai_title");
        expect(written).toContain("genai_content_chunk_count"); // the chunk count is written by generateEmbedding.py
        expect(written).not.toContain("ext_made_up_control"); // control: it does not match everything
        const fw = section(readDoc("pipelines/system-genai-metadata.md"), "## Fields written");
        const documented = documentedFieldTypes(fw);
        const schema = schemaFieldTypes(fw);
        for (const key of written) {
            expect(documented).toHaveProperty(key);
            expect(schema).toHaveProperty(key);
        }
    });

    test("the event contract lists every Detail field the indexer reads, including the six segment fields", () => {
        const ev = section(
            readDoc("pipelines/system-genai-metadata.md"),
            "## Embeddings and the `vector.embedding.ready` event"
        );
        for (const field of [
            "schemaVersion",
            "databaseId",
            "assetId",
            "filePath",
            "versionId",
            "contentEtag",
            "bucketId",
            "fileClass",
            "fileExt",
            "fileSize",
            "contentType",
            "embeddingModelId",
            "embeddingDimensions",
            "analysisModelId",
            "sourceModalities",
            "pipelineExecutionId",
            "workflowExecutionId",
            "generatedAt",
            "documentS3Location",
            "segmentKey",
            "segmentKind",
            "segmentLabel",
            "segmentStartMs",
            "segmentEndMs",
            "segmentCount",
        ]) {
            expect(ev).toContain("`" + field + "`");
        }
    });

    test("the embeddings section states the composition order, the modality labels the code emits, and the capture and re-embed limitations", () => {
        const embedding = readRepo(
            path.join(
                "backendPipelines",
                "system",
                "genAiMetadata",
                "lambda",
                "generateEmbedding.py"
            )
        );
        const ev = section(
            readDoc("pipelines/system-genai-metadata.md"),
            "## Embeddings and the `vector.embedding.ready` event"
        );
        for (const label of [
            "asset-metadata",
            "file-identity",
            "genai-metadata",
            "file-attributes",
            "existing-file-metadata",
            "existing-asset-metadata",
            "existing-database-metadata",
            "existing-file-attributes",
            "file-text",
        ]) {
            expect(embedding).toContain('"' + label + '"'); // control: the label is one the source emits
            expect(ev).toContain("`" + label + "`");
        }
        expect(ev).toMatch(/`existing-file-attributes`[\s\S]*`file-text`/); // the excerpt is the last part
        expect(ev).toContain("12,000");
        expect(ev).toContain("400");
        expect(ev).toContain("`SEED_WITH_EXISTING_METADATA`");
        expect(ev).toContain("300 KiB"); // the envelope's per-entity capture bound
        expect(ev).toContain("captured whole"); // what an existing-* label does and does not assert
        expect(ev).toContain("does not re-embed");
    });

    test("the segment subsections state the window plan, the chunk defaults, the office formats and the cost note", () => {
        const text = readDoc("pipelines/system-genai-metadata.md");
        const ev = section(text, "## Embeddings and the `vector.embedding.ready` event");
        for (const h of ["### Segment vectors", "### Video windows", "### Content chunks"])
            expect(ev).toContain(h);
        const windows = section(ev, "### Video windows");
        for (const s of [
            "`videoTime`",
            "360",
            "`t`",
            "10 digits",
            "5,000 input tokens",
            "$0.005",
            "$2",
            "`segments/<segmentKey>.failed.json`",
            "`BedrockSegmentError`",
            "`SegmentFramesUnavailable`",
            "`SegmentPublishError`",
            "`BedrockThrottled`",
            "240,000",
            "skipped",
            "retried twice",
            "planned",
            "`segment-frames`",
            "not written as file metadata",
        ]) {
            expect(windows).toContain(s);
        }
        expect(windows).not.toContain("segments/results/<segmentKey>.failed.json"); // the aux prefix holds the map manifest only
        const chunks = section(ev, "### Content chunks");
        for (const s of [
            "`textChunk`",
            "1,600",
            "200",
            "1,000",
            "2,000,000",
            "50 MiB",
            "`c`",
            "6 digits",
            "`c000001`",
            "`genai_content_chunk_count`",
            "`CONTENT_CHUNKING`",
            "`file-text`",
            "`SegmentPublishError`",
        ]) {
            expect(chunks).toContain(s);
        }
        const segments = section(ev, "### Segment vectors");
        for (const s of [
            "`segmentKey`",
            "32 bytes",
            "`segmentKind`",
            "`none`",
            "`segmentCount`",
            "one hit per file",
        ]) {
            expect(segments).toContain(s);
        }
        expect(section(text, "## Failure behaviour")).toContain("`SegmentPublishError`");
        expect(section(text, "## Costs")).toContain("window");
        // Control: the subsection extractor is scoped — a heading that is not in the page yields nothing.
        expect(section(ev, "### Audio transcription")).toBe("");
    });

    test("the window and chunk constants on the page are the source's", () => {
        const lambdaDir = path.join("backendPipelines", "system", "genAiMetadata", "lambda");
        const video = readRepo(path.join(lambdaDir, "videoSegments.py"));
        const chunks = readRepo(path.join(lambdaDir, "contentChunks.py"));
        expect(video).toMatch(/^VIDEO_SEGMENT_MAX\s*=\s*360$/m);
        expect(video).toMatch(/^VIDEO_SEGMENT_MIN_SECONDS\s*=\s*2$/m);
        expect(chunks).toMatch(/^CONTENT_CHUNK_CHARS\s*=\s*1_600$/m);
        expect(chunks).toMatch(/^CONTENT_CHUNK_OVERLAP_CHARS\s*=\s*200$/m);
        expect(chunks).toMatch(/^CONTENT_CHUNK_MAX\s*=\s*1_000$/m);
    });

    test("the template section lists the ten tags of the shipped template and the vocabulary object", () => {
        const template = JSON.parse(
            readRepo(
                path.join(
                    "backendPipelines",
                    "system",
                    "genAiMetadata",
                    "vamsSchema",
                    "templates",
                    "system-genai-metadata-default.json"
                )
            )
        ) as { tagSchema: Array<{ tagKey: string }>; configBody: string };
        const tags = template.tagSchema.map((t) => t.tagKey);
        expect(tags).toHaveLength(10);
        const tpl = section(readDoc("pipelines/system-genai-metadata.md"), "## Template");
        for (const tag of tags) expect(tpl).toContain("`" + tag + "`");
        expect(template.configBody).toContain('"classificationVocabulary"');
        expect(tpl).toContain("`classificationVocabulary`");
        expect(tpl).toMatch(/^\| `VIDEO_SEGMENT_SECONDS`\s+\| integer/m); // control: the tag is a table row, so the two line-scoped matches below read that row
        expect(tpl).toMatch(/`VIDEO_SEGMENT_SECONDS`[^\n]*\$0\.005[^\n]*\$2[^\n]*360/);
        expect(tpl).toMatch(/`CONTENT_CHUNKING`[^\n]*`true`/);
    });

    test("the failure section names every Bedrock and segment error code the pipeline emits", () => {
        const fail = section(readDoc("pipelines/system-genai-metadata.md"), "## Failure behaviour");
        for (const code of [
            "BedrockAccessDenied",
            "BedrockModelError",
            "BedrockThrottled",
            "BedrockEmbeddingError",
            "BedrockGuardrailIntervened",
            "BedrockSegmentError",
            "SegmentPublishError",
            "SegmentFramesUnavailable",
        ]) {
            expect(fail).toContain("`" + code + "`");
        }
    });

    test("the configuration section documents every key of the config block, including the guardrail pair", () => {
        const configTs = readRepo(path.join("infra", "config", "config.ts"));
        const cfg = section(readDoc("pipelines/system-genai-metadata.md"), "## Configuration");
        for (const key of [
            "enabled",
            "bedrockAnalysisModelId",
            "autoRegisterWithVAMS",
            "autoRegisterAutoTriggerOnFileUpload",
            "useFargateRenderer",
            "lambdaLimits.maxInputFileSizeMb",
            "lambdaLimits.maxPointCloudPoints",
            "bedrockGuardrail",
        ]) {
            expect(cfg).toContain("`" + key + "`");
        }
        expect(configTs).toMatch(
            /bedrockGuardrail:\s*\{\s*guardrailIdentifier:\s*string;\s*guardrailVersion:\s*string;\s*create:\s*\{\s*enabled:\s*boolean;\s*promptAttackInputStrength:\s*SystemGenAiGuardrailPromptAttackStrength;\s*piiFilter:\s*SystemGenAiGuardrailPiiFilter;\s*\};\s*\}/
        );
        expect(cfg).toContain("guardrailIdentifier");
        expect(cfg).toContain("guardrailVersion");
        for (const key of [
            "create.enabled",
            "create.promptAttackInputStrength",
            "create.piiFilter",
        ]) {
            expect(cfg).toContain("`" + key + "`");
        }
    });
});

describe("concepts/vector-search.md — the vector search concept page", () => {
    test("the page exists and carries its headings", () => {
        expect(docExists("concepts/vector-search.md")).toBe(true);
        const text = readDoc("concepts/vector-search.md");
        for (const h of [
            "# Vector search",
            "## What is embedded",
            "## Where embeddings live",
            "## Only the latest live version is searchable",
            "## Searching",
            "## Filters with and without Amazon OpenSearch",
            "## Reindexing",
            "## Changing the embedding model",
            "## Costs",
            "## Limits",
        ]) {
            expect(text).toContain(h);
        }
        const embedded = section(text, "## What is embedded");
        expect(embedded).toContain("`genai_*`");
        expect(embedded).toContain("`ext_*`");
        expect(embedded).toContain("database");
        for (const label of [
            "existing-file-metadata",
            "existing-asset-metadata",
            "existing-database-metadata",
            "existing-file-attributes",
        ]) {
            expect(embedded).toContain("`" + label + "`");
        }
        expect(embedded).toMatch(/`existing-file-attributes`[\s\S]*`file-text`/); // the excerpt is the last part
        expect(embedded).toContain("`SEED_WITH_EXISTING_METADATA`");
        expect(embedded).toContain("300 KiB"); // the envelope's per-entity capture bound
        expect(embedded).toContain("captured whole"); // what an existing-* label does and does not assert
        expect(embedded).toContain("does not re-embed");
        expect(embedded).toContain("### Segment vectors");
        expect(embedded).toContain("`VIDEO_SEGMENT_SECONDS`");
        expect(embedded).toContain("`CONTENT_CHUNKING`");
        expect(embedded).toContain("50 MiB");
        expect(embedded).toContain("`_vector.segmentHits`");
        expect(embedded).toContain("`_vector.bestSegment`");
        expect(embedded).toContain("`nlp.itemsCollapsed`");
        expect(embedded).toContain("`includeSegments: false`");
        expect(embedded).toContain("`nlp.classIntent`");
        expect(embedded).toContain("never a filter");
        expect(embedded).toContain("`segment-frames`");
        expect(embedded).toContain("**Search inside files**");
        expect(embedded).toContain("ranking bias");
    });

    test("the storage section names the index name pattern and all seven filter attributes", () => {
        const s = section(readDoc("concepts/vector-search.md"), "## Where embeddings live");
        expect(s).toContain("`vec-<model>-<dimensions>`");
        for (const attr of [
            "databaseId",
            "isLatest",
            "isArchived",
            "fileClass",
            "fileExt",
            "embeddingModelId",
            "segmentKind",
        ]) {
            expect(s).toContain("`" + attr + "`");
        }
    });

    test("the searching section names every client surface and the warning codes the API emits", () => {
        const s = section(readDoc("concepts/vector-search.md"), "## Searching");
        expect(s).toContain("`POST /search/nlp`");
        expect(s).toContain("`vamscli search nlp`");
        expect(s).toContain("`search_nlp`");
        expect(s).toContain("`VECTORSEARCH`");
        expect(s).toContain("`bestSegment`");
        expect(s).toContain("`classIntent`");
        const codes = readRepo(path.join("backend", "backend", "models", "vectorsearch.py"));
        for (const code of ["truncated:window", "truncated:targets", "segments:window_full"]) {
            expect(codes).toContain('"' + code + '"'); // control: the code is one the model declares
            expect(s).toContain("`" + code + "`");
        }
        const store = readRepo(
            path.join("backend", "backend", "common", "vectorsearch", "vectorStore.py")
        );
        expect(store).toMatch(/^MAX_TOP_K\s*=\s*100$/m);
        expect(s).toContain("100 candidates");
    });

    test("the model-change procedure is the four-step clear/disable/change/backfill sequence", () => {
        const s = section(readDoc("concepts/vector-search.md"), "## Changing the embedding model");
        expect(s).toMatch(/1\..*"clear"/s);
        expect(s).toMatch(/2\..*vectorSearch\.enabled.*false/s);
        expect(s).toMatch(/3\..*embeddingModelId/s);
        expect(s).toMatch(/4\..*vectorBackfill/s);
        expect(s).toContain("`LimitExceededException`");
    });

    test("the reindexing section names the reindexer's operations and optional keys as the handler accepts them", () => {
        const reindexer = readRepo(
            path.join("backend", "backend", "handlers", "osVectorSearch", "vectorReindexer.py")
        );
        const s = section(readDoc("concepts/vector-search.md"), "## Reindexing");
        for (const op of ['"clear"', '"enqueue"', '"both"']) {
            expect(reindexer).toContain(op);
            expect(s).toContain("`" + op + "`");
        }
        for (const key of ["dryRun", "limit", "databaseId", "startAfter"]) {
            expect(reindexer).toContain(key);
            expect(s).toContain("`" + key + "`");
        }
        expect(s).toContain("`System-Reindex`");
    });

    test("the limits section records the open items", () => {
        const s = section(readDoc("concepts/vector-search.md"), "## Limits");
        expect(s).toContain("100");
        expect(s).toContain("`truncated`");
        expect(s).toContain("`segments:window_full`");
        expect(s).toContain("European Sovereign Cloud");
        expect(s).toContain("FIPS");
        expect(s).toContain("`embeddingDimensions`");
        expect(s).toContain("windows or chunks");
        expect(s).toContain("retrieval-augmented generation");
    });
});

describe("sidebar and page set", () => {
    test("control: the sidebar lists the pipeline overview", () => {
        expect(sidebarDocIds()).toContain("pipelines/overview");
    });

    test("every sidebar id resolves to a page", () => {
        const missing = sidebarDocIds().filter(
            (id) => !docExists(id + ".md") && !docExists(id + ".mdx")
        );
        expect(missing).toEqual([]);
    });

    test("the three new pages are in the sidebar", () => {
        const ids = sidebarDocIds();
        expect(ids).toContain("pipelines/system-pipelines");
        expect(ids).toContain("pipelines/system-genai-metadata");
        expect(ids).toContain("concepts/vector-search");
    });

    test("every page under pipelines/ is reachable from the sidebar", () => {
        const ids = new Set(sidebarDocIds());
        const orphans = walkDocs()
            .filter((p) => p.startsWith("pipelines/"))
            .map((p) => p.replace(/\.mdx?$/, ""))
            .filter((id) => !ids.has(id));
        expect(orphans).toEqual([]);
    });

    test("no page links to a Markdown file that does not exist", () => {
        const broken: string[] = [];
        for (const page of walkDocs()) {
            const text = readDoc(page);
            for (const m of text.matchAll(/\]\(([^)#\s:]+?\.mdx?)(?:#[^)]*)?\)/g)) {
                const target = path.resolve(path.join(DOCS, path.dirname(page)), m[1]);
                if (!fs.existsSync(target)) broken.push(`${page} -> ${m[1]}`);
            }
        }
        expect(broken).toEqual([]);
    });

    test("every /img/ PNG or JPEG a page embeds exists under static/img", () => {
        const missing: string[] = [];
        for (const page of walkDocs()) {
            for (const m of readDoc(page).matchAll(/!\[[^\]]*\]\(\/img\/([^)\s]+)\)/g)) {
                if (!fs.existsSync(path.join(SITE, "static", "img", m[1])))
                    missing.push(`${page} -> /img/${m[1]}`);
            }
        }
        expect(missing).toEqual([]);
    });

    // TEMPORARY-TEST — pins the removal of pipelines/genai-labeling.md, pipelines/cad-mesh-extraction.md, and
    // the four copies of the retired labeling pipeline's use-case diagram; the durable "no link to a
    // missing page" and "every /img/ PNG a page embeds exists" tests are what keep holding once this is deleted.
    // The retired identifiers are assembled from parts because retiredPipelineIdentifiers.test.ts forbids their
    // literal spelling anywhere under infra/, including here.
    test("the retired pipeline pages and their diagram are gone and nothing names them", () => {
        expect(docExists("pipelines/genai-labeling.md")).toBe(false);
        expect(docExists("pipelines/cad-mesh-extraction.md")).toBe(false);
        expect(sidebarDocIds()).not.toContain("pipelines/genai-labeling");
        expect(sidebarDocIds()).not.toContain("pipelines/cad-mesh-extraction");
        const naming = walkDocs().filter((p) =>
            /genai-labeling|cad-mesh-extraction/.test(readDoc(p))
        );
        expect(naming).toEqual([]);
        const diagram = "pipeline_usecase_genAi" + RETIRED_LABELING_STEM;
        const retiredDiagram = [
            path.join(REPO, "documentation", "diagrams", diagram + ".png"),
            path.join(REPO, "documentation", "diagrams", "source", "drawio", diagram + ".drawio"),
            path.join(SITE, "static", "img", diagram + ".png"),
            path.join(DOCS, "assets", "images", "diagrams", diagram + ".png"),
        ];
        expect(retiredDiagram.filter((p) => fs.existsSync(p))).toEqual([]);
    });
});

describe("pipelines/overview.md — the built-in table, the VPC chart, and the output paths", () => {
    test("control: the section and its columns are present", () => {
        const t = section(readDoc("pipelines/overview.md"), "## Built-in Pipelines");
        expect(t).toContain("| Pipeline");
        expect(t).toContain("| Config Flag");
        expect(tableRows(t).length).toBeGreaterThan(10);
    });

    test("every config flag the table names is a pipelines block in config.ts", () => {
        const configTs = readRepo(path.join("infra", "config", "config.ts"));
        const t = section(readDoc("pipelines/overview.md"), "## Built-in Pipelines");
        const unknown: string[] = [];
        for (const row of tableRows(t)) {
            const cells = row.split("|").map((c) => c.trim());
            for (const m of cells[2].matchAll(/`(use[A-Za-z0-9]+)(?:\.[A-Za-z0-9.]+)?`/g)) {
                if (!configTs.includes(m[1])) unknown.push(m[1]);
            }
        }
        expect(unknown).toEqual([]);
    });

    test("the consolidated system pipeline replaces the two retired rows", () => {
        const t = section(readDoc("pipelines/overview.md"), "## Built-in Pipelines");
        expect(t).toContain("[SYSTEM - GenAI Metadata Generation](system-genai-metadata.md)");
        expect(t).toContain("`useSystemGenAiMetadata`");
        expect(t).toContain("`SYSTEM - Preview`");
        expect(t).toContain("[system pipelines](system-pipelines.md)");
    });

    test("the chart carries the system pipeline and no Rekognition endpoint", () => {
        const s = section(readDoc("pipelines/overview.md"), "### VPC and Network Requirements");
        expect(s).toContain("| 3D Basic Conversion");
        expect(s).toContain("| SYSTEM - GenAI Metadata Generation");
        expect(s).toContain("Amazon Bedrock Runtime¹");
        expect(s).toContain("`useSystemGenAiMetadata.useFargateRenderer`");
        expect(s).toContain("`app.vectorSearch.enabled`");
        expect(s).not.toContain("Rekognition");
    });

    test("the output-path table carries the results prefix and the reserved status file", () => {
        const s = section(readDoc("pipelines/overview.md"), "## Pipeline S3 Output Paths");
        expect(s).toContain("`outputS3AssetResultsPath`");
        expect(s).toContain("`execution.status.json`");
    });

    test("the execution-flow diagram shows the results prefix and the pipeline-reported FAILED path", () => {
        const s = section(readDoc("pipelines/overview.md"), "## Pipeline Execution Flow");
        expect(s).toContain("```mermaid");
        expect(s).toContain("results prefix");
        expect(s).toContain("execution.status.json");
    });
});

describe("architecture pages", () => {
    test("every feature-flag table lists every VAMS_APP_FEATURES member", () => {
        const members = featureEnumMembers();
        expect(members.length).toBeGreaterThan(10);
        expect(members).toContain("VECTORSEARCH");
        for (const [page, heading] of [
            ["architecture/details.md", "### Feature Flags"],
            ["overview/features.md", "### Feature Flags"],
        ]) {
            const table = section(readDoc(page), heading);
            const missing = members.filter((m) => !table.includes("`" + m + "`"));
            expect({ page, missing }).toEqual({ page, missing: [] });
        }
    });

    test("details.md indexing flow shows the vector indexer and the vector table", () => {
        const s = section(readDoc("architecture/details.md"), "## Data Indexing Flow");
        expect(s).toContain("vector.embedding.ready");
        expect(s).toContain("Vector Indexer");
        expect(s).toContain("Vector Embeddings Table");
    });

    test("details.md Available Pipelines names compute per current pipeline set", () => {
        const s = section(readDoc("architecture/details.md"), "### Available Pipelines");
        expect(s).toContain("| SYSTEM - GenAI Metadata Generation");
        expect(s).toMatch(/\| 3D Basic Conversion\s+\| AWS Lambda/);
        expect(s).toContain("| Coordinate Transform");
        expect(s).toContain("| NVIDIA Cosmos 3");
        expect(s).not.toContain("Rekognition");
    });

    test("details.md dependency chain shows SearchBuilder depending on ApiBuilder2, as core-stack.ts declares", () => {
        const core = readRepo(path.join("infra", "lib", "core-stack.ts"));
        expect(core).toContain(
            "searchBuilderNestedStack.addStackDependency(apiBuilder2NestedStack)"
        );
        const s = section(readDoc("architecture/details.md"), "## Nested Stack Dependency Chain");
        expect(s).toMatch(/APIBuild2\["ApiBuilder2/);
        expect(s).toMatch(/APIBuild2 --> SearchB/);
    });

    test("aws-resources.md names the vector search functions, queues, and rule", () => {
        const text = readDoc("architecture/aws-resources.md");
        expect(section(text, "### Search and Indexing Functions")).toContain(
            "`osVectorSearchFunctions.ts`"
        );
        const sqs = section(text, "## Amazon SQS Queues");
        expect(sqs).toContain("**VectorIndexerQueue**");
        expect(sqs).toContain("**SystemWorkflowLaunchQueue**");
        expect(section(text, "## Amazon EventBridge")).toContain("**Vector Embedding Ready Rule**");
        expect(section(text, "## Amazon OpenSearch Service")).toMatch(/natural-language search/i);
        expect(text).toContain("VAMSStateMachine-SystemGenAiMetadata");
    });

    test("security.md carries the Bedrock data-handling note and the partition rows", () => {
        const text = readDoc("architecture/security.md");
        expect(text).toContain("### Data Sent to Amazon Bedrock");
        const gov = section(text, "## GovCloud Security Constraints");
        expect(gov).toContain("`vectorSearch.enabled`");
        expect(gov).toContain("DynamoDB vector search");
    });

    test("networking.md carries the Bedrock Runtime endpoint row and no Rekognition", () => {
        const text = readDoc("architecture/networking.md");
        const cond = section(text, "### Conditional Interface Endpoints");
        expect(cond).toContain("| Amazon Bedrock Runtime");
        expect(cond).toContain("`bedrock-runtime`");
        expect(text).not.toContain("Rekognition");
        expect(text).toContain("BYO-VPC");
    });
});

describe("deployment pages", () => {
    test("prerequisites.md has the Bedrock model-access section the pipeline page links to", () => {
        const text = readDoc("deployment/prerequisites.md");
        expect(text).toContain("### Amazon Bedrock model access");
        const s = section(text, "### Amazon Bedrock model access");
        expect(s).toContain("aws-marketplace:Subscribe");
        expect(s).toContain("FTUFormNotFilled");
        expect(s).toContain("us-gov-west-1");
        expect(s).toContain("Amazon Titan Text Embeddings V2");
        expect(s).toContain("bedrockGuardrail");
    });

    test("plan-your-deployment.md describes natural-language search as a capability and lists the VPC-requiring set", () => {
        const text = readDoc("deployment/plan-your-deployment.md");
        const search = section(text, "### Search capability");
        expect(search).toContain("**Natural-language (vector) search**");
        expect(search).toContain("`vectorSearch.enabled: true`");
        expect(search).toContain("`pipelines.useSystemGenAiMetadata`");
        const vpc = section(text, "### VPC configuration");
        expect(vpc).toContain("useSystemGenAiMetadata.useFargateRenderer");
        expect(vpc).toContain("Coordinate Transform");
        expect(vpc).toContain("NVIDIA Cosmos 3");
        expect(vpc).not.toContain("GenAI labeling");
        expect(section(text, "## Deployment modes")).toContain("`app.vectorSearch.enabled`");
    });

    test("configuration-reference.md documents the consolidated pipeline and its guardrail keys", () => {
        const text = readDoc("deployment/configuration-reference.md");
        expect(text).toContain("## Processing pipelines (`app.pipelines`)");
        const s = section(
            text,
            "### SYSTEM GenAI metadata (`app.pipelines.useSystemGenAiMetadata`)"
        );
        expect(s).toContain(
            "`app.pipelines.useSystemGenAiMetadata.bedrockGuardrail.guardrailIdentifier`"
        );
        expect(s).toContain(
            "`app.pipelines.useSystemGenAiMetadata.bedrockGuardrail.guardrailVersion`"
        );
        expect(section(text, "#### VPC Interface Endpoints")).toContain("| Bedrock Runtime");
    });

    // TEMPORARY-TEST — pins the removal of the Rekognition endpoint row and the two retired pipeline
    // configuration sections; configuration validation rejects both keys, so nothing would document them again.
    test("configuration-reference.md names neither retired configuration key nor Amazon Rekognition", () => {
        const text = readDoc("deployment/configuration-reference.md");
        expect(text).not.toContain(RETIRED_CAD_MESH_KEY);
        expect(text).not.toContain(RETIRED_LABELING_KEY);
        expect(text).not.toContain("Rekognition");
    });
});

describe("overview pages", () => {
    test("features.md spells out the built-in pipeline count that its table has", () => {
        const s = section(readDoc("overview/features.md"), "### Built-In Pipelines");
        const rows = tableRows(s);
        expect(rows.length).toBeGreaterThan(15);
        expect(s).toContain(
            `VAMS includes ${numberWord(rows.length)} built-in processing pipelines`
        );
    });

    test("features.md names natural-language search, vector indexing, and system pipelines", () => {
        const text = readDoc("overview/features.md");
        expect(section(text, "### Search")).toContain("**Natural-language search**");
        expect(section(text, "### Search Indexing")).toContain("**Vector indexing**");
        expect(section(text, "### Pipeline Capabilities")).toContain("**System pipelines**");
        const builtIns = section(text, "### Built-In Pipelines");
        expect(builtIns).toContain("`useSystemGenAiMetadata`");
        expect(builtIns).toContain("SYSTEM - Preview");
        expect(builtIns).not.toContain(RETIRED_LABELING_KEY);
        expect(builtIns).not.toContain(RETIRED_CAD_MESH_KEY);
    });

    test("benefits, use cases, solution overview, and the landing page describe search without OpenSearch as the only engine", () => {
        expect(section(readDoc("overview/benefits.md"), "## Intelligent Search")).toMatch(
            /natural-language/i
        );
        const useCases = readDoc("overview/use-cases.md");
        expect(useCases).not.toContain("Rekognition");
        expect(useCases).toContain("SYSTEM GenAI metadata pipeline");
        expect(readDoc("overview/solution-overview.md")).toMatch(/natural-language/i);
        expect(readDoc("index.mdx")).toMatch(/natural-language/i);
    });

    test("costs.md prices the embeddings model and the vector table", () => {
        const text = readDoc("overview/costs.md");
        expect(section(text, "### Search Services (Choose One or None)")).toContain(
            "**Natural-language (vector) search**"
        );
        expect(section(text, "## Pipeline Costs")).toContain("SYSTEM GenAI metadata");
    });
});

describe("concepts, troubleshooting, developer, and custom-pipelines additions", () => {
    test("concept pages point at vector search", () => {
        expect(readDoc("concepts/overview.md")).toContain("### Vector search");
        const mdSearch = section(
            readDoc("concepts/metadata-and-schemas.md"),
            "## Metadata in search"
        );
        expect(mdSearch).toContain("`genai_*`");
        expect(mdSearch).toContain("`ext_*`");
        expect(mdSearch).toContain("system-genai-metadata.md#fields-written");
        expect(readDoc("concepts/viewers.md")).toContain("SYSTEM GenAI metadata pipeline");
    });

    test("troubleshooting pages carry the vector-search answers and limits", () => {
        expect(
            section(
                readDoc("troubleshooting/faq.md"),
                "### Can I use VAMS without Amazon OpenSearch?"
            )
        ).toMatch(/natural-language search/i);
        expect(readDoc("troubleshooting/common-issues.md")).toContain(
            "### Natural-Language Search Does Not Return a Newly Uploaded File"
        );
        const limits = readDoc("troubleshooting/known-limitations.md");
        expect(limits).toContain("### Natural-Language Search Limits");
        expect(section(limits, "### Natural-Language Search Limits")).toContain("100");
        expect(section(limits, "### SYSTEM GenAI Metadata Pipeline Size Limits")).toContain(
            "50 MiB"
        );
        expect(limits).toContain("| DynamoDB vector search");
    });

    test("developer pages relate OpenSearch to vector search and list the MCP tool", () => {
        const os = readDoc("developer/opensearch.md");
        expect(os).toContain("## Relationship to vector search");
        expect(section(os, "## Disabling OpenSearch")).toContain("natural-language search");
        expect(
            section(readDoc("developer/agentic-development.md"), "### VAMS MCP Server")
        ).toContain("`search_nlp`");
    });

    test("custom-pipelines.md documents the isSystem bundle key and the embedding event contract", () => {
        const text = readDoc("pipelines/custom-pipelines.md");
        expect(section(text, "### pipeline.json")).toContain("| `isSystem`");
        const ev = section(text, "### Publishing embeddings for vector search");
        expect(ev).toContain("`vector.embedding.ready`");
        expect(ev).toContain("`events:PutEvents`");
        expect(ev).toContain("`documentS3Location`");
        for (const f of [
            "`segmentKey`",
            "`segmentKind`",
            "`segmentLabel`",
            "`segmentStartMs`",
            "`segmentEndMs`",
            "`segmentCount`",
        ]) {
            expect(ev).toContain(f);
        }
        expect(ev).toContain("32 bytes");
        expect(ev).toContain("whole-file");
        expect(section(text, "## Development checklist")).toContain("vector.embedding.ready");
        // The references the existing skill-sync test pins must survive the edits.
        expect(text).toContain("`/add-pipeline`");
        expect(text).toContain("`/add-api-endpoint`");
    });

    test("the segment-key contract on the custom-pipelines page is the one documentIds.py enforces", () => {
        const ids = readRepo(
            path.join("backend", "backend", "common", "indexing", "documentIds.py")
        );
        expect(ids).toMatch(/^SEGMENT_KEY_MAX_BYTES\s*=\s*32$/m);
        expect(ids).toMatch(
            /^SEGMENT_KINDS\s*=\s*\("none",\s*"videoTime",\s*"textChunk",\s*"animationTime"\)/m
        );
        const ev = section(
            readDoc("pipelines/custom-pipelines.md"),
            "### Publishing embeddings for vector search"
        );
        expect(ev).toContain("`videoTime`");
        expect(ev).toContain("`textChunk`");
        expect(ev).toContain("`none`");
    });
});

describe("user guide", () => {
    test("search-and-discovery.md is structured around tabs and the natural-language mode", () => {
        const text = readDoc("user-guide/search-and-discovery.md");
        for (const h of [
            "## Search tabs",
            "## Keyword search",
            "## Natural language search",
            "## Limited search mode",
        ]) {
            expect(text).toContain(h);
        }
        const nlp = section(text, "## Natural language search");
        expect(nlp).toContain("**Natural language**");
        expect(nlp).toContain("latest");
        expect(nlp).toContain("top");
        expect(nlp).toContain("`genai_*`");
        expect(nlp).toContain("`ext_*`");
        expect(nlp).toContain("**Metadata** tab");
        expect(nlp).toContain("listed once"); // segment matches collapse to one row; the popover names the best one
        expect(nlp).toContain("**Search inside files**"); // the whole-file-only toggle, on by default
        expect(nlp).toContain("50 MiB"); // the content-embedding size bound: a larger file has no chunks
        expect(section(text, "## Limited search mode")).toContain("vector search");
        const tabs = section(text, "## Search tabs");
        expect(tabs).toContain("`?tab=asset-list`");
        expect(tabs).toContain("`?tab=unified-search`");
        expect(tabs).not.toContain("?tab=search`");
    });

    test("the tab ids on the user-guide pages are the search provider ids the web catalog declares", () => {
        const catalog = readRepo(
            path.join("web", "src", "searchPlugin", "config", "searchProviderConfig.json")
        );
        for (const id of ["asset-list", "unified-search"]) {
            expect(catalog).toContain('"' + id + '"');
        }
    });

    test("web-interface.md describes the tabs, the toggle, the System badge, and the tab URL", () => {
        const text = readDoc("user-guide/web-interface.md");
        const search = section(text, "## Asset Search Page");
        expect(search).toContain("**Asset List**");
        expect(search).toContain("**Search**");
        expect(search).toContain("**Natural language**");
        expect(section(text, "### Pipelines Page")).toContain("**System**");
        expect(section(text, "### Workflows Page")).toContain("**System**");
        const urls = section(text, "### URL Patterns");
        expect(urls).toContain("?tab=asset-list");
        expect(urls).toContain("?tab=unified-search");
    });

    test("pipelines-and-workflows.md lists the current built-ins and the system-record behaviour", () => {
        const s = section(
            readDoc("user-guide/pipelines-and-workflows.md"),
            "## Built-in pipelines"
        );
        expect(s).toContain("**GenAI Metadata Generation**");
        expect(s).toContain("**System**");
        expect(s).not.toContain("GenAI Labeling");
        expect(s).not.toContain("**Metadata Extraction**");
    });
});

describe("changelog, revision history, and notices", () => {
    const version = (JSON.parse(readRepo("package.json")) as { version: string }).version;

    test("control: the version is a 2.x release", () => {
        expect(version).toMatch(/^2\.\d+\.\d+$/);
    });

    test("CHANGELOG.md's newest release heading is the package version", () => {
        const changelog = readRepo("CHANGELOG.md");
        const first = changelog.match(/^## \[(\d+\.\d+\.\d+)\]/m);
        expect(first && first[1]).toBe(version);
        const release = section(changelog, `## [${version}]`);
        expect(release).toContain("### Major Change Summary:");
        expect(release).toContain("### ⚠ BREAKING CHANGES");
        expect(release).toContain("### Features");
        expect(release).toContain("### Chores");
        expect(release).toContain("### Known Outstanding Issues");
        expect(release).toContain("`POST /search/nlp`");
        expect(release).toContain("`system-genai-metadata`");
        expect(release).toContain("`perInputFileVersion`");
        expect(release).toContain("`execution.status.json`");
        expect(release).toContain("`ext_*`");
        expect(release).toContain("`location`");
        expect(release).toContain("`classificationVocabulary`");
        expect(release).toContain("`EXTRACT_GEO_LOCATION`");
        expect(release).toContain("`WRITE_EXTRACTED_METADATA`");
    });

    test("additional/revisions.md carries the release row and section", () => {
        const revisions = readDoc("additional/revisions.md");
        expect(revisions).toContain(`| [${version}](#${version.replace(/\./g, "")}) |`);
        expect(revisions).toContain(`### ${version}`);
        expect(section(revisions, `### ${version}`)).toContain("**Removed:**");
    });

    test("the release notes carry the segment vectors inside the existing search and pipeline bullets", () => {
        const release = section(readRepo("CHANGELOG.md"), `## [${version}]`);
        for (const s of [
            "`includeSegments: false`",
            "`_vector.segmentHits`",
            "`bestSegment`",
            "`nlp.classIntent`",
            "`--no-segments`",
            "`VIDEO_SEGMENT_SECONDS`",
            "`CONTENT_CHUNKING`",
        ]) {
            expect(release).toContain(s);
        }
        // Condensed into the bullets that exist, not added as bullets: this branch's Features bullets
        // are exactly these openers, in order, at the head of the list. Bullets merged in from the
        // development branch (the workflow orchestration refinements) follow them.
        const featureOpeners = [
            "-   **Search** Natural-language (vector) search",
            "-   **Pipelines** Consolidated `SYSTEM - GenAI Metadata Generation` pipeline",
            "-   **Workflows & Pipelines** System pipelines and workflows",
            "-   **Workflows & Pipelines** Workflow execution locks",
            "-   **Workflows & Pipelines** `System-Reindex` trigger type",
            "-   **Pipelines** Reserved `execution.status.json` results file",
            "-   **CDK** The trigger dispatcher skips triggers",
        ];
        const bullets = section(release, "### Features")
            .split("\n")
            .filter((l) => /^- {3}\*\*/.test(l));
        expect(bullets.length).toBeGreaterThanOrEqual(featureOpeners.length);
        featureOpeners.forEach((opener, i) => expect(bullets[i].startsWith(opener)).toBe(true));
        // No segment-vector bullet of its own anywhere in the list.
        for (const line of bullets.slice(featureOpeners.length)) {
            expect(line).not.toMatch(/segment vector|content chunk|video window/i);
        }
        const revisions = section(readDoc("additional/revisions.md"), `### ${version}`);
        for (const s of [
            "`VIDEO_SEGMENT_SECONDS`",
            "`CONTENT_CHUNKING`",
            "`includeSegments: false`",
            "--no-segments`",
        ]) {
            expect(revisions).toContain(s);
        }
    });

    test("additional/notices.md records the consolidated pipeline's licensed components", () => {
        const text = readDoc("additional/notices.md");
        expect(text).toContain("### SYSTEM GenAI Metadata Pipeline Library Notice");
        const s = section(text, "### SYSTEM GenAI Metadata Pipeline Library Notice");
        expect(s).toContain("Blender");
        expect(s).toContain("GPL");
        expect(s).toContain("imageio-ffmpeg");
        expect(s).toContain("tinytag");
        expect(s).toContain("defusedxml");
        expect(s).toContain("IfcOpenShell");
        for (const lib of ["python-docx", "python-pptx", "openpyxl"]) expect(s).toContain(lib);
        expect(section(text, "### Optional LGPL-Licensed Components")).toContain(
            "SYSTEM GenAI metadata pipeline"
        );
        expect(text).not.toContain("CAD/Mesh Metadata Extraction");
    });

    test("the media image's office-format pins named in the notices page are the requirements.txt pins", () => {
        const requirements = readRepo(
            path.join(
                "backendPipelines",
                "system",
                "genAiMetadata",
                "containers",
                "media",
                "requirements.txt"
            )
        );
        for (const lib of [
            "python-docx",
            "python-pptx",
            "openpyxl",
            "tinytag",
            "defusedxml",
            "imageio-ffmpeg",
        ]) {
            expect(requirements).toMatch(new RegExp(`^${lib}==`, "m"));
        }
    });
});

describe("diagram and screenshot embeds", () => {
    // The owner-drawn diagrams and owner-captured screenshots are not in the tree; each planned embed is held by
    // one `TODO(owner)` placeholder comment so the page never references a missing /img/ file (which fails the build).
    const placeholders: Array<[string, string]> = [
        ["pipelines/system-genai-metadata.md", "pipeline_usecase_systemGenAiMetadata.png"],
        ["concepts/vector-search.md", "vectorSearch_queryFlow.png"],
        ["architecture/details.md", "dataQueues_MainFlow.png"],
        ["user-guide/search-and-discovery.md", "asset_search_table_2026MMDD_v2.7.png"],
        ["user-guide/search-and-discovery.md", "file_search_table_2026MMDD_v2.7.png"],
        ["user-guide/search-and-discovery.md", "search_nlp_mode_2026MMDD_v2.7.png"],
        ["user-guide/web-interface.md", "pipelines_page_system_badge_2026MMDD_v2.7.png"],
    ];

    test("every planned diagram or screenshot is either embedded from static/img or held by a TODO(owner) placeholder", () => {
        for (const [page, file] of placeholders) {
            const text = readDoc(page);
            const embedded = new RegExp(
                `!\\[[^\\]]*\\]\\(/img/${file.replace(/\./g, "\\.")}\\)`
            ).test(text);
            const held = new RegExp(
                `<!-- TODO\\(owner\\): (?:diagram|screenshot): ${file.replace(/\./g, "\\.")}`
            ).test(text);
            expect({ page, file, embeddedOrHeld: embedded || held }).toEqual({
                page,
                file,
                embeddedOrHeld: true,
            });
            if (embedded) expect(fs.existsSync(path.join(SITE, "static", "img", file))).toBe(true);
        }
    });

    test("the data-queue diagram the architecture page embeds exists in both repository copies", () => {
        // The two copies are not byte-identical today (different export runs), so only presence is pinned here.
        expect(fs.existsSync(path.join(SITE, "static", "img", "dataQueues_MainFlow.png"))).toBe(
            true
        );
        expect(
            fs.existsSync(path.join(REPO, "documentation", "diagrams", "dataQueues_MainFlow.png"))
        ).toBe(true);
    });
});
