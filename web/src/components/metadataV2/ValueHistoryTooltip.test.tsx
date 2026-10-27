/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The change-information boxes, the bulk-edit CSV example and the Format JSON button set their
 * background and text colour together from theme tokens, so neither mode pairs a fixed light
 * background with the theme's text colour.
 */
import React from "react";
import fs from "fs";
import path from "path";
import { render, screen } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import { ValueHistoryTooltip } from "./ValueHistoryTooltip";

const boxOf = (text: string) => screen.getByText(text) as HTMLElement;

describe("metadata value surfaces in dark mode", () => {
    beforeEach(() => document.body.classList.add("awsui-dark-mode"));
    afterEach(() => document.body.classList.remove("awsui-dark-mode"));

    it("colours every change-information box from theme tokens", () => {
        render(
            <ValueHistoryTooltip
                oldKey="oldKey"
                oldType="string"
                oldValue="oldValue"
                schemaDefaultValue="defaultValue"
                hasChanges
            />
        );
        createWrapper(document.body).findPopover()!.findTrigger().click();

        for (const text of ["oldKey", "string", "oldValue"]) {
            const box = boxOf(text);
            expect(box.style.background).toBe("var(--vams-bg-secondary)");
            expect(box.style.color).toBe("var(--vams-text-primary)");
        }
        const schemaDefault = boxOf("defaultValue");
        expect(schemaDefault.style.background).toBe("var(--vams-bg-selected)");
        expect(schemaDefault.style.color).toBe("var(--vams-text-primary)");
    });

    it("keeps hex colours out of the CSV example and the Format JSON button", () => {
        const read = (rel: string) => fs.readFileSync(path.join(__dirname, rel), "utf8");
        const bulk = read("BulkEditMode.tsx");
        const csv = bulk.indexOf("Metadata Key,Value Type,Metadata Value");
        const example = bulk.slice(bulk.lastIndexOf("<pre", csv), csv);
        expect(example).toContain('background: "var(--vams-bg-secondary)"');
        expect(example).toContain('color: "var(--vams-text-primary)"');

        const raw = read("valueTypes/RawValueEditor.tsx");
        const button = raw.slice(raw.indexOf("<button"), raw.indexOf("</button>"));
        expect(button).toContain('background: "var(--vams-bg-secondary)"');
        expect(button).toContain('color: "var(--vams-text-primary)"');
        expect(button).toContain('border: "1px solid var(--vams-border-default)"');
        expect(button).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
    });
});
