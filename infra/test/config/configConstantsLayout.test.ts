/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * config.ts, and each add-on configuration module under config/addon/, declares every module-level
 * constant at the top of the file, under its two constant sections: the constants that shape the
 * deployment, then the constants used only to check configuration. A constant declared beside the
 * function that reads it is found only by searching and falls outside both sections, so each one
 * declared below the first function, interface or type is reported by name. A constant lib/ or bin/
 * reads reaches synthesized infrastructure, so one declared in the checks section is reported by name
 * too.
 */
import * as fs from "fs";
import * as path from "path";
import * as ts from "typescript";

const INFRA = path.join(__dirname, "..", "..");
const CONFIG_TS = path.join(INFRA, "config", "config.ts");
const ADDON_CONFIG_DIR = path.join(INFRA, "config", "addon");
/** config.ts and the add-on configuration modules it composes. */
const CONFIG_MODULES = [
    CONFIG_TS,
    ...fs
        .readdirSync(ADDON_CONFIG_DIR)
        .filter((name) => name.endsWith(".ts"))
        .sort()
        .map((name) => path.join(ADDON_CONFIG_DIR, name)),
];
const DEPLOYMENT_HEADER = "// Constants that shape the deployment";
const CHECKS_HEADER = "// Constants used only to check configuration";
/** The trees whose imports from the configuration modules reach synthesized infrastructure. */
const READER_DIRS = [path.join(INFRA, "lib"), path.join(INFRA, "bin")];
const relative = (file: string): string => path.relative(INFRA, file).split(path.sep).join("/");
/** The configuration modules that declare constants, which the section-header checks apply to. */
const MODULES_WITH_CONSTANTS = CONFIG_MODULES.filter(
    (file) => parse(file).statements.filter(ts.isVariableStatement).length > 0
).map(relative);

function parse(file: string): { text: string; statements: readonly ts.Statement[] } {
    const text = fs.readFileSync(file, "utf8");
    const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
    return { text, statements: source.statements };
}

function constantNames(statements: readonly ts.Statement[]): string[] {
    return statements
        .filter(ts.isVariableStatement)
        .flatMap((s) => s.declarationList.declarations.map((d) => d.name.getText()));
}

/** Every TypeScript source file under dir. */
function tsFiles(dir: string): string[] {
    return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return entry.name === "node_modules" ? [] : tsFiles(full);
        return entry.name.endsWith(".ts") && !entry.name.endsWith(".d.ts") ? [full] : [];
    });
}

/** The module's names lib/ and bin/ read: each named import, and each member a namespace import reads. */
function namesReadFrom(module: string): Set<string> {
    const target = module.replace(/\.ts$/, "");
    const marker = path.relative(INFRA, target).split(path.sep).join("/");
    const names = new Set<string>();
    for (const file of READER_DIRS.flatMap(tsFiles)) {
        const text = fs.readFileSync(file, "utf8");
        if (!text.includes(marker)) continue;
        const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
        const namespaces = new Set<string>();
        for (const statement of source.statements) {
            if (
                !ts.isImportDeclaration(statement) ||
                !ts.isStringLiteral(statement.moduleSpecifier)
            )
                continue;
            if (path.resolve(path.dirname(file), statement.moduleSpecifier.text) !== target)
                continue;
            const bindings = statement.importClause?.namedBindings;
            if (bindings && ts.isNamespaceImport(bindings)) namespaces.add(bindings.name.text);
            else bindings?.elements.forEach((e) => names.add((e.propertyName ?? e.name).text));
        }
        const visit = (node: ts.Node): void => {
            if (
                ts.isPropertyAccessExpression(node) &&
                ts.isIdentifier(node.expression) &&
                namespaces.has(node.expression.text)
            )
                names.add(node.name.text);
            ts.forEachChild(node, visit);
        };
        if (namespaces.size > 0) visit(source);
    }
    return names;
}

describe("config.ts constant layout", () => {
    test("the configuration modules include config.ts and the add-on lane (control)", () => {
        const modules = CONFIG_MODULES.map(relative);
        expect(modules).toContain("config/config.ts");
        expect(modules).toContain("config/addon/addonConfig.ts");
    });

    test.each(CONFIG_MODULES.map(relative))(
        "%s declares every module-level constant before the first function, interface or type",
        (rel) => {
            const { statements } = parse(path.join(INFRA, rel));
            const first = statements.findIndex(
                (s) =>
                    ts.isFunctionDeclaration(s) ||
                    ts.isInterfaceDeclaration(s) ||
                    ts.isTypeAliasDeclaration(s) ||
                    ts.isClassDeclaration(s) ||
                    ts.isEnumDeclaration(s)
            );
            expect(first).toBeGreaterThan(0);
            expect(constantNames(statements.slice(first))).toEqual([]);
        }
    );

    test.each(MODULES_WITH_CONSTANTS)(
        "%s keeps its constants under the two section headers, the deployment section first",
        (rel) => {
            const { text, statements } = parse(path.join(INFRA, rel));
            const deployment = text.indexOf(DEPLOYMENT_HEADER);
            const checks = text.indexOf(CHECKS_HEADER);
            expect(deployment).toBeGreaterThan(0);
            expect(checks).toBeGreaterThan(deployment);
            const constants = statements.filter(ts.isVariableStatement);
            expect(constantNames(constants.filter((s) => s.getStart() < deployment))).toEqual([]);
        }
    );

    test("the layout checks see the constants (control)", () => {
        // A parse that found no constants would satisfy the layout tests vacuously.
        const counts = CONFIG_MODULES.map(
            (file) => parse(file).statements.filter(ts.isVariableStatement).length
        );
        expect(counts[0]).toBeGreaterThan(20);
        expect(counts.reduce((sum, n) => sum + n, 0)).toBeGreaterThan(40);
        expect(MODULES_WITH_CONSTANTS.length).toBeGreaterThan(1);
    });

    test.each(MODULES_WITH_CONSTANTS)(
        "every constant lib/ or bin/ reads from %s is declared in its deployment section",
        (rel) => {
            const file = path.join(INFRA, rel);
            const { text, statements } = parse(file);
            const checks = text.indexOf(CHECKS_HEADER);
            expect(checks).toBeGreaterThan(0);
            const read = namesReadFrom(file);
            const constants = statements.filter(ts.isVariableStatement);
            const late = constantNames(constants.filter((s) => s.getStart() > checks));
            expect(late.filter((name) => read.has(name))).toEqual([]);
        }
    );

    test("the reader walk matches the imports of config.ts (control)", () => {
        // A walk that matched no import would leave nothing to place.
        const { statements } = parse(CONFIG_TS);
        const read = namesReadFrom(CONFIG_TS);
        const readByInfrastructure = constantNames(
            statements.filter(ts.isVariableStatement)
        ).filter((name) => read.has(name));
        expect(readByInfrastructure).toContain("LAMBDA_PYTHON_RUNTIME");
        expect(readByInfrastructure.length).toBeGreaterThan(10);
    });
});
