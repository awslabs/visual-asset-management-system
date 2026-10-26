/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    LINK_DIRECTION_OPTIONS,
    METADATA_SCOPE_OPTIONS,
    bodyFromRulesDraft,
    metadataScopeOf,
    rulesDraftFromBody,
    validateMetadataScope,
    withMetadataScope,
} from "./complianceSchemaRules";
import { COMPLIANCE_METADATA_SCOPES } from "../../services/ComplianceService";

const ref = { databaseId: "GLOBAL", schemaName: "defaultAsset" };

describe("metadata rule scopes", () => {
    it("offers the four scopes the API accepts, asset first", () => {
        expect(METADATA_SCOPE_OPTIONS.map((o) => o.value)).toEqual(COMPLIANCE_METADATA_SCOPES);
        expect(METADATA_SCOPE_OPTIONS[0].label).toBe("Asset metadata");
    });

    it("reads a rule without a scope as asset", () => {
        expect(metadataScopeOf({ metadataSchemaRef: ref })).toBe("asset");
        expect(metadataScopeOf({ scope: "links" })).toBe("links");
    });

    it("drops the filters of the scope switched away from and writes asset as no scope", () => {
        const files = withMetadataScope(
            { metadataSchemaRef: ref, includePaths: ["^m/"], excludePaths: ["\\.tmp$"] },
            "files"
        );
        expect(files).toEqual({
            metadataSchemaRef: ref,
            scope: "files",
            includePaths: ["^m/"],
            excludePaths: ["\\.tmp$"],
        });
        const links = withMetadataScope({ ...files, linkDirection: "parents" }, "links");
        expect(links).toEqual({ metadataSchemaRef: ref, scope: "links", linkDirection: "parents" });
        expect(withMetadataScope(links, "asset")).toEqual({ metadataSchemaRef: ref });
    });

    it("keeps the scope fields through a builder round trip", () => {
        const body = {
            schemaFormat: "vams-rules-v1" as const,
            rules: {
                placement: {
                    ruleType: "metadata" as const,
                    enforcement: "inform" as const,
                    scope: "links" as const,
                    linkDirection: "parents" as const,
                    relationshipType: "parentChild" as const,
                    metadataSchemaRef: ref,
                    checks: [{ name: "c", additionalRequiredFields: ["Translation"] }],
                },
            },
        };
        expect(bodyFromRulesDraft(rulesDraftFromBody(body))).toEqual(body);
    });

    it("accepts every scope with its own filters", () => {
        expect(validateMetadataScope({})).toEqual({});
        expect(validateMetadataScope({ scope: "fileAttributes", includePaths: ["^a/"] })).toEqual(
            {}
        );
        expect(
            validateMetadataScope({
                scope: "links",
                linkDirection: "children",
                relationshipType: "related",
            })
        ).toEqual({});
    });

    it("reports path filters outside a file scope with the API's message", () => {
        expect(validateMetadataScope({ scope: "links", excludePaths: ["x"] }).scope).toBe(
            "'includePaths' and 'excludePaths' apply to the files and fileAttributes scopes only"
        );
    });

    it("reports link filters outside the links scope with the API's message", () => {
        expect(validateMetadataScope({ relationshipType: "related" }).scope).toBe(
            "'linkDirection' and 'relationshipType' apply to the links scope only"
        );
    });

    it("bounds the path patterns as the API does", () => {
        expect(
            validateMetadataScope({ scope: "files", includePaths: Array(26).fill("x") })
                .includePaths
        ).toBe("includePaths: ensure this value has at most 25 items");
        expect(
            validateMetadataScope({ scope: "files", excludePaths: ["x".repeat(513)] }).excludePaths
        ).toBe("Every 'excludePaths' pattern must be 1 to 512 characters");
    });

    it("refuses an unknown scope or link vocabulary", () => {
        expect(validateMetadataScope({ scope: "everything" }).scope).toMatch(
            /permitted: 'asset', 'files', 'fileAttributes', 'links'/
        );
        expect(
            validateMetadataScope({ scope: "links", linkDirection: "sideways" }).linkDirection
        ).toMatch(/permitted: 'parents', 'children', 'related'/);
    });
});

describe("link direction choices", () => {
    it("offers each direction once, and still accepts `related` as both directions", () => {
        const labels = LINK_DIRECTION_OPTIONS.map((option) => option.label);
        expect(new Set(labels).size).toBe(labels.length);
        expect(LINK_DIRECTION_OPTIONS.map((option) => option.value)).toEqual([
            "",
            "parents",
            "children",
        ]);
        expect(labels[0]).toBe("Both directions");
        expect(
            validateMetadataScope({ scope: "links", linkDirection: "related" }).linkDirection
        ).toBeUndefined();
    });
});
