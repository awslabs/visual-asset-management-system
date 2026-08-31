/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The backend route registry and the CDK route registry must describe the same API.
 *
 * S3-CONTRACTS-038. `backend/backend/common/apiRoutes.py` is the authoritative surface: the custom
 * Lambda authorizer resolves a request against it, handlers dispatch on `ApiRoute.matches()`, and
 * `GET /auth/routes/api` lists it for constraint authoring. The CDK side registers routes through
 * `attachFunctionToApi()` into `RouteRegistry`, which is rendered into one inline OpenAPI document on the
 * `SpecRestApi`. Nothing compared the two, on what root `CLAUDE.md` calls the seam where "a handler
 * without a route is dead code; a route without a handler will 500".
 *
 * The two directions are NOT symmetric, so they are asserted separately:
 *
 * *   **CDK ⊆ backend is unconditional and is the dangerous direction.** A method+path registered with
 *     API Gateway but absent from `apiRoutes.py` reaches a handler that does not recognise it. Depending
 *     on the handler that is a 500 or, worse, a path the authorizer has no constraint mapping for.
 * *   **backend ⊆ CDK holds only once every route-contributing feature is enabled.** Routes can be
 *     feature-gated: `GET /addon/physna/viewer` is registered by the Physna add-on stack, which ships
 *     disabled, so against the stock commercial template the backend surface is legitimately one pair
 *     larger. Measured — that single route is the whole difference (164 backend pairs vs 163). Asserting
 *     equality against the stock template would therefore have to hardcode an allowance, which would
 *     grow silently with each new optional route; enabling the add-on instead keeps the assertion exact.
 *
 * The backend side is parsed from the Python SOURCE rather than executed, so the test needs no Python
 * interpreter. That is a real constraint: this suite runs under Jest, and a `python` dependency would
 * make an infra test fail on a machine with no backend environment — which is the same class of problem
 * as the Docker dependency removed from `infra.test.ts`.
 *
 * Parsing brings its own risk, so the parse is guarded: the declared-constant count and the pair count
 * are both asserted against known-good figures, and every declared constant must be reachable from a
 * category group array. That last one catches the defect root `CLAUDE.md` warns about directly — a
 * constant defined but never added to a group is absent from `get_public_api_routes()`, so it is invisible
 * to the authorizer listing and to the CLI while still looking present in the file.
 */

import * as fs from "fs";
import * as path from "path";
import { SynthResult, synthTemplate } from "../support/templateSynth";

const API_ROUTES_PY = path.resolve(__dirname, "..", "../../backend/backend/common/apiRoutes.py");

interface DeclaredRoute {
    name: string;
    methodPathPairs: string[];
    internal: boolean;
}

/** Strip Python comments and docstrings so a commented-out declaration is not parsed as live. */
function pythonCode(source: string): string {
    return source
        .replace(/"""[\s\S]*?"""/g, '""')
        .replace(/'''[\s\S]*?'''/g, "''")
        .split("\n")
        .map((line) => {
            const hash = line.indexOf("#");
            if (hash === -1) return line;
            // A '#' inside a string literal is not a comment. Path templates contain no '#', so a
            // simple quote count is enough here and is checked by the count assertions below.
            const before = line.slice(0, hash);
            const quotes = (before.match(/"/g) || []).length;
            return quotes % 2 === 0 ? before : line;
        })
        .join("\n");
}

/**
 * Every `NAME = ApiRoute(...)` declaration, with its method+path pairs.
 *
 * The argument list is extracted by BALANCED PARENTHESES rather than by a single regex, because many
 * declarations wrap across lines. A one-line regex parsed only 144 of the 164 pairs and the 20 it missed
 * were reported as routes API Gateway serves but the backend does not declare — a parse failure wearing
 * the costume of the exact defect this file exists to detect.
 */
function declaredRoutes(code: string): DeclaredRoute[] {
    const routes: DeclaredRoute[] = [];
    const start = /^([A-Z][A-Z0-9_]*)\s*=\s*ApiRoute\(/gm;
    let match: RegExpExecArray | null;
    while ((match = start.exec(code)) !== null) {
        const name = match[1];
        // Walk from the opening paren to its match, so a nested methods tuple does not end the scan.
        let depth = 0;
        let end = -1;
        for (let i = match.index + match[0].length - 1; i < code.length; i++) {
            if (code[i] === "(") depth++;
            else if (code[i] === ")") {
                depth--;
                if (depth === 0) {
                    end = i;
                    break;
                }
            }
        }
        if (end === -1) continue;
        const args = code.slice(match.index + match[0].length, end);

        const routePath = /"([^"]+)"/.exec(args)?.[1];
        const methodsRaw = /\(([^)]*)\)/.exec(args)?.[1];
        if (!routePath || methodsRaw === undefined) continue;

        // Methods are the module's GET/POST/PUT/DELETE/HEAD constants, whose values equal their names.
        const methods = methodsRaw
            .split(",")
            .map((m) => m.trim())
            .filter(Boolean);
        routes.push({
            name,
            methodPathPairs: methods.map((m) => `${m} ${routePath}`),
            internal: /internal\s*=\s*True/.test(args),
        });
    }
    return routes;
}

/**
 * The names referenced by any `*_ROUTES` group tuple, which is what ALL_API_ROUTES is composed from.
 *
 * Balanced parentheses again, for the same reason as `declaredRoutes` and with a sharper failure mode: a
 * lazy `[\s\S]*?\n\)` body match OVERSHOOTS a single-line tuple. `SEARCH_ROUTES: ... = (API_SEARCH, ...)`
 * closes on its own line with no `\n)`, so the lazy scan ran on to the next `\n)` far below and swallowed
 * every intervening declaration into that group's membership. The effect was that EVERY constant looked
 * grouped, so the orphan assertion below could not fail — verified by sabotage: an injected constant
 * belonging to no group was reported as a member of SEARCH_ROUTES.
 */
function groupedNames(code: string): Set<string> {
    const grouped = new Set<string>();
    const start = /^[A-Z][A-Z0-9_]*_ROUTES\s*:\s*Tuple\[ApiRoute,\s*\.\.\.\]\s*=\s*\(/gm;
    let match: RegExpExecArray | null;
    while ((match = start.exec(code)) !== null) {
        let depth = 0;
        let end = -1;
        for (let i = match.index + match[0].length - 1; i < code.length; i++) {
            if (code[i] === "(") depth++;
            else if (code[i] === ")") {
                depth--;
                if (depth === 0) {
                    end = i;
                    break;
                }
            }
        }
        if (end === -1) continue;
        const body = code.slice(match.index + match[0].length, end);
        for (const token of body.match(/[A-Z][A-Z0-9_]*/g) || []) {
            grouped.add(token);
        }
    }
    return grouped;
}

const CODE = pythonCode(fs.readFileSync(API_ROUTES_PY, "utf-8"));
const DECLARED = declaredRoutes(CODE);
const GROUPED = groupedNames(CODE);

/** Public (non-internal) method+path pairs the backend recognises. */
const BACKEND_PAIRS = new Set(
    DECLARED.filter((r) => !r.internal).flatMap((r) => r.methodPathPairs)
);

/** Method+path pairs the synthesized REST API exposes, excluding the CORS preflight MOCKs. */
function cdkPairs(synth: SynthResult): Set<string> {
    const apis = synth.ofType("AWS::ApiGateway::RestApi");
    expect(apis).toHaveLength(1);
    const body = (apis[0].properties as any).Body;
    expect(body?.paths).toBeDefined();

    const pairs = new Set<string>();
    for (const routePath of Object.keys(body.paths)) {
        for (const method of Object.keys(body.paths[routePath])) {
            // OPTIONS is the unauthenticated CORS MOCK the spec builder adds per path; it is not a
            // backend route and apiRoutes.py correctly does not declare it.
            if (method.toLowerCase() === "options") continue;
            pairs.add(`${method.toUpperCase()} ${routePath}`);
        }
    }
    return pairs;
}

/** Enable every optional feature that contributes an API route. */
function withRouteContributingFeatures(c: any) {
    c.app.useGlobalVpc.enabled = true;
    // The Physna add-on registers GET /addon/physna/viewer and ships disabled.
    const physna = c.app.addons?.usePhysnaSync;
    if (physna) {
        physna.enabled = true;
        physna.tenantId = "00000000-0000-4000-8000-000000000000";
        physna.apiBaseEndpoint = "https://app-api.physna.com/v3/";
        physna.authTokenEndpoint =
            "https://physna-app.auth.us-east-2.amazoncognito.com/oauth2/token";
        physna.authType = "cognito";
        physna.clientId = "synth-only-client-id";
        physna.clientSecret = "synth-only-client-secret";
        physna.credentialsSecretArn = "";
    }
}

describe("apiRoutes.py parses as expected", () => {
    // The parse is the weak link in this file: a regex that silently matched nothing would make every
    // comparison below trivially true. These four assertions are what stop that.
    test("the file was found and read", () => {
        expect(fs.existsSync(API_ROUTES_PY)).toBe(true);
        expect(CODE.length).toBeGreaterThan(5000);
    });

    test("a plausible number of route constants were parsed", () => {
        expect(DECLARED.length).toBeGreaterThan(90);
    });

    test("a plausible number of public method+path pairs were derived", () => {
        // Measured at 164 when written. A bound rather than an equality so adding an endpoint does not
        // fail this file for the wrong reason — the parity assertions below are what must hold exactly.
        expect(BACKEND_PAIRS.size).toBeGreaterThan(150);
    });

    test("category group arrays were parsed", () => {
        expect(GROUPED.size).toBeGreaterThan(90);
    });

    test("every declared route constant is referenced by a category group array", () => {
        // Root CLAUDE.md: the group arrays feed handler dispatch and the GET /auth/routes/api listing,
        // "so a missing entry is invisible to constraint authoring and the CLI". A constant absent from
        // every group is excluded from get_public_api_routes() while still reading as present.
        const orphans = DECLARED.filter((r) => !GROUPED.has(r.name)).map((r) => r.name);
        expect(orphans).toEqual([]);
    });
});

describe("every registered API Gateway route is known to the backend", () => {
    // The unconditional, dangerous direction: a route API Gateway serves that apiRoutes.py does not
    // declare reaches a handler that does not recognise it.
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial");
    });

    test("[control] the REST API body carries paths", () => {
        expect(cdkPairs(synth).size).toBeGreaterThan(150);
    });

    test("no registered route is missing from apiRoutes.py", () => {
        const unknown = [...cdkPairs(synth)].filter((pair) => !BACKEND_PAIRS.has(pair)).sort();
        expect(unknown).toEqual([]);
    });
});

describe("every backend route is registered with API Gateway", () => {
    // The other direction, which needs the optional route-contributing features on. A declared route
    // with no registration is dead: the CLI and the authorizer listing advertise an endpoint that 403s
    // at the resource policy because the path is not in the spec.
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: withRouteContributingFeatures,
            mutateKey: "route-parity-all-features",
        });
    });

    test("[control] enabling the add-on added its route", () => {
        // Proves the mutation took effect. Without this, the parity assertion below could pass because
        // the feature never turned on AND the route was dropped from the backend at the same time.
        expect(cdkPairs(synth).has("GET /addon/physna/viewer")).toBe(true);
    });

    test("no backend route is unregistered", () => {
        const unregistered = [...BACKEND_PAIRS].filter((pair) => !cdkPairs(synth).has(pair)).sort();
        expect(unregistered).toEqual([]);
    });

    test("the two surfaces are exactly equal with all route features enabled", () => {
        const cdk = cdkPairs(synth);
        expect([...cdk].sort()).toEqual([...BACKEND_PAIRS].sort());
    });
});
