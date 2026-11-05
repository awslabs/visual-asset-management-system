/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * @jest-environment node
 */

// Guards the catalog against the three things it must agree with: the manifest + the component
// files the registry can load, the feature-switch constants the availability rules name, and the
// provider table in web/CLAUDE.md — the same shape of guard viewerConfig.test.ts gives the viewers.

import fs from "fs";
import path from "path";
import catalog from "./searchProviderConfig.json";
import { PROVIDER_COMPONENTS } from "../providers/manifest";
import { featuresEnabled } from "../../common/constants/featuresEnabled";

const providers: any[] = (catalog as any).providers;
const knownFeatures = Object.values(featuresEnabled);

describe("searchProviderConfig vs the manifest and the files", () => {
    it("has at least the two shipped providers with unique ids and priorities", () => {
        const ids = providers.map((p) => p.id);
        expect(ids).toEqual(expect.arrayContaining(["asset-list", "unified-search"]));
        expect(new Set(ids).size).toBe(ids.length);
        const priorities = providers.map((p) => p.priority);
        expect(new Set(priorities).size).toBe(priorities.length);
    });

    it.each(providers.map((p) => [p.id, p]))(
        "%s resolves through the manifest to a component file",
        (_id, provider) => {
            const relativePath = (PROVIDER_COMPONENTS as Record<string, string>)[
                provider.componentPath
            ];
            expect(relativePath).toBeDefined();
            const file = path.join(__dirname, "..", "providers", `${relativePath}.tsx`);
            expect(fs.existsSync(file)).toBe(true);
        }
    );

    it("keeps the asset list unconditional", () => {
        const assetList = providers.find((p) => p.id === "asset-list");
        expect(assetList.availability).toEqual({ always: true });
    });

    it("names only feature switches the web declares", () => {
        const named: string[] = providers.flatMap((p) =>
            "anyOf" in p.availability
                ? p.availability.anyOf.map((c: any) => c.featureEnabled ?? c.featureDisabled)
                : []
        );
        // Control: the unified provider names two switches, so an empty list would be a parse bug.
        expect(named.length).toBeGreaterThan(1);
        named.forEach((name) => expect(knownFeatures).toContain(name));
    });
});

describe("web/CLAUDE.md provider catalog table", () => {
    const claudeMd = fs.readFileSync(path.join(__dirname, "..", "..", "..", "CLAUDE.md"), "utf8");

    interface Row {
        id: string;
        name: string;
        priority: string;
        availability: string;
    }

    // Rows look like: | `id` | Name | priority | availability |
    const rows: Row[] = claudeMd
        .split("\n")
        .filter((line) => /^\|\s*`[a-z0-9-]+`\s*\|/.test(line))
        .map((line) => {
            const cells = line.split("|").map((cell) => cell.trim());
            return {
                id: cells[1].replace(/`/g, ""),
                name: cells[2],
                priority: cells[3],
                availability: cells[4],
            };
        })
        // Other tables (the dependency table) use the same shape; keep the provider rows.
        .filter((row) => providers.some((p) => p.id === row.id));

    it("documents every configured provider once", () => {
        expect(rows.map((row) => row.id).sort()).toEqual(providers.map((p) => p.id).sort());
    });

    it.each(rows.map((row) => [row.id, row] as [string, Row]))(
        "%s's documented name, priority and availability match the catalog",
        (id, row) => {
            const config = providers.find((p) => p.id === id);
            expect(row.name).toBe(config.name);
            expect(Number(row.priority)).toBe(config.priority);
            if ("always" in config.availability) {
                expect(row.availability).toMatch(/always/i);
            } else {
                config.availability.anyOf.forEach((condition: any) => {
                    if (condition.featureEnabled) {
                        expect(row.availability).toContain(`${condition.featureEnabled} enabled`);
                    } else {
                        expect(row.availability).toContain(`${condition.featureDisabled} absent`);
                    }
                });
            }
        }
    );
});
