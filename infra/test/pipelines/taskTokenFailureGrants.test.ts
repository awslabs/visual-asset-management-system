/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every pipeline Lambda whose handler reports a task-token failure must be GRANTED
 * `states:SendTaskFailure` by the CDK builder that creates it.
 *
 * The two halves are inert without each other, and the failure is silent in the direction that matters:
 * with the handler code but no grant, `SendTaskFailure` raises `AccessDeniedException`, the handler logs
 * one line, and the workflow task waits out its full `taskTimeout` — hours on the GPU pipelines. Nothing
 * fails, nothing synthesizes differently, and no unit test of the handler notices, because the handler is
 * correct.
 *
 * This test exists because the same defect was found FIVE times in one review, in five different
 * pipelines, and each time it was found by hand. A file-level grep does not catch it: these builder files
 * typically grant the action to two or three PEER functions, so `grep -c SendTaskFailure <builder>` returns
 * a healthy-looking number while the function under test has none. Scope is the whole point.
 *
 * Two builder shapes exist in the tree and both are handled:
 *   - `export function buildXFunction(...)` ... `fun.addToRolePolicy(...)`   (most pipelines)
 *   - a `Construct` subclass assigning `this.xFunction = new lambda.Function(...)` and later
 *     `this.xFunction.addToRolePolicy(...)`                                   (Isaac Lab)
 *
 * Asserted against SOURCE rather than a synthesized template on purpose: most of these pipelines are
 * disabled in every shipped config template, so a template assertion would inspect nothing for them —
 * the same reason `containerImagePinning.test.ts` reads source.
 */

import * as fs from "fs";
import * as path from "path";

const REPO = path.join(__dirname, "..", "..", "..");
const PIPELINES = path.join(REPO, "backendPipelines");
const BUILDERS = path.join(REPO, "infra", "lib", "nestedStacks", "pipelines");

/** Recursively collect files matching a predicate, skipping build and cache directories. */
function walk(dir: string, keep: (name: string) => boolean): string[] {
    const out: string[] = [];
    if (!fs.existsSync(dir)) return out;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        if (
            entry.name === "node_modules" ||
            entry.name === "__pycache__" ||
            entry.name === "cdk.out"
        )
            continue;
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) out.push(...walk(full, keep));
        else if (keep(entry.name)) out.push(full);
    }
    return out;
}

/**
 * Handler module names (the `.py` stem) that call SendTaskFailure, per pipeline directory.
 *
 * Keyed on the STEM because that is what a CDK builder names in its `handler:` property, which is the
 * only reliable link between a Python file and the construct that creates its function.
 */
function handlersReportingFailure(): Map<string, Set<string>> {
    const byPipeline = new Map<string, Set<string>>();
    for (const file of walk(PIPELINES, (n) => n.endsWith(".py"))) {
        const rel = path.relative(PIPELINES, file).replace(/\\/g, "/");
        // Only orchestration lambdas: a container's own code has no CDK-granted role of its own.
        if (!rel.includes("/lambda/")) continue;
        if (rel.includes("/tests/")) continue;
        const text = fs.readFileSync(file, "utf-8");
        if (!/send_task_failure/.test(text)) continue;
        const pipeline = rel.split("/lambda/")[0];
        const stem = path.basename(file, ".py");
        if (!byPipeline.has(pipeline)) byPipeline.set(pipeline, new Set());
        byPipeline.get(pipeline)!.add(stem);
    }
    return byPipeline;
}

/**
 * Resolve the handler MODULE STEM from one builder scope.
 *
 * Two spellings exist and both must be handled, because the one the tree actually uses is the harder one:
 *   handler: "openPipeline.lambda_handler"          -- a plain literal
 *   const name = "constructPipeline";
 *   handler: `${name}.lambda_handler`               -- a template literal over a local const
 *
 * The second is what nearly every pipeline builder uses. Matching only the first made this test report
 * grants that demonstrably exist, because the captured "stem" was the literal text `${name}`.
 */
function handlerStems(scope: string): string[] {
    const stems: string[] = [];
    for (const m of scope.matchAll(/handler:\s*[`"']([^`"']+)[`"']/g)) {
        const value = m[1];
        const templated = value.match(/^\$\{(\w+)\}\./);
        if (templated) {
            // Resolve the const the template interpolates, within this same scope.
            const decl = scope.match(
                new RegExp(`const\\s+${templated[1]}\\s*=\\s*["'\`]([^"'\`]+)["'\`]`)
            );
            if (decl) stems.push(decl[1]);
            continue;
        }
        const parts = value.split(".");
        if (parts.length >= 2) stems.push(parts[parts.length - 2]);
    }
    return stems;
}

/**
 * For a builder file, return the set of handler stems whose creating scope also carries the grant.
 *
 * A "scope" is one `export function build...` body, or one `this.<name>Function = ...` assignment paired
 * with any later `this.<name>Function.addToRolePolicy(...)`. Both are matched textually because the
 * builders are hand-written in two consistent styles.
 */
function grantedHandlerStems(builderText: string): Set<string> {
    const granted = new Set<string>();

    // Shape 1: exported builder functions. Split on the declaration and treat each chunk as one scope.
    const chunks = builderText.split(/^export function /m);
    for (const chunk of chunks) {
        if (!/SendTaskFailure/.test(chunk)) continue;
        for (const stem of handlerStems(chunk)) granted.add(stem);
    }

    // Shape 2: a Construct class assigning this.<name>Function and granting on it later.
    for (const m of builderText.matchAll(/this\.(\w+)\s*=\s*new lambda\.Function\(/g)) {
        const prop = m[1];
        const grantRe = new RegExp(`this\\.${prop}\\.addToRolePolicy\\(`, "g");
        let hasGrant = false;
        for (const g of builderText.matchAll(grantRe)) {
            if (/SendTaskFailure/.test(builderText.slice(g.index!, g.index! + 500)))
                hasGrant = true;
        }
        if (!hasGrant) continue;
        // A class property's `const name` may be declared anywhere in the constructor, so resolve
        // against the whole file rather than the local slice.
        const tail = builderText.slice(m.index!, m.index! + 900) + "\n" + builderText;
        for (const stem of handlerStems(tail)) {
            granted.add(stem);
            break; // the first resolved stem belongs to this assignment
        }
    }
    return granted;
}

describe("pipeline task-token failure grants", () => {
    const reporting = handlersReportingFailure();
    const builderFiles = walk(BUILDERS, (n) => n.endsWith(".ts"));
    const allBuilderText = builderFiles.map((f) => fs.readFileSync(f, "utf-8")).join("\n");

    it("finds pipeline handlers that report a task-token failure", () => {
        // Control: an empty map makes the assertion below pass while checking nothing, which is the
        // failure mode of every source-scanning test.
        expect(reporting.size).toBeGreaterThanOrEqual(5);
        expect(builderFiles.length).toBeGreaterThanOrEqual(5);
    });

    it("the scope-aware matcher is not just a file-level grep", () => {
        // Control for the matcher itself. This synthetic builder grants the action to ONE function while
        // a second function in the same file has none — the exact false positive that let this defect be
        // missed five times. A file-level grep returns 1 and looks healthy; the matcher must return only
        // the granted stem.
        const synthetic = `
export function buildGrantedFunction(scope) {
    const fun = new lambda.Function(scope, "a", { handler: "granted.lambda_handler" });
    fun.addToRolePolicy(new iam.PolicyStatement({ actions: ["states:SendTaskFailure"] }));
    return fun;
}
export function buildUngrantedFunction(scope) {
    const fun = new lambda.Function(scope, "b", { handler: "ungranted.lambda_handler" });
    return fun;
}
export function buildTemplatedFunction(scope) {
    const name = "templated";
    const fun = new lambda.Function(scope, "c", { handler: \`\${name}.lambda_handler\` });
    fun.addToRolePolicy(new iam.PolicyStatement({ actions: ["states:SendTaskFailure"] }));
    return fun;
}`;
        const granted = grantedHandlerStems(synthetic);
        expect(granted.has("granted")).toBe(true);
        expect(granted.has("ungranted")).toBe(false);
        // The template-literal spelling is what nearly every real builder uses. Omitting it from the
        // control is why the first version of this test reported grants that plainly existed.
        expect(granted.has("templated")).toBe(true);
        expect(granted.has("$" + "{name}")).toBe(false);
        expect(/SendTaskFailure/.test(synthetic)).toBe(true); // a file-level grep WOULD pass
    });

    it("every handler that reports a failure is granted states:SendTaskFailure BY ITS OWN builder", () => {
        // Paired PER PIPELINE, not globally. The first version of this test matched stems across every
        // builder file, and a mutation removing a real grant did not fail it -- because `constructPipeline`
        // is a stem many pipelines use, and the others do grant it. That is precisely the shape of the
        // defect (granted in siblings, missing here), so a global match cannot see it. Builder directories
        // mirror pipeline directories, which is what makes the pairing derivable; cosmos is one builder
        // directory serving four pipeline variants, so the match is on prefix rather than equality.
        const missing: string[] = [];
        for (const [pipeline, stems] of reporting) {
            const owningBuilders = builderFiles.filter((file) => {
                const rel = path
                    .relative(BUILDERS, file)
                    .split(path.sep)
                    .join("/")
                    .replace(/\/lambdaBuilder\/.*$/, "");
                return pipeline === rel || pipeline.startsWith(rel + "/");
            });
            if (owningBuilders.length === 0) {
                // No builder mirrors this pipeline's path. Reported rather than skipped: a silent skip is
                // how a pipeline drops out of coverage when its directory is renamed.
                missing.push(`${pipeline} -> NO BUILDER FOUND for path pairing`);
                continue;
            }
            const grantedHere = new Set<string>();
            for (const file of owningBuilders) {
                for (const stem of grantedHandlerStems(fs.readFileSync(file, "utf-8"))) {
                    grantedHere.add(stem);
                }
            }
            for (const stem of stems) {
                if (!grantedHere.has(stem)) missing.push(`${pipeline} -> ${stem}.py`);
            }
        }
        expect(missing.sort()).toEqual([]);
    });

    /** Each `new iam.PolicyStatement({...})` that mentions a task-token action, as source text. */
    function taskTokenStatements(text: string): string[] {
        const out: string[] = [];
        for (const m of text.matchAll(/new iam\.PolicyStatement\(\{/g)) {
            // Walk braces from the statement's opening `{` so a nested object cannot end the slice early.
            let depth = 0;
            let end = m.index! + m[0].length - 1;
            for (let i = end; i < text.length; i++) {
                if (text[i] === "{") depth++;
                else if (text[i] === "}") {
                    depth--;
                    if (depth === 0) {
                        end = i;
                        break;
                    }
                }
            }
            const block = text.slice(m.index!, end + 1);
            if (/states:SendTask(Failure|Success|Heartbeat)/.test(block)) out.push(block);
        }
        return out;
    }

    it("every task-token grant is scoped to this deployment's account and region", () => {
        // A task token names no state machine a builder can resolve, so the RESOURCE part is legitimately
        // a wildcard — but the partition/region/account prefix is not, and `resources: ["*"]` grants the
        // action against every state machine in every account the role can reach. One pipeline was written
        // that way while all its peers were scoped, so this pins the narrower form rather than leaving it
        // to review. Durable, not temporary: a broader wildcard is writable at any time.
        const unscoped: string[] = [];
        let examined = 0;
        for (const file of builderFiles) {
            const text = fs.readFileSync(file, "utf-8");
            for (const block of taskTokenStatements(text)) {
                examined++;
                const resources = block.match(/resources:\s*\[([\s\S]*?)\]/);
                const value = resources ? resources[1] : "";
                if (!/:states:/.test(value)) {
                    unscoped.push(
                        `${path.relative(REPO, file).split(path.sep).join("/")}: ${value
                            .trim()
                            .slice(0, 80)}`
                    );
                }
            }
        }
        expect(examined).toBeGreaterThanOrEqual(20); // control: an empty scan proves nothing
        expect(unscoped.sort()).toEqual([]);
    });

    it("the scope matcher recognises the unscoped form", () => {
        // Control for the assertion above, which is an empty-set check. Feeds it both spellings.
        const scoped = `
    fun.addToRolePolicy(new iam.PolicyStatement({
        actions: ["states:SendTaskFailure"],
        effect: iam.Effect.ALLOW,
        resources: [\`arn:\${ServiceHelper.Partition()}:states:\${config.env.region}:\${config.env.account}:*\`],
    }));`;
        const bare = `
    fun.addToRolePolicy(new iam.PolicyStatement({
        actions: ["states:SendTaskFailure"],
        effect: iam.Effect.ALLOW,
        resources: ["*"],
    }));`;
        const scopedBlocks = taskTokenStatements(scoped);
        const bareBlocks = taskTokenStatements(bare);
        expect(scopedBlocks).toHaveLength(1);
        expect(bareBlocks).toHaveLength(1);
        expect(/:states:/.test(scopedBlocks[0].match(/resources:\s*\[([\s\S]*?)\]/)![1])).toBe(
            true
        );
        expect(/:states:/.test(bareBlocks[0].match(/resources:\s*\[([\s\S]*?)\]/)![1])).toBe(false);
    });
});
