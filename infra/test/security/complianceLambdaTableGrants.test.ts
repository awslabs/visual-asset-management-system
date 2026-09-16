/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every DynamoDB table a compliance Lambda's code can reach is granted on that Lambda's execution
 * role, with write actions wherever the code writes through the table.
 *
 * The compliance handlers resolve their tables through `ResourceKeys` (`common/resourceNames.py`)
 * and share one store module (`complianceEvaluationStore.py`) that resolves every table an
 * evaluation needs at import and exposes them through functions. A handler therefore reaches tables in three ways: its own
 * `dynamodb.Table(...)` bindings, aliases of the store's bindings (`asset_state_table =
 * store.asset_state_table`), and the store functions it calls. A grant missed on any of the three
 * is invisible to every unit test (they stub DynamoDB) and to synth (nothing relates a Python module
 * to an IAM statement); it surfaces as an `AccessDeniedException` on a live request, in one branch
 * of one handler, and turns a completed mutation into a 500 after the fact.
 *
 * So the needs are DERIVED FROM THE PYTHON SOURCE rather than listed here. For each compliance Lambda
 * the analysis walks its handler module and, function by function, the compliance and common
 * compliance modules it imports — following `alias.function(...)` calls into the store, symbol imports
 * such as `check_and_trigger_cascade`, intra-module calls, and the store's `update_item(table, ...)`
 * helper, whose writes belong to the table passed in. Reachability is per FUNCTION, not per module:
 * a Lambda that calls only `store.write_audit` needs the audit table, not every table the store binds
 * at import (`dynamodb.Table(name)` is a lazy handle and performs no call), so the cascade API — which
 * hands each cascade to the executor Lambda — is held to the three tables it touches itself.
 *
 * Each `ResourceKeys` name is resolved to the table CloudFormation created by following the same
 * registry the runtime uses: `ResourceKeys.X` → its SSM parameter key → the parameter the
 * ResourceNames stack emits → the storage stack Output it references → the table's logical id. The
 * role policies are then read from the synthesized commercial template through the cross-stack
 * parameters that carry the table ARNs into the API stack, so the assertion is on what deploys.
 *
 * The reverse direction is asserted for writes: no compliance Lambda holds a write grant on a table
 * its code only reads. The store's bindings make it easy to grant a handler `grantReadWriteData` on
 * every table the store knows, and a read-only handler with write actions is the privilege an
 * injected or mistaken code path would exploit.
 */

import * as fs from "fs";
import * as path from "path";
import { SynthResult, synthTemplate } from "../support/templateSynth";

// Full-app synth from a config template costs ~20 s and the harness caches one result per template.
jest.setTimeout(600_000);

const BACKEND_ROOT = path.resolve(__dirname, "..", "..", "..", "backend", "backend");
const RESOURCE_NAMES_PY = path.join(BACKEND_ROOT, "common", "resourceNames.py");

/** Python packages whose modules the reachability walk follows into. */
const FOLLOWED_PACKAGES = ["handlers.compliance", "common.compliance"];

/** `boto3` Table methods that mutate rows. Any other reference to a table is a read. */
const WRITE_METHODS = new Set([
    "put_item",
    "update_item",
    "delete_item",
    "batch_writer",
    "batch_write_item",
]);

const READ_ACTIONS = [
    "dynamodb:GetItem",
    "dynamodb:Query",
    "dynamodb:Scan",
    "dynamodb:BatchGetItem",
];
const WRITE_ACTIONS = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"];
const BATCH_WRITE_ACTION = "dynamodb:BatchWriteItem";

/** Tables `setupSecurityAndLoggingEnvironmentAndPermissions` grants on every handler role. */
const SECURITY_HELPER_TABLES = new Set([
    "CONSTRAINTS_STORAGE_TABLE",
    "USER_ROLES_STORAGE_TABLE",
    "ROLES_STORAGE_TABLE",
]);

// ---------------------------------------------------------------------------------------------
// Python source model
// ---------------------------------------------------------------------------------------------

interface PyFunction {
    name: string;
    params: string[];
    body: string;
}

interface PyModule {
    /** Dotted module id, e.g. `handlers.compliance.complianceEvaluationStore`. */
    id: string;
    /** Variable name → `ResourceKeys` member, for table names and table handles alike. */
    tableVars: Map<string, string>;
    functions: Map<string, PyFunction>;
    /** Top-level statements with the table bindings removed. */
    moduleCode: string;
    /** Local name → module id, from `from <package> import <module> [as <name>]`. */
    aliases: Map<string, string>;
    /** Local name → function, from `from <module> import <name>`. */
    symbols: Map<string, { module: string; name: string }>;
    /** Alias bindings (`x = store.y`) resolved once every module is parsed. */
    pendingAliases: Array<{ variable: string; alias: string; attribute: string }>;
}

/** Strip docstrings and comments; a `#` inside a string literal is kept. */
function pythonCode(source: string): string {
    return source
        .replace(/"""[\s\S]*?"""/g, '""')
        .replace(/'''[\s\S]*?'''/g, "''")
        .split("\n")
        .map((line) => {
            const hash = line.indexOf("#");
            if (hash === -1) return line;
            const before = line.slice(0, hash);
            const doubleQuotes = (before.match(/"/g) || []).length;
            const singleQuotes = (before.match(/'/g) || []).length;
            return doubleQuotes % 2 === 0 && singleQuotes % 2 === 0 ? before : line;
        })
        .join("\n");
}

function modulePath(id: string): string {
    return path.join(BACKEND_ROOT, ...id.split(".")) + ".py";
}

function isFollowed(id: string): boolean {
    return (
        FOLLOWED_PACKAGES.some((pkg) => id.startsWith(`${pkg}.`)) && fs.existsSync(modulePath(id))
    );
}

const CLOSING: Record<string, string> = { "(": ")", "[": "]", "{": "}" };

/** The text between the bracket at `open` and its match. */
function balanced(code: string, open: number): { inner: string; end: number } {
    const opening = code[open];
    const closing = CLOSING[opening];
    let depth = 0;
    for (let i = open; i < code.length; i++) {
        if (code[i] === opening) depth++;
        else if (code[i] === closing) {
            depth--;
            if (depth === 0) return { inner: code.slice(open + 1, i), end: i };
        }
    }
    throw new Error(`unbalanced ${opening} at ${open}`);
}

function parseModule(id: string): PyModule {
    const code = pythonCode(fs.readFileSync(modulePath(id), "utf-8"))
        // Collapse a parenthesized import list onto one line.
        .replace(
            /from\s+([\w.]+)\s+import\s+\(([^)]*)\)/g,
            (_m, mod, names) => `from ${mod} import ${names.replace(/\s+/g, " ").trim()}`
        );

    const aliases = new Map<string, string>();
    const symbols = new Map<string, { module: string; name: string }>();
    for (const match of code.matchAll(/^\s*from\s+([\w.]+)\s+import\s+([^\n]+)$/gm)) {
        const [, from, names] = match;
        for (const entry of names.split(",")) {
            const [name, alias] = entry
                .trim()
                .split(/\s+as\s+/)
                .map((s) => s.trim());
            if (!name) continue;
            if (isFollowed(`${from}.${name}`)) {
                aliases.set(alias ?? name, `${from}.${name}`);
            } else if (isFollowed(from)) {
                symbols.set(alias ?? name, { module: from, name });
            }
        }
    }

    // Top-level blocks: a block starts at a column-0 line and runs through the indented and blank
    // lines that follow it, and through a column-0 line that only closes a bracket the block opened.
    const blocks: string[] = [];
    for (const line of code.split("\n")) {
        const opensBlock = /^\S/.test(line) && !/^[)\]}]/.test(line);
        if (opensBlock || blocks.length === 0) blocks.push(line);
        else blocks[blocks.length - 1] += `\n${line}`;
    }

    const functions = new Map<string, PyFunction>();
    let moduleCode = "";
    for (const block of blocks) {
        const def = /^(?:async\s+)?def\s+(\w+)\s*\(/.exec(block);
        if (!def) {
            moduleCode += `${block}\n`;
            continue;
        }
        const { inner } = balanced(block, def[0].length - 1);
        const params = inner
            .split(",")
            .map((p) => p.trim().replace(/^\*+/, "").split(/[:=]/)[0].trim())
            .filter(Boolean);
        functions.set(def[1], { name: def[1], params, body: block });
    }

    const tableVars = new Map<string, string>();
    const nameVars = new Map<string, string>();
    const bindings: RegExp[] = [
        /(\w+)\s*=\s*\(?\s*get_table_name\(\s*ResourceKeys\.(\w+)\s*\)/g,
        /(\w+)\s*=\s*\(?\s*\w+\.Table\(\s*(\w+)\s*\)/g,
    ];
    for (const match of moduleCode.matchAll(bindings[0])) {
        nameVars.set(match[1], match[2]);
        tableVars.set(match[1], match[2]);
    }
    for (const match of moduleCode.matchAll(bindings[1])) {
        const key = nameVars.get(match[2]);
        if (key) tableVars.set(match[1], key);
    }
    const pendingAliases: PyModule["pendingAliases"] = [];
    const aliasBinding = /^(\w+)\s*=\s*(\w+)\.(\w+)\s*$/gm;
    for (const match of moduleCode.matchAll(aliasBinding)) {
        if (aliases.has(match[2])) {
            pendingAliases.push({ variable: match[1], alias: match[2], attribute: match[3] });
        }
    }
    for (const pattern of [...bindings, aliasBinding]) {
        moduleCode = moduleCode.replace(pattern, "");
    }

    return { id, tableVars, functions, moduleCode, aliases, symbols, pendingAliases };
}

const moduleCache = new Map<string, PyModule>();

/** The module and, transitively, every followed module it imports. */
function loadModule(id: string): PyModule {
    const hit = moduleCache.get(id);
    if (hit) return hit;
    const mod = parseModule(id);
    moduleCache.set(id, mod);
    for (const target of mod.aliases.values()) loadModule(target);
    for (const { module } of mod.symbols.values()) loadModule(module);
    for (const pending of mod.pendingAliases) {
        const key = loadModule(mod.aliases.get(pending.alias)!).tableVars.get(pending.attribute);
        if (key) mod.tableVars.set(pending.variable, key);
    }
    return mod;
}

// ---------------------------------------------------------------------------------------------
// Reachability: which tables a handler's code touches, and how
// ---------------------------------------------------------------------------------------------

interface TableNeed {
    write: boolean;
    batchWrite: boolean;
    index: boolean;
}

type Needs = Map<string, TableNeed>;

function need(needs: Needs, key: string): TableNeed {
    let entry = needs.get(key);
    if (!entry) {
        entry = { write: false, batchWrite: false, index: false };
        needs.set(key, entry);
    }
    return entry;
}

function recordMethods(entry: TableNeed, methods: string[]): void {
    for (const method of methods) {
        if (WRITE_METHODS.has(method)) entry.write = true;
        if (method === "batch_writer" || method === "batch_write_item") entry.batchWrite = true;
    }
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Methods called on `table` inside a function whose first parameter is a table handle. */
function methodsOnTableParameter(fn: PyFunction): string[] {
    if (fn.params[0] !== "table") return [];
    return [...fn.body.matchAll(/(?<![\w.])table\.(\w+)\(/g)].map((m) => m[1]);
}

interface PyCall {
    /** The dotted callee, e.g. `store.write_audit` or `asset_state_table.query`. */
    callee: string;
    /** The argument text between the call's parentheses. */
    args: string;
}

/** Every call expression in a body, by callee and argument text. */
function callsIn(body: string): PyCall[] {
    const calls: PyCall[] = [];
    for (const match of body.matchAll(/(?<![\w.])((?:\w+\.)*\w+)\s*\(/g)) {
        try {
            const { inner } = balanced(body, match.index! + match[0].length - 1);
            calls.push({ callee: match[1], args: inner });
        } catch {
            // A parenthesis inside a string literal; the call is not a table access.
        }
    }
    return calls;
}

/**
 * Whether a call queries a secondary index: `IndexName` among its arguments, or a `**kwargs` splat
 * of a dict the body builds with an `IndexName` entry.
 */
function queriesIndex(call: PyCall, body: string): boolean {
    if (/\bIndexName\b/.test(call.args)) return true;
    for (const splat of call.args.matchAll(/\*\*(\w+)/g)) {
        const name = escape(splat[1]);
        for (const literal of body.matchAll(new RegExp(`(?<![\\w.])${name}\\s*=\\s*\\{`, "g"))) {
            const { inner } = balanced(body, literal.index! + literal[0].length - 1);
            if (/\bIndexName\b/.test(inner)) return true;
        }
        if (new RegExp(`(?<![\\w.])${name}\\[\\s*["']IndexName["']\\s*\\]`).test(body)) return true;
    }
    return false;
}

/** Every table a handler module's code can reach, with whether it writes and queries an index. */
function deriveNeeds(handlerModule: string): Needs {
    const needs: Needs = new Map();
    const root = loadModule(handlerModule);
    const visited = new Set<string>();
    const queue: Array<{ mod: PyModule; body: string }> = [{ mod: root, body: root.moduleCode }];
    const enqueue = (mod: PyModule, fnName: string) => {
        const fn = mod.functions.get(fnName);
        if (!fn || visited.has(`${mod.id}::${fnName}`)) return;
        visited.add(`${mod.id}::${fnName}`);
        queue.push({ mod, body: fn.body });
    };
    for (const fnName of root.functions.keys()) enqueue(root, fnName);

    /** The function a dotted callee names: local, imported by symbol, or through a module alias. */
    const resolveFunction = (
        mod: PyModule,
        callee: string
    ): { module: PyModule; fn: PyFunction } | undefined => {
        const [head, tail] = callee.split(".");
        if (tail) {
            const target = mod.aliases.get(head);
            const module = target ? moduleCache.get(target) : undefined;
            const fn = module?.functions.get(tail);
            return module && fn ? { module, fn } : undefined;
        }
        const symbol = mod.symbols.get(head);
        if (symbol) {
            const module = moduleCache.get(symbol.module)!;
            const fn = module.functions.get(symbol.name);
            return fn ? { module, fn } : undefined;
        }
        const fn = mod.functions.get(head);
        return fn ? { module: mod, fn } : undefined;
    };

    /** Every table expression visible in `mod`: its own variables and `alias.variable` forms. */
    const tableExpressions = (mod: PyModule): Map<string, string> => {
        const expressions = new Map(mod.tableVars);
        for (const [alias, target] of mod.aliases) {
            for (const [variable, key] of moduleCache.get(target)!.tableVars) {
                expressions.set(`${alias}.${variable}`, key);
            }
        }
        return expressions;
    };

    while (queue.length > 0) {
        const { mod, body } = queue.shift()!;
        const tables = tableExpressions(mod);
        const calls = callsIn(body);

        for (const [expression, key] of tables) {
            if (new RegExp(`(?<![\\w.])${escape(expression)}\\b`).test(body)) {
                need(needs, key);
            }
        }

        for (const call of calls) {
            const method = /^(.+)\.(\w+)$/.exec(call.callee);
            const receiverKey = method ? tables.get(method[1]) : undefined;
            if (receiverKey) {
                const entry = need(needs, receiverKey);
                recordMethods(entry, [method![2]]);
                if (queriesIndex(call, body)) entry.index = true;
                continue;
            }

            const target = resolveFunction(mod, call.callee);
            if (target) enqueue(target.module, target.fn.name);

            // A table handle passed as an argument: the callee reads it, queries its index when
            // the call names one, and — for a helper whose first parameter is `table` — performs
            // the helper's own calls on it (`update_item(asset_state_table, ...)`).
            for (const [expression, key] of tables) {
                const asArgument = new RegExp(`(?<![\\w.])${escape(expression)}\\s*(?:,|$)`);
                if (!asArgument.test(call.args)) continue;
                const entry = need(needs, key);
                if (queriesIndex(call, body)) entry.index = true;
                if (target) recordMethods(entry, methodsOnTableParameter(target.fn));
            }
        }
    }
    return needs;
}

// ---------------------------------------------------------------------------------------------
// Synthesized template: ResourceKeys → table logical id, and the role policies that name it
// ---------------------------------------------------------------------------------------------

/** `ResourceKeys` member → SSM parameter key suffix, e.g. `dynamoTables/complianceAuditStorage`. */
function resourceKeyParamKeys(): Map<string, string> {
    const source = fs.readFileSync(RESOURCE_NAMES_PY, "utf-8");
    const keys = new Map<string, string>();
    for (const match of source.matchAll(/^\s+(\w+)\s*=\s*ResourceParamKey\("([^"]+)"/gm)) {
        keys.set(match[1], match[2]);
    }
    return keys;
}

interface GrantedTable {
    actions: Set<string>;
    indexActions: Set<string>;
}

interface ComplianceLambda {
    handlerModule: string;
    stack: string;
    logicalId: string;
    roleLogicalId: string;
}

/** The cross-stack wiring of one synth, indexed for table resolution in both directions. */
class TableResolver {
    /** Nested-stack Parameter name → the storage Output it is fed from. */
    private readonly parameterToOutput = new Map<string, string>();
    /** Storage Output name → table logical id, for `Ref` and `Arn` outputs alike. */
    private readonly outputToTable = new Map<string, string>();
    private readonly paramKeys = resourceKeyParamKeys();

    constructor(private readonly synth: SynthResult) {
        const rootKey = Object.keys(synth.templates).find((k) => !k.endsWith(".nested"))!;
        for (const resource of Object.values<any>(synth.templates[rootKey].Resources ?? {})) {
            if (resource.Type !== "AWS::CloudFormation::Stack") continue;
            for (const [name, value] of Object.entries<any>(
                resource.Properties?.Parameters ?? {}
            )) {
                const attribute = value?.["Fn::GetAtt"]?.[1];
                if (typeof attribute === "string" && attribute.startsWith("Outputs.")) {
                    this.parameterToOutput.set(name, attribute.slice("Outputs.".length));
                }
            }
        }
        for (const template of Object.values<any>(synth.templates)) {
            const tables = new Set(
                Object.entries<any>(template.Resources ?? {})
                    .filter(([, r]) => r.Type === "AWS::DynamoDB::Table")
                    .map(([id]) => id)
            );
            for (const [name, output] of Object.entries<any>(template.Outputs ?? {})) {
                const target = output.Value?.Ref ?? output.Value?.["Fn::GetAtt"]?.[0];
                if (tables.has(target)) this.outputToTable.set(name, target);
            }
        }
    }

    /** The table logical id a `ResourceKeys` member resolves to through the SSM registry. */
    tableFor(resourceKey: string): string {
        const paramKey = this.paramKeys.get(resourceKey);
        if (!paramKey) throw new Error(`${resourceKey} is not a ResourceKeys member`);
        const parameter = this.synth
            .ofType("AWS::SSM::Parameter")
            .find((p) => SynthResult.flatten(p.properties.Name ?? "").endsWith(`/${paramKey}`));
        if (!parameter) throw new Error(`no SSM parameter published for ${paramKey}`);
        const output = this.parameterToOutput.get(parameter.properties.Value?.Ref);
        const table = output ? this.outputToTable.get(output) : undefined;
        if (!table) throw new Error(`${paramKey} does not resolve to a DynamoDB table`);
        return table;
    }

    /** The table logical id (and whether the index ARN) behind one statement Resource entry. */
    tableOfResource(entry: unknown): { table: string; index: boolean } | undefined {
        const match = /^\$\{(\w+)\}(\/index\/\*)?$/.exec(SynthResult.flatten(entry));
        if (!match) return undefined;
        const output = this.parameterToOutput.get(match[1]);
        const table = output ? this.outputToTable.get(output) : undefined;
        return table ? { table, index: match[2] !== undefined } : undefined;
    }

    complianceLambdas(): ComplianceLambda[] {
        return this.synth
            .ofType("AWS::Lambda::Function")
            .map((fn) => ({
                fn,
                handler: /^handlers\.compliance\.(\w+)\.lambda_handler$/.exec(
                    String(fn.properties.Handler ?? "")
                ),
            }))
            .filter(({ handler }) => handler !== null)
            .map(({ fn, handler }) => ({
                handlerModule: `handlers.compliance.${handler![1]}`,
                stack: fn.stack,
                logicalId: fn.logicalId,
                roleLogicalId: fn.properties.Role?.["Fn::GetAtt"]?.[0],
            }))
            .sort((a, b) => a.handlerModule.localeCompare(b.handlerModule));
    }

    /** Every table the Lambda's role can act on, with the actions granted on the table and its indexes. */
    grantedTables(lambda: ComplianceLambda): Map<string, GrantedTable> {
        const statements: any[] = [];
        for (const policy of this.synth.resources) {
            if (policy.stack !== lambda.stack) continue;
            if (/IAM::(Policy|ManagedPolicy)$/.test(policy.type)) {
                const roles = ((policy.properties as any).Roles ?? []) as unknown[];
                if (roles.some((ref) => JSON.stringify(ref).includes(lambda.roleLogicalId))) {
                    statements.push(
                        ...((policy.properties as any).PolicyDocument?.Statement ?? [])
                    );
                }
            }
            if (policy.type === "AWS::IAM::Role" && policy.logicalId === lambda.roleLogicalId) {
                for (const doc of ((policy.properties as any).Policies ?? []) as any[]) {
                    statements.push(...(doc.PolicyDocument?.Statement ?? []));
                }
            }
        }
        const granted = new Map<string, GrantedTable>();
        for (const statement of statements) {
            if (statement.Effect !== "Allow") continue;
            const actions = ([] as string[]).concat(statement.Action ?? []);
            for (const entry of ([] as unknown[]).concat(statement.Resource ?? [])) {
                const resolved = this.tableOfResource(entry);
                if (!resolved) continue;
                let table = granted.get(resolved.table);
                if (!table) {
                    table = { actions: new Set(), indexActions: new Set() };
                    granted.set(resolved.table, table);
                }
                const target = resolved.index ? table.indexActions : table.actions;
                actions.forEach((a) => target.add(a));
            }
        }
        return granted;
    }
}

/** The grants `needs` requires that `granted` does not carry, one line per gap. */
function coverageGaps(
    resolver: TableResolver,
    needs: Needs,
    granted: Map<string, GrantedTable>
): string[] {
    const gaps: string[] = [];
    for (const [key, requirement] of [...needs].sort(([a], [b]) => a.localeCompare(b))) {
        const table = resolver.tableFor(key);
        const grant = granted.get(table);
        const missing = (actions: string[], set: Set<string> | undefined) =>
            actions.filter((a) => !set?.has(a));
        for (const action of missing(READ_ACTIONS, grant?.actions)) {
            gaps.push(`${key}: ${action} is not granted on the table`);
        }
        if (requirement.index) {
            for (const action of missing(READ_ACTIONS, grant?.indexActions)) {
                gaps.push(`${key}: ${action} is not granted on the table's indexes`);
            }
        }
        if (requirement.write) {
            for (const action of missing(WRITE_ACTIONS, grant?.actions)) {
                gaps.push(`${key}: ${action} is not granted on the table`);
            }
        }
        if (requirement.batchWrite && !grant?.actions.has(BATCH_WRITE_ACTION)) {
            gaps.push(`${key}: ${BATCH_WRITE_ACTION} is not granted on the table`);
        }
    }
    return gaps;
}

interface SurplusGrant {
    table: string;
    kind: "unreachable" | "write";
    detail: string;
}

/** Grants `granted` carries beyond what `needs` requires. */
function surplusGrants(
    resolver: TableResolver,
    needs: Needs,
    granted: Map<string, GrantedTable>
): SurplusGrant[] {
    const expected = new Map<string, TableNeed>();
    for (const [key, requirement] of needs) expected.set(resolver.tableFor(key), requirement);
    const exempt = new Set([...SECURITY_HELPER_TABLES].map((key) => resolver.tableFor(key)));

    const surplus: SurplusGrant[] = [];
    for (const [table, grant] of [...granted].sort(([a], [b]) => a.localeCompare(b))) {
        if (exempt.has(table)) continue;
        const requirement = expected.get(table);
        if (!requirement) {
            surplus.push({
                table,
                kind: "unreachable",
                detail: `${table}: granted but unreachable from the handler`,
            });
            continue;
        }
        const writes = [...WRITE_ACTIONS, BATCH_WRITE_ACTION].filter((a) => grant.actions.has(a));
        if (!requirement.write && writes.length > 0) {
            surplus.push({
                table,
                kind: "write",
                detail: `${table}: ${writes.join(", ")} granted but the handler only reads it`,
            });
        }
    }
    return surplus;
}

// ---------------------------------------------------------------------------------------------

describe("compliance Lambda table grants cover the tables the handlers reach", () => {
    let synth: SynthResult;
    let resolver: TableResolver;
    let lambdas: ComplianceLambda[];

    const lambdaFor = (name: string): ComplianceLambda =>
        lambdas.find((l) => l.handlerModule === `handlers.compliance.${name}`)!;

    beforeAll(() => {
        synth = synthTemplate("commercial");
        resolver = new TableResolver(synth);
        lambdas = resolver.complianceLambdas();
    });

    test("the synth carries the nine compliance Lambdas and every derived table resolves", () => {
        // Positive control for every per-Lambda assertion: an empty Lambda list or an unresolvable
        // key would otherwise leave nothing to compare.
        expect(lambdas.map((l) => l.handlerModule)).toEqual([
            "handlers.compliance.complianceAuditService",
            "handlers.compliance.complianceCascadeExecutor",
            "handlers.compliance.complianceCascadeService",
            "handlers.compliance.complianceEvaluateService",
            "handlers.compliance.complianceQuarantineService",
            "handlers.compliance.complianceSchemaBindingService",
            "handlers.compliance.complianceSchemaService",
            "handlers.compliance.complianceTrigger",
            "handlers.compliance.complianceWorkflowCallback",
        ]);
        for (const lambda of lambdas) {
            expect(lambda.roleLogicalId).toBeDefined();
            const needs = deriveNeeds(lambda.handlerModule);
            expect(needs.size).toBeGreaterThan(0);
            for (const key of needs.keys()) {
                expect(resolver.tableFor(key)).toMatch(/StorageTable/);
            }
        }
    });

    test("the analysis sees the store's tables through every access shape", () => {
        // A handler reaches the store's tables by alias (`asset_state_table = store.asset_state_table`),
        // by store function, and through the `update_item(table, ...)` helper; a table passed to
        // `query_all_items` with an `IndexName` is an index read. Each shape is pinned on a handler
        // that uses it, so a parser regression cannot pass by deriving nothing.
        const quarantine = deriveNeeds("handlers.compliance.complianceQuarantineService");
        expect(quarantine.get("COMPLIANCE_ASSET_STATE_STORAGE_TABLE")).toMatchObject({
            write: true,
            index: true,
        });
        expect(quarantine.get("COMPLIANCE_AUDIT_STORAGE_TABLE")).toMatchObject({ write: true });
        expect(quarantine.get("ASSET_STORAGE_TABLE")).toMatchObject({ write: false });

        const cascade = deriveNeeds("handlers.compliance.complianceCascadeService");
        expect([...cascade.keys()].sort()).toEqual([
            "ASSET_STORAGE_TABLE",
            "COMPLIANCE_AUDIT_STORAGE_TABLE",
            "COMPLIANCE_CASCADE_STORAGE_TABLE",
        ]);
        expect(cascade.get("ASSET_STORAGE_TABLE")).toMatchObject({ write: false, index: false });
        expect(cascade.get("COMPLIANCE_CASCADE_STORAGE_TABLE")).toMatchObject({
            write: true,
            index: true,
        });

        const schema = deriveNeeds("handlers.compliance.complianceSchemaService");
        expect(schema.get("COMPLIANCE_AUDIT_STORAGE_TABLE")).toMatchObject({ write: true });
        expect(schema.get("COMPLIANCE_SCHEMA_STORAGE_TABLE")).toMatchObject({
            write: true,
            batchWrite: true,
            index: true,
        });
        expect(schema.get("DATABASE_STORAGE_TABLE")).toMatchObject({ write: false, index: false });

        const callback = deriveNeeds("handlers.compliance.complianceWorkflowCallback");
        expect(callback.get("COMPLIANCE_EVALUATION_STORAGE_TABLE")).toMatchObject({
            write: true,
            index: true,
        });
        expect(callback.get("PIPELINE_EXECUTIONS_STORAGE_TABLE")).toMatchObject({
            write: false,
            index: true,
        });
        expect(callback.get("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE")).toMatchObject({
            write: false,
            index: false,
        });
        expect(callback.get("ASSET_STORAGE_TABLE")).toMatchObject({ write: false });
    });

    test("every compliance Lambda is granted every table its code reaches", () => {
        const gaps = lambdas.flatMap((lambda) =>
            coverageGaps(
                resolver,
                deriveNeeds(lambda.handlerModule),
                resolver.grantedTables(lambda)
            ).map((gap) => `${lambda.handlerModule}: ${gap}`)
        );
        expect(gaps).toEqual([]);
    });

    test("no compliance Lambda holds a write grant on a table its code only reads", () => {
        const surplus = lambdas.flatMap((lambda) =>
            surplusGrants(
                resolver,
                deriveNeeds(lambda.handlerModule),
                resolver.grantedTables(lambda)
            )
                .filter((s) => s.kind === "write")
                .map((s) => `${lambda.handlerModule}: ${s.detail}`)
        );
        expect(surplus).toEqual([]);
    });

    test("the cascade service is granted exactly the tables its code reaches", () => {
        // The cascade API hands the cascade to the executor Lambda; its own table access is the
        // cascade row, the audit entry, and the trigger asset's existence check.
        const cascade = lambdaFor("complianceCascadeService");
        const surplus = surplusGrants(
            resolver,
            deriveNeeds(cascade.handlerModule),
            resolver.grantedTables(cascade)
        );
        expect(surplus.map((s) => s.detail)).toEqual([]);
    });

    test("a fabricated need is reported as a gap", () => {
        // Negative control: the comparison must be able to fail. The audit service reads one table,
        // so a need for a table it is not granted, and a write on the table it only reads, are both
        // absent from its role.
        const audit = lambdaFor("complianceAuditService");
        const granted = resolver.grantedTables(audit);
        const fabricated: Needs = new Map(deriveNeeds(audit.handlerModule));
        fabricated.set("COMPLIANCE_CASCADE_STORAGE_TABLE", {
            write: false,
            batchWrite: false,
            index: true,
        });
        fabricated.set("COMPLIANCE_AUDIT_STORAGE_TABLE", {
            write: true,
            batchWrite: false,
            index: true,
        });
        expect(coverageGaps(resolver, fabricated, granted)).toEqual(
            expect.arrayContaining([
                "COMPLIANCE_CASCADE_STORAGE_TABLE: dynamodb:GetItem is not granted on the table",
                "COMPLIANCE_CASCADE_STORAGE_TABLE: dynamodb:Query is not granted on the table's indexes",
                "COMPLIANCE_AUDIT_STORAGE_TABLE: dynamodb:PutItem is not granted on the table",
            ])
        );
        expect(coverageGaps(resolver, deriveNeeds(audit.handlerModule), granted)).toEqual([]);
    });

    test("a fabricated grant is reported as surplus", () => {
        const audit = lambdaFor("complianceAuditService");
        const needs = deriveNeeds(audit.handlerModule);
        const granted = new Map(resolver.grantedTables(audit));
        const cascadeTable = resolver.tableFor("COMPLIANCE_CASCADE_STORAGE_TABLE");
        granted.set(cascadeTable, {
            actions: new Set(READ_ACTIONS),
            indexActions: new Set(READ_ACTIONS),
        });
        const auditTable = resolver.tableFor("COMPLIANCE_AUDIT_STORAGE_TABLE");
        granted.set(auditTable, {
            actions: new Set([...READ_ACTIONS, ...WRITE_ACTIONS]),
            indexActions: new Set(READ_ACTIONS),
        });
        expect(surplusGrants(resolver, needs, granted).map((s) => [s.table, s.kind])).toEqual([
            [auditTable, "write"],
            [cascadeTable, "unreachable"],
        ]);
        expect(surplusGrants(resolver, needs, resolver.grantedTables(audit))).toEqual([]);
    });
});
