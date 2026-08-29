/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every Lambda builder's `handler: \`handlers.X.Y.lambda_handler\`` must name a module that exists at
 * `backend/backend/handlers/X/Y.py`.
 *
 * This is a SOURCE-level check, not a synth one, and the distinction is the whole point. A builder that
 * is declared but never called emits no CloudFormation, so a template assertion sees nothing and passes.
 * The mismatch then surfaces only when someone wires the builder to a route: the deploy succeeds and the
 * route returns 502 `Runtime.ImportModuleError` on first invocation. Nothing in CDK, TypeScript, or a
 * synth test relates the handler string to the Python tree.
 *
 * Scope is every `.ts` file under `lib/` — the 17 files in `lib/lambdaBuilder/` plus the builders that
 * live beside their nested stack (Garnet, Physna) and the one construct that builds a handler Lambda
 * inline (the Cognito pre-token-generation trigger).
 */

import * as fs from "fs";
import * as path from "path";

const LIB_ROOT = path.join(__dirname, "..", "lib");
const BACKEND_ROOT = path.join(__dirname, "..", "..", "backend", "backend");

/** Every `.ts` file under `lib/`, recursively. */
function tsFiles(dir: string): string[] {
    return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return tsFiles(full);
        return entry.isFile() && entry.name.endsWith(".ts") ? [full] : [];
    });
}

/**
 * Candidate string values for a `${identifier}` interpolation, read from the NEAREST PRECEDING `const`
 * declaration of that identifier. Each builder function declares its own `const name = "..."` just above
 * its handler string, so nearest-preceding is the enclosing scope. Scanning the whole file instead would
 * expand one handler string with every sibling builder's name — which turns a real mismatch into noise
 * and produces false unresolvables of its own.
 *
 * Both shapes in the tree are covered: a plain literal (`const name = "assetService";`) and a ternary
 * over two literals (`const handlerName = govCloud ? "pretokengenv1" : "pretokengenv2";`), where BOTH
 * branches must resolve.
 */
function candidateValues(lines: string[], upToLine: number, identifier: string): string[] {
    const literals = (rhs: string) =>
        [...rhs.matchAll(/["'`]([A-Za-z0-9_.-]+)["'`]/g)].map((m) => m[1]);

    const declaration = new RegExp(`\\bconst\\s+${identifier}\\s*=\\s*([^;]*);`);
    for (let i = upToLine; i >= 0; i--) {
        const match = declaration.exec(lines[i]);
        if (match) return literals(match[1]);
    }

    // Fallback for a shared builder that takes the module name as a prop and destructures it
    // (`buildCommonPhysnaLambda`): the values come from its call sites, so collect every
    // `identifier: "literal"` in the file. File-scoped, and every candidate must resolve.
    const property = new RegExp(`\\b${identifier}\\s*:\\s*["'\`][A-Za-z0-9_.-]+["'\`]`, "g");
    return [...lines.join("\n").matchAll(property)].flatMap((m) => literals(m[0]));
}

/** Expand `handlers.assets.${name}.lambda_handler` into one concrete string per candidate value. */
function expand(template: string, lines: string[], atLine: number): string[] {
    const interpolation = /\$\{([A-Za-z_][A-Za-z0-9_]*)\}/;
    const match = interpolation.exec(template);
    if (!match) return [template];
    const values = candidateValues(lines, atLine, match[1]);
    // An unresolvable interpolation is returned as-is rather than dropped, so it shows up as an
    // unresolvable module. Dropping it is how this kind of parser ends up finding nothing and passing.
    if (values.length === 0) return [template];
    return values.flatMap((value) => expand(template.replace(match[0], value), lines, atLine));
}

interface HandlerDeclaration {
    file: string;
    line: number;
    /** The handler string as written, interpolations resolved. */
    handler: string;
    /** `backend/backend/handlers/X/Y.py` for `handlers.X.Y.lambda_handler`. */
    modulePath: string;
}

/**
 * `handlers.X.Y.<entrypoint>` -> `backend/backend/handlers/X/Y.py`. The last dot-segment is the Python
 * function name, not part of the module path. `sqsBucketSync.lambda_handler_` (completed at runtime by
 * string concatenation) drops the same way, so the module still resolves.
 */
function modulePathOf(handler: string): string {
    const segments = handler.split(".");
    segments.pop();
    return path.join(BACKEND_ROOT, ...segments) + ".py";
}

/** Every `handler: \`handlers....\`` template literal under `lib/`, with `${...}` resolved. */
function collectHandlerDeclarations(): HandlerDeclaration[] {
    const declarations: HandlerDeclaration[] = [];
    for (const file of tsFiles(LIB_ROOT)) {
        const source = fs.readFileSync(file, "utf-8");
        const lines = source.split(/\r?\n/);
        lines.forEach((text, index) => {
            // Only the backtick form appears in the tree; a quoted form would be matched too.
            const match = /handler:\s*[`"'](handlers\.[^`"']*)[`"']/.exec(text);
            if (!match) return;
            for (const handler of expand(match[1], lines, index)) {
                declarations.push({
                    file: path.relative(path.join(__dirname, ".."), file).replace(/\\/g, "/"),
                    line: index + 1,
                    handler,
                    modulePath: modulePathOf(handler),
                });
            }
        });
    }
    return declarations;
}

describe("lambda builder handler strings resolve to real backend modules", () => {
    const declarations = collectHandlerDeclarations();

    test("the parser finds the builders it is meant to check", () => {
        // Positive control for every negative below. A parser that matched nothing — a changed quoting
        // style, a moved directory — would satisfy the "no unresolvable handler" assertion vacuously.
        expect(declarations.length).toBeGreaterThan(60);

        // The three families the scan must reach: lib/lambdaBuilder/, the nested-stack addon builders,
        // and the Cognito construct's inline handler Lambda.
        const files = new Set(declarations.map((d) => d.file));
        expect([...files].filter((f) => f.startsWith("lib/lambdaBuilder/")).length).toBeGreaterThan(
            15
        );
        expect(files).toContain(
            "lib/nestedStacks/addon/physna/lambdaBuilder/physnaSyncFunctions.ts"
        );
        expect(files).toContain(
            "lib/nestedStacks/addon/garnetFramework/lambdaBuilder/garnetIndexerFunctions.ts"
        );
        expect(files).toContain("lib/nestedStacks/auth/constructs/cognito-web-native-construct.ts");

        // Interpolations are resolved, not left as literal text — the check below is meaningless if
        // `${name}` reaches the filesystem.
        expect(declarations.filter((d) => d.handler.includes("${"))).toEqual([]);
    });

    test("every handler string resolves to a module on disk", () => {
        const unresolvable = declarations
            .filter((d) => !fs.existsSync(d.modulePath))
            .map((d) => `${d.file}:${d.line} -> ${d.handler}`);
        expect(unresolvable).toEqual([]);
    });

    test("a fabricated handler does not resolve, so the check above discriminates", () => {
        // Control for the resolver itself: proves existsSync is reached and can return false, rather
        // than every path accidentally resolving (e.g. a modulePathOf that produced a directory).
        expect(fs.existsSync(modulePathOf("handlers.assets.assetService.lambda_handler"))).toBe(
            true
        );
        expect(fs.existsSync(modulePathOf("handlers.assets.noSuchModule.lambda_handler"))).toBe(
            false
        );
        // The exact string FIX-031 deleted. Restoring the builder must fail the test above, so the
        // module it named has to stay unresolvable.
        expect(
            fs.existsSync(
                modulePathOf("handlers.assetLinks.assetLinksMetadataService.lambda_handler")
            )
        ).toBe(false);
    });

    test("the assetLinks builders name exactly the two modules that exist", () => {
        const assetLinkHandlers = declarations
            .filter((d) => d.handler.startsWith("handlers.assetLinks."))
            .map((d) => d.handler)
            .sort();
        expect(assetLinkHandlers).toEqual([
            "handlers.assetLinks.assetLinksService.lambda_handler",
            "handlers.assetLinks.createAssetLink.lambda_handler",
        ]);
    });
});
