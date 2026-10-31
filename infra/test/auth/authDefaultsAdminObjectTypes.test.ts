/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The seeded `admin` role holds an allow-all constraint on every core permission object type.
 *
 * The core object types are declared in `backend/backend/common/constants.py`
 * (`CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES`). A type with no seeded admin constraint is denied to every
 * role, the admin included, until someone writes a constraint for it: a new core type the handlers
 * enforce (for example `complianceBinding`, which authorizes binding a compliance schema) would leave
 * the seeded administrator unable to use the feature. Each seeded constraint is written back to
 * Amazon DynamoDB on every deployment (`deployment/update-the-solution.md`), so the construct is the
 * whole of the admin's seeded grant on an upgraded deployment as well as a new one.
 *
 * Both files are read as source, not synthesized: the constraint items are literal, config-independent
 * DynamoDB `putItem` payloads, and the constants list is a literal Python list.
 */

import * as fs from "fs";
import * as path from "path";

const REPO_ROOT = path.join(__dirname, "..", "..", "..");
const ADMIN_CONSTRUCT = fs.readFileSync(
    path.join(
        REPO_ROOT,
        "infra",
        "lib",
        "nestedStacks",
        "auth",
        "constructs",
        "dynamodb-authdefaults-admin-construct.ts"
    ),
    "utf-8"
);
const CONSTANTS = fs.readFileSync(
    path.join(REPO_ROOT, "backend", "backend", "common", "constants.py"),
    "utf-8"
);

interface SeededConstraint {
    id: string;
    objectType: string;
    criteria: { field: string; operator: string; value: string }[];
    permissions: { permission: string; permissionType: string }[];
}

function coreObjectTypes(source: string): string[] {
    const match = source.match(/CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES = \[([\s\S]*?)\]/);
    if (!match) throw new Error("CORE_ALLOWED_CONSTRAINT_OBJECT_TYPES not found in constants.py");
    return [...match[1].matchAll(/'([A-Za-z]+)'/g)].map((m) => m[1]);
}

function seededConstraints(source: string): SeededConstraint[] {
    const marker = /constraintId: \{\s*S: "initial_admin_([A-Za-z0-9_]+)"/g;
    const starts = [...source.matchAll(marker)].map((m) => ({ id: m[1], index: m.index ?? 0 }));
    return starts.map((start, i) => {
        const end = i + 1 < starts.length ? starts[i + 1].index : source.length;
        const segment = source.slice(start.index, end);
        const objectType = segment.match(/objectType: \{\s*S: "([^"]*)"/);
        if (!objectType) throw new Error(`${start.id}: no objectType`);
        const fields = [...segment.matchAll(/field: \{\s*S: "([^"]*)"/g)].map((m) => m[1]);
        const operators = [...segment.matchAll(/operator: \{\s*S: "([^"]*)"/g)].map((m) => m[1]);
        const values = [...segment.matchAll(/value: \{\s*S: "([^"]*)"/g)].map((m) => m[1]);
        const permissions = [...segment.matchAll(/permission: \{\s*S: "([^"]*)"/g)].map(
            (m) => m[1]
        );
        const permissionTypes = [...segment.matchAll(/permissionType: \{\s*S: "([^"]*)"/g)].map(
            (m) => m[1]
        );
        if (fields.length !== values.length || operators.length !== values.length) {
            throw new Error(`${start.id}: the criteria reader is out of step with the construct`);
        }
        if (permissions.length !== permissionTypes.length) {
            throw new Error(`${start.id}: the permission reader is out of step with the construct`);
        }
        return {
            id: start.id,
            objectType: objectType[1],
            criteria: values.map((value, c) => ({
                field: fields[c],
                operator: operators[c],
                value,
            })),
            permissions: permissions.map((permission, p) => ({
                permission,
                permissionType: permissionTypes[p],
            })),
        };
    });
}

const ALLOW_ALL = ["GET", "PUT", "POST", "DELETE"].map((permission) => ({
    permission,
    permissionType: "allow",
}));
const types = coreObjectTypes(CONSTANTS);
const constraints = seededConstraints(ADMIN_CONSTRUCT);

describe("seeded admin constraints", () => {
    it("reads the core object types and the seeded constraints", () => {
        // Control for both readers: an empty parse would let every assertion below pass vacuously.
        expect(types).toEqual(
            expect.arrayContaining(["database", "asset", "api", "web", "complianceSchema"])
        );
        expect(types.length).toBeGreaterThanOrEqual(15);
        expect(constraints.length).toBeGreaterThanOrEqual(types.length);
    });

    it.each(types)("seeds one allow-all admin constraint on %s", (objectType) => {
        const seeded = constraints.filter((c) => c.objectType === objectType);
        expect(seeded).toHaveLength(1);
        expect(seeded[0].permissions).toEqual(ALLOW_ALL);
        expect(seeded[0].criteria).toHaveLength(1);
        expect(seeded[0].criteria[0]).toMatchObject({ operator: "contains", value: ".*" });
    });

    it("scopes the complianceBinding constraint on databaseId, the type's one field", () => {
        const seeded = constraints.filter((c) => c.objectType === "complianceBinding");
        expect(seeded).toHaveLength(1);
        expect(seeded[0].id).toBe("allow_all_compliancebindings");
        expect(seeded[0].criteria[0].field).toBe("databaseId");
    });

    it("seeds no object type the backend does not accept", () => {
        // A constraint on an unknown type is rejected at create time and matches nothing.
        for (const constraint of constraints) {
            expect(types).toContain(constraint.objectType);
        }
    });
});
