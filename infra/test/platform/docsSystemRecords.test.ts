/**
 * System pipelines and workflows are documented where an operator will look.
 *
 * - `concepts/pipelines-and-workflows.md` carries a `### System pipelines` section with the refusal
 *   table the backend enforces, and the schema-owned admonition it extends still carries every phrase
 *   docsSchemaOwnershipAndSubscriptions.test.ts pins.
 * - `architecture/data-model.md` states, beside each of the two record tables, that rows carry
 *   `isSystem` and how a row without it reads.
 *
 * The messages are the literals `backend/backend/common/workflows/systemRecords.py` sends; the page
 * quotes them so a reader can match an error to its rule. A PUT refusal names the refused field, so
 * its template's `{field}` placeholder is quoted as `<field>`.
 */

import * as fs from "fs";
import * as path from "path";

const REPO = path.join(__dirname, "..", "..", "..");
const DOCS = path.join(REPO, "documentation", "docusaurus-site", "docs");

const readDoc = (rel: string): string => fs.readFileSync(path.join(DOCS, rel), "utf8");
const readRepo = (rel: string): string => fs.readFileSync(path.join(REPO, rel), "utf8");

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

function admonitionBody(text: string, title: string): string {
    const start = text.indexOf(title);
    if (start < 0) return "";
    const bodyStart = start + title.length;
    const end = text.indexOf("\n:::", bodyStart);
    return end < 0 ? "" : text.slice(bodyStart, end);
}

/** A Python string constant's value, read from the module that owns it. */
function pythonLiteral(source: string, name: string): string {
    const m = source.match(new RegExp(`^${name} = (?:\\(\\s*)?(['"])((?:(?!\\1).)*)\\1`, "m"));
    if (!m) throw new Error(`${name} not found`);
    return m[2];
}

const SCHEMA_OWNED_TITLE =
    ":::warning[Built-in pipelines, workflows, and templates are schema-owned by the CDK]";
const SYSTEM_HEADING = "### System pipelines";

describe("system records are documented", () => {
    const concepts = readDoc("concepts/pipelines-and-workflows.md");
    const records = readRepo(
        path.join("backend", "backend", "common", "workflows", "systemRecords.py")
    );

    test("the concepts page has a System pipelines section that names every refusal", () => {
        const body = section(concepts, SYSTEM_HEADING);
        expect(body.length).toBeGreaterThan(0);
        expect(body).toContain("`isSystem: true`");
        // Who sets the flag, and that an API caller cannot.
        expect(body).toContain("Only the deployment's registration importer sets `isSystem`");
        expect(body).toContain("creates an ordinary record");
        // The two bundles this deployment ships as system records.
        expect(body).toContain("`SYSTEM - GenAI`");
        expect(body).toContain("`SYSTEM - Preview`");
        for (const name of [
            "SYSTEM_PIPELINE_READONLY_MESSAGE",
            "SYSTEM_WORKFLOW_READONLY_MESSAGE",
            "SYSTEM_PIPELINE_ARCHIVE_MESSAGE",
            "SYSTEM_WORKFLOW_ARCHIVE_MESSAGE",
            "SYSTEM_TEMPLATE_LOCKED_MESSAGE",
            "SYSTEM_TRIGGER_LOCKED_MESSAGE",
        ]) {
            expect(body).toContain(pythonLiteral(records, name).replace("{field}", "<field>"));
        }
        // The allowed edits and the persistence rule, so the reader is left with an action.
        expect(body).toContain("`configBody`, `tagSchema`, and `webFormJson`");
        expect(body).toMatch(/re-asserts the shipped values/);
        expect(body).toContain("`isSystem` is not a permission constraint field");
    });

    test("the schema-owned admonition still carries every pinned phrase and points at the section", () => {
        const body = admonitionBody(concepts, SCHEMA_OWNED_TITLE);
        expect(body).toContain("`vamsSchema`");
        expect(body).toMatch(/Disabling or archiving a built-in in the web interface/);
        expect(body).toMatch(/re-enables it/);
        expect(body).toContain("`autoRegisterWithVAMS`");
        expect(body).toContain("`autoRegisterAutoTriggerOnFileUpload`");
        expect(body).toContain("`isSystem: true`");
        expect(body).toContain("[System pipelines](#system-pipelines)");
    });

    test("the built-in list names the shipped metadata pipeline", () => {
        // Durable: the tip must name the pipeline the deployment ships. No negative assertion on the
        // former wording — that would pin one past rewording (Rule 13's temporary case).
        const tip = admonitionBody(concepts, ":::tip[Built-in pipelines]");
        expect(tip).toContain("GenAI metadata generation");
    });

    test("the data model states isSystem beside both record tables", () => {
        const dataModel = readDoc("architecture/data-model.md");
        const pipelines = section(dataModel, "### Pipeline Storage Table (V2)");
        const workflows = section(dataModel, "### Workflow Storage Table (V2)");
        for (const text of [pipelines, workflows]) {
            expect(text).toContain("`isSystem`");
            expect(text).toMatch(/reads as `false`/);
            expect(text).toContain("`vamsSchema`");
        }
    });

    test("control: the literal reader finds a known constant", () => {
        expect(pythonLiteral(records, "IMPORT_SOURCE_MARKER")).toBe("vamsSchemaImport");
    });

    test("control: the literal reader reads a parenthesised PUT refusal template whole", () => {
        expect(pythonLiteral(records, "SYSTEM_PIPELINE_READONLY_MESSAGE")).toBe(
            'System pipelines are read-only except "enabled"; "{field}" cannot be changed.'
        );
    });
});

describe("every shipped system bundle carries a SYSTEM category, and the pages say which", () => {
    /** Every vamsSchema* directory under backendPipelines whose pipeline.json declares isSystem: true. */
    function systemBundles(): string[] {
        const found: string[] = [];
        const walk = (dir: string): void => {
            for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
                if (!entry.isDirectory() || entry.name === "node_modules") continue;
                const full = path.join(dir, entry.name);
                const pipelineFile = path.join(full, "pipeline.json");
                // vamsSchema*: a pipeline folder may ship a second bundle beside its own (vamsSchemaMedia).
                const inBundle = full.split(path.sep).some((part) => part.startsWith("vamsSchema"));
                if (inBundle && fs.existsSync(pipelineFile)) {
                    if (JSON.parse(fs.readFileSync(pipelineFile, "utf8")).isSystem === true) {
                        found.push(full);
                    }
                }
                walk(full);
            }
        };
        walk(path.join(REPO, "backendPipelines"));
        return found.sort();
    }

    const bundles = systemBundles();
    const shipped = section(
        readDoc("pipelines/system-pipelines.md"),
        "## Shipped system pipelines"
    );
    const concepts = section(readDoc("concepts/pipelines-and-workflows.md"), SYSTEM_HEADING);

    test("control: the walk finds the five shipped system bundles, Zip Archive and Media Preview included", () => {
        expect(bundles).toHaveLength(5);
        expect(bundles.some((dir) => dir.includes(path.join("utility", "zipArchive")))).toBe(true);
        expect(
            bundles.some((dir) => dir.endsWith(path.join("3dThumbnail", "vamsSchemaMedia")))
        ).toBe(true);
    });

    test.each(bundles.map((dir) => [path.relative(REPO, dir), dir]))(
        "%s: pipeline and workflow share a SYSTEM - <Area> category the pages name",
        (_rel, dir) => {
            const pipeline = JSON.parse(fs.readFileSync(path.join(dir, "pipeline.json"), "utf8"));
            const workflow = JSON.parse(fs.readFileSync(path.join(dir, "workflow.json"), "utf8"));
            expect(pipeline.category).toMatch(/^SYSTEM - \S/);
            expect(workflow.category).toBe(pipeline.category);
            expect(shipped).toContain(`\`${pipeline.category}\``);
            expect(concepts).toContain(`\`${pipeline.category}\``);
        }
    );

    test("the Media Preview Thumbnail row of the shipped table names its category", () => {
        const row =
            shipped.split("\n").find((line) => line.includes("`preview-media-thumbnail`")) ?? "";
        expect(row).toContain("`SYSTEM - Preview`");
    });

    test("the Zip Archive row of the shipped table names its category", () => {
        const row =
            shipped.split("\n").find((line) => line.includes("`utility-zip-archive`")) ?? "";
        expect(row).toContain("`SYSTEM - Utility`");
    });

    // D132: a compliance pipeline rule may use no system pipeline; every list of them names each bundle.
    const RULE_NOTE_TITLE = ":::note[System pipelines cannot back a pipeline rule]";
    const ruleLists: [string, string][] = [
        [
            "user-guide/compliance.md",
            admonitionBody(readDoc("user-guide/compliance.md"), RULE_NOTE_TITLE),
        ],
        [
            "CHANGELOG.md",
            readRepo("CHANGELOG.md")
                .split("\n")
                .find((line) =>
                    line.includes("A compliance pipeline rule cannot use a system pipeline")
                ) ?? "",
        ],
        [
            "additional/revisions.md",
            readDoc("additional/revisions.md")
                .split("\n")
                .find((line) =>
                    line.includes("A compliance pipeline rule cannot use a system pipeline")
                ) ?? "",
        ],
    ];

    test.each(ruleLists)(
        "%s: the list of system pipelines a pipeline rule may not use is found",
        (_doc, text) => {
            // Control: an empty match would make every name check below vacuous.
            expect(text.length).toBeGreaterThan(100);
        }
    );

    test.each(
        bundles.flatMap((dir) =>
            ruleLists.map(([doc, text]) => [
                path.relative(REPO, dir),
                doc,
                text,
                JSON.parse(fs.readFileSync(path.join(dir, "pipeline.json"), "utf8")).pipelineName,
            ])
        )
    )(
        "%s: %s names its pipeline among those a pipeline rule may not use",
        (_rel, _doc, text, name) => {
            expect(text).toContain(name);
        }
    );
});
