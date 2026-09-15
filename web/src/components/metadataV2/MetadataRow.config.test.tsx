/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * How often a metadata row reads the runtime config.
 *
 * `appCache.getItem("config")` is a synchronous localStorage read plus a full JSON.parse of the config
 * envelope. It was called from the row's render body, unconditionally: every keystroke in a value input
 * replaces the rows array and re-renders all twenty rows on the page, so a twenty-character value cost
 * ~400 reads and ~400 parses on the interaction most sensitive to input latency — and every non-geo row
 * paid for a value only the geo branch consumes.
 *
 * The value-type inputs are stubbed so this file never loads `MapMetadataPicker` (react-map-gl is
 * ESM-only under Jest), and `appCache` is mocked so the reads can be counted.
 */

import React from "react";
import { render } from "@testing-library/react";
import MetadataRow from "./MetadataRow";
import { MetadataRowState, MetadataValueType } from "./types/metadata.types";

jest.mock("../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: ["LOCATIONSERVICES"] })) },
}));

jest.mock("./valueTypes", () => {
    const stub = (name: string) => {
        const Component = () => <span data-testid={name} />;
        Component.displayName = name;
        return Component;
    };
    return {
        XYZInput: stub("XYZInput"),
        WXYZInput: stub("WXYZInput"),
        Matrix4x4Input: stub("Matrix4x4Input"),
        LLAInput: stub("LLAInput"),
        GeoPointInput: stub("GeoPointInput"),
        GeoJSONInput: stub("GeoJSONInput"),
        MapMetadataPicker: stub("MapMetadataPicker"),
        JSONTextInput: stub("JSONTextInput"),
        DateInput: stub("DateInput"),
        BooleanInput: stub("BooleanInput"),
        InlineControlledListInput: stub("InlineControlledListInput"),
        RawValueEditor: stub("RawValueEditor"),
    };
});

const { appCache } = jest.requireMock("../../services/appCache");

const row = (editType: MetadataValueType, editValue: string): MetadataRowState =>
    ({
        metadataKey: "position",
        metadataValue: editValue,
        metadataValueType: editType,
        isEditing: false,
        hasChanges: false,
        isNew: false,
        isDeleted: false,
        editKey: "position",
        editValue,
        editType,
    } as MetadataRowState);

const noop = () => undefined;

/**
 * The whole tree, so `render` and `rerender` are given the SAME root element type. Passing a different
 * root (RTL's `wrapper` on the first render, a bare table on the next) remounts the component and
 * re-runs its mount-time work, which would make the counting assertion below meaningless.
 */
const tree = (state: MetadataRowState) => (
    <table>
        <tbody>
            <MetadataRow
                row={state}
                index={0}
                onEdit={noop}
                onCancel={noop}
                onSave={noop}
                onDelete={noop}
                onKeyChange={noop}
                onTypeChange={noop}
                onValueChange={noop}
            />
        </tbody>
    </table>
);

describe("MetadataRow runtime-config reads", () => {
    beforeEach(() => jest.clearAllMocks());

    it("a geo row reads the config exactly once, and not again on re-render", () => {
        // The "exactly once" half is the positive control: it proves the mock is wired and that a geo
        // row really does consult the feature flag, so the "not again" half cannot pass merely because
        // nothing ever read it.
        const { rerender } = render(tree(row("geopoint", "a")));
        expect(appCache.getItem).toHaveBeenCalledTimes(1);
        expect(appCache.getItem).toHaveBeenCalledWith("config");

        // Stands in for keystrokes: the parent replaces the rows array, so every row re-renders.
        for (const value of ["ab", "abc", "abcd"]) {
            rerender(tree(row("geopoint", value)));
        }
        expect(appCache.getItem).toHaveBeenCalledTimes(1);
    });

    it("a non-geo row never reads the config at all", () => {
        // Only the geo branch consumes the flag, so a string/number/date row should not pay for it.
        render(tree(row("string", "hello")));
        expect(appCache.getItem).not.toHaveBeenCalled();
    });
});
