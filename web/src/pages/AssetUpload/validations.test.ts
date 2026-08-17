/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { enforceableRequiredTagTypes, validateRequiredTagTypeSelected } from "./validations";
import { TagType } from "../Tag/TagType.interface";

const tagType = (over: Partial<TagType> & { tagTypeName: string }): TagType =>
    ({ description: "", required: "False", tags: [], ...over } as TagType);

describe("enforceableRequiredTagTypes", () => {
    it("drops a required tag type that has no tags", () => {
        // Nothing can satisfy it, so demanding a selection makes the form unsubmittable.
        const types = [
            tagType({ tagTypeName: "Empty", required: "True", tags: [] }),
            tagType({ tagTypeName: "Populated", required: "True", tags: ["a"] }),
        ];

        expect(enforceableRequiredTagTypes(types).map((t) => t.tagTypeName)).toEqual(["Populated"]);
    });

    it("drops an empty required tag type in either scope", () => {
        // The rule is about the tag type having tags, not about which scope owns it.
        const types = [
            tagType({ tagTypeName: "GlobalEmpty", required: "True", tags: [] }),
            tagType({
                tagTypeName: "DbEmpty",
                required: "True",
                tags: [],
                databaseId: "factory-db",
            } as any),
        ];

        expect(enforceableRequiredTagTypes(types)).toEqual([]);
    });

    it("tolerates a record with no tags array at all", () => {
        const types = [{ tagTypeName: "NoField", required: "True" } as any];
        expect(enforceableRequiredTagTypes(types)).toEqual([]);
        expect(enforceableRequiredTagTypes(undefined)).toEqual([]);
    });

    it("keeps optional tag types out regardless of their tags", () => {
        expect(enforceableRequiredTagTypes([tagType({ tagTypeName: "Opt", tags: ["a"] })])).toEqual(
            []
        );
    });
});

describe("validateRequiredTagTypeSelected", () => {
    it("accepts no tags when the only required tag type is empty", () => {
        const types = [tagType({ tagTypeName: "Empty", required: "True", tags: [] })];

        expect(validateRequiredTagTypeSelected([], types)).toBeUndefined();
        expect(validateRequiredTagTypeSelected(undefined, types)).toBeUndefined();
    });

    it("accepts an unrelated selection when the only required tag type is empty", () => {
        // The second branch of the validator also has to ignore the empty type; otherwise picking a
        // tag from another type reports it as missing.
        const types = [
            tagType({ tagTypeName: "Empty", required: "True", tags: [] }),
            tagType({ tagTypeName: "Free", tags: ["blue"] }),
        ];

        expect(validateRequiredTagTypeSelected(["blue"], types)).toBeUndefined();
    });

    it("still demands a selection from a required tag type that has tags", () => {
        const types = [tagType({ tagTypeName: "Line", required: "True", tags: ["press"] })];

        expect(validateRequiredTagTypeSelected([], types)).toBe("Required Field.");
        expect(validateRequiredTagTypeSelected(["other"], types)).toContain("Line");
        expect(validateRequiredTagTypeSelected(["press"], types)).toBeUndefined();
    });

    it("names only the unsatisfied populated tag types", () => {
        const types = [
            tagType({ tagTypeName: "Empty", required: "True", tags: [] }),
            tagType({ tagTypeName: "Line", required: "True", tags: ["press"] }),
            tagType({ tagTypeName: "Site", required: "True", tags: ["north"] }),
        ];

        const message = validateRequiredTagTypeSelected(["press"], types);
        expect(message).toContain("Site");
        expect(message).not.toContain("Empty");
    });
});
