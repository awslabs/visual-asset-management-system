/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The web half of the CLI/web check for a workstation's image (feature #536, D14): every record in
 * workstationImageCases.json, which tools/VamsCLI/tests/addon/test_edh_workstation_image.py reads
 * too, shows the agreed image name and, only where agreed, the newer-image badge with its tooltip.
 * The CLI test asserts its Image column and `get` lines against the same file.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import WorkstationImageCell from "./WorkstationImageCell";
import { newerImageNotice, workstationImageName } from "../labels";
import fixture from "../workstationImageCases.json";
import type { WorkstationRecord, WorkstationState } from "../types";

interface ImageCase {
    name: string;
    record: Partial<WorkstationRecord> & { state: string; imageId: string };
    catalogName: string | null;
    shownName: string;
    webShownName: string;
    badge: string | null;
    tooltip: string | null;
    listImage: string;
    getRecipe: string | null;
    getRecipeVersion: string | null;
}

const CASES = fixture.cases as ImageCase[];
const table = CASES.map((c) => [c.name, c] as const);

const recordOf = (c: ImageCase) =>
    ({ ...c.record, state: c.record.state as WorkstationState } as Pick<
        WorkstationRecord,
        | "state"
        | "imageId"
        | "imageName"
        | "recipeId"
        | "recipeDisplayName"
        | "recipeVersion"
        | "latestRecipeVersion"
        | "imageLatest"
    >);

describe("workstationImageCases.json (shared with the CLI tests)", () => {
    it("covers every state, every imageLatest value and a record without the fields (control)", () => {
        expect(new Set(CASES.map((c) => c.record.state))).toEqual(
            new Set(["ACTIVE", "CREATING", "DELETED", "ORPHANED", "STOPPED"])
        );
        expect(new Set(CASES.map((c) => c.record.imageLatest))).toEqual(
            new Set([true, false, null, undefined])
        );
        // A badge for each live state, and none for the two that are not.
        const badged = new Set(CASES.filter((c) => c.badge).map((c) => c.record.state));
        expect(badged).toEqual(new Set(["ACTIVE", "STOPPED", "CREATING"]));
        expect(
            CASES.filter((c) => c.record.imageLatest === false && !c.badge).map(
                (c) => c.record.state
            )
        ).toEqual(expect.arrayContaining(["DELETED", "ORPHANED"]));
        // The catalog fallback is exercised, and only where the service named nothing.
        expect(CASES.some((c) => c.webShownName !== c.shownName)).toBe(true);
    });

    it.each(table)("%s: the name is chosen in the agreed order", (_name, c) => {
        expect(workstationImageName(recordOf(c))).toBe(c.shownName);
        expect(workstationImageName(recordOf(c), c.catalogName)).toBe(c.webShownName);
    });

    it.each(table)("%s: the newer-image badge and its tooltip are as agreed", (_name, c) => {
        const notice = newerImageNotice(recordOf(c));
        expect(notice?.label ?? null).toBe(c.badge);
        expect(notice?.description ?? null).toBe(c.tooltip);
    });

    it.each(table)("%s: the image cell shows the name, the id and the badge", (_name, c) => {
        render(<WorkstationImageCell row={recordOf(c)} catalogName={c.catalogName} />);
        expect(screen.getByText(c.webShownName)).toBeInTheDocument();
        // The image id stays visible next to the name; a cell that can only show the id shows it once.
        expect(screen.getAllByText(c.record.imageId)).toHaveLength(1);

        const badge = screen.queryByText(/^Newer image:/);
        if (c.badge === null) {
            expect(badge).not.toBeInTheDocument();
            return;
        }
        // The badge says it in words, not colour alone, and the tooltip sentence is both the hover
        // title and the accessible description a screen reader announces with it.
        expect(badge).toHaveTextContent(c.badge);
        expect(badge).toHaveAttribute("title", c.tooltip);
        expect(badge).toHaveAccessibleDescription(c.tooltip as string);
        expect(screen.getByText(c.tooltip as string)).toHaveClass("sr-only");
    });
});
