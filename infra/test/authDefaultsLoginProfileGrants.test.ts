/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The seeded `basicReadOnly` role must be granted `/auth/loginProfile` on both its GET and its POST
 * `api` constraint. `/auth/loginProfile/{userId}` is enforced at Tier 1 like any other route, and the
 * two methods are called by different clients: the web app POSTs it during sign-in to record the
 * user's email, the CLI GETs it to validate a session. A grant present on only one of them produces a
 * role that works in one client and 403s in the other.
 *
 * This applies to existing deployments as much as to new ones. `deployment/update-the-solution.md`
 * records that every default constraint - each one whose id begins `initial_admin_` or
 * `initial_basicro_` - is written back to Amazon DynamoDB and replaced in full on every deployment,
 * in-place or A/B. So the seeded constraints an upgraded deployment ends up with are exactly the ones
 * this construct declares, not the ones it was originally created with.
 *
 * The assertions read the construct source rather than a synthesized template: the constraint items
 * are literal, config-independent DynamoDB `putItem` payloads, and reading the source keeps this off
 * the full-app synth path. Each positive check is paired with a control that would fail if the reader
 * below matched indiscriminately - a reader that pooled every constraint's criteria together, or one
 * that ignored the allow/deny distinction, would satisfy the loginProfile assertions for a construct
 * that never granted the route. The last test runs the same reader over a copy of the source with the
 * POST criterion deleted, so the grant assertions are demonstrably sensitive to the criterion rather
 * than to anything else in the file.
 */

import * as fs from "fs";
import * as path from "path";

const SOURCE_PATH = path.join(
    __dirname,
    "..",
    "lib",
    "nestedStacks",
    "auth",
    "constructs",
    "dynamodb-authdefaults-ro-construct.ts"
);
const SOURCE = fs.readFileSync(SOURCE_PATH, "utf-8");

interface SeededConstraint {
    /** The constraint id suffix, e.g. "allow_get_apis" for initial_basicro_allow_get_apis. */
    id: string;
    /** route__path values in criteriaOr (the widening list). */
    routeValuesOr: string[];
    /** route__path values in criteriaAnd (the narrowing list). */
    routeValuesAnd: string[];
    permissions: { permission: string; permissionType: string }[];
}

/** `value: { S: "..." }`, tolerating comment lines between the brace and the string. */
const VALUE_RE = /value: \{\s*(?:\/\/[^\n]*\n\s*)*S: "([^"]*)"/g;
const FIELD_RE = /field: \{\s*S: "([^"]*)"/g;
const PERMISSION_RE = /permission: \{\s*S: "([^"]*)"/g;
const PERMISSION_TYPE_RE = /permissionType: \{\s*S: "([^"]*)"/g;

/**
 * Route values of one criteria list. The keys appear in a fixed order (criteriaOr and/or
 * criteriaAnd, then description), so a list ends at whichever of the other two keys follows it.
 */
function criteriaRouteValues(segment: string, key: "criteriaOr" | "criteriaAnd"): string[] {
    const start = segment.indexOf(`${key}: {`);
    if (start === -1) return [];
    const otherKey = key === "criteriaOr" ? "criteriaAnd: {" : "criteriaOr: {";
    const ends = [segment.indexOf("description: {", start), segment.indexOf(otherKey, start)]
        .filter((i) => i > -1)
        .sort((a, b) => a - b);
    const region = segment.slice(start, ends.length ? ends[0] : segment.length);

    // Each criterion is {field, id, operator, value}; pair the fields with the values positionally
    // so a criterion on some other field is not read as a route grant.
    const fields = [...region.matchAll(FIELD_RE)].map((m) => m[1]);
    const values = [...region.matchAll(VALUE_RE)].map((m) => m[1]);
    if (fields.length !== values.length) {
        throw new Error(
            `${key}: read ${fields.length} fields but ${values.length} values - the reader is out of step with the construct`
        );
    }
    return values.filter((_value, i) => fields[i] === "route__path");
}

function parseSeededConstraints(source: string): SeededConstraint[] {
    const marker = /constraintId: \{\s*S: `initial_\$\{roleNameIDClean\}_([A-Za-z0-9_]+)`/g;
    const starts = [...source.matchAll(marker)].map((m) => ({ id: m[1], index: m.index! }));

    return starts.map((start, i) => {
        const end = i + 1 < starts.length ? starts[i + 1].index : source.length;
        const segment = source.slice(start.index, end);

        const permissionsStart = segment.indexOf("groupPermissions: {");
        const permissionsRegion = permissionsStart === -1 ? "" : segment.slice(permissionsStart);
        const permissions = [...permissionsRegion.matchAll(PERMISSION_RE)].map((m) => m[1]);
        const permissionTypes = [...permissionsRegion.matchAll(PERMISSION_TYPE_RE)].map(
            (m) => m[1]
        );
        if (permissions.length !== permissionTypes.length) {
            throw new Error(
                `${start.id}: read ${permissions.length} permissions but ${permissionTypes.length} permission types`
            );
        }

        return {
            id: start.id,
            routeValuesOr: criteriaRouteValues(segment, "criteriaOr"),
            routeValuesAnd: criteriaRouteValues(segment, "criteriaAnd"),
            permissions: permissions.map((permission, p) => ({
                permission,
                permissionType: permissionTypes[p],
            })),
        };
    });
}

const constraints = parseSeededConstraints(SOURCE);
const pick = (parsed: SeededConstraint[], id: string): SeededConstraint => {
    const found = parsed.filter((c) => c.id === id);
    expect(found).toHaveLength(1);
    return found[0];
};
const byId = (id: string): SeededConstraint => pick(constraints, id);

describe("seeded basicReadOnly api constraints", () => {
    it("reads every seeded constraint out of the construct", () => {
        // Control for the reader itself: an empty or partial parse would let every assertion
        // below pass vacuously.
        expect(constraints.length).toBeGreaterThanOrEqual(12);
        expect(constraints.map((c) => c.id)).toEqual(
            expect.arrayContaining([
                "allow_web_paths_get",
                "allow_get_apis",
                "deny_execution_logs",
                "allow_post_apis",
                "allow_user_api_keys",
            ])
        );
    });

    it("grants GET on /auth/loginProfile", () => {
        const constraint = byId("allow_get_apis");
        expect(constraint.permissions).toEqual([{ permission: "GET", permissionType: "allow" }]);
        expect(constraint.routeValuesOr).toContain("/auth/loginProfile");
    });

    it("grants POST on /auth/loginProfile", () => {
        const constraint = byId("allow_post_apis");
        expect(constraint.permissions).toEqual([{ permission: "POST", permissionType: "allow" }]);
        expect(constraint.routeValuesOr).toContain("/auth/loginProfile");
    });

    it("reads each constraint's own criteria, not a pooled list", () => {
        // /asset-links is granted for GET only. A reader that pooled criteria across constraints
        // would report it as a POST grant too, and would report /auth/loginProfile on both lists
        // however the construct was written.
        expect(byId("allow_get_apis").routeValuesOr).toContain("/asset-links");
        expect(byId("allow_post_apis").routeValuesOr).not.toContain("/asset-links");
        expect(byId("allow_get_apis").routeValuesOr.length).toBeGreaterThan(
            byId("allow_post_apis").routeValuesOr.length
        );
    });

    it("distinguishes a deny constraint from a grant", () => {
        // The execution-logs constraint withholds a route the broad /workflows GET allow reaches.
        // Reading it as an allow would make "granted" true for anything mentioned anywhere.
        const constraint = byId("deny_execution_logs");
        expect(constraint.permissions).toEqual([{ permission: "GET", permissionType: "deny" }]);
        expect(constraint.routeValuesAnd).toContain("/workflows/executions/");
        expect(byId("allow_get_apis").routeValuesOr).not.toContain("/logs");
    });

    it("reports the POST grant missing when the criterion is removed", () => {
        // Sensitivity check: delete the criterion from the POST constraint only (the first
        // occurrence at or after its id) and re-read. The GET grant must survive, so the two
        // assertions above are pinned to their own constraint.
        const postStart = SOURCE.indexOf("`initial_${roleNameIDClean}_allow_post_apis`");
        expect(postStart).toBeGreaterThan(-1);
        const mutated =
            SOURCE.slice(0, postStart) +
            SOURCE.slice(postStart).replace('S: "/auth/loginProfile"', 'S: "/removed-by-test"');
        expect(mutated).not.toEqual(SOURCE);

        const reparsed = parseSeededConstraints(mutated);
        expect(pick(reparsed, "allow_post_apis").routeValuesOr).not.toContain("/auth/loginProfile");
        expect(pick(reparsed, "allow_get_apis").routeValuesOr).toContain("/auth/loginProfile");
    });
});
