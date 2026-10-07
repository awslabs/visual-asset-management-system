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
 * quotes them so a reader can match an error to its rule.
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
        expect(body).toContain("`SYSTEM - Preview`");
        for (const name of [
            "SYSTEM_PIPELINE_READONLY_MESSAGE",
            "SYSTEM_WORKFLOW_READONLY_MESSAGE",
            "SYSTEM_PIPELINE_ARCHIVE_MESSAGE",
            "SYSTEM_WORKFLOW_ARCHIVE_MESSAGE",
            "SYSTEM_TEMPLATE_LOCKED_MESSAGE",
            "SYSTEM_TRIGGER_LOCKED_MESSAGE",
        ]) {
            expect(body).toContain(pythonLiteral(records, name));
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
});
