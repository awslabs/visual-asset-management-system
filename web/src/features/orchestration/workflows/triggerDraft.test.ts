/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    draftFrom,
    draftKey,
    draftToTrigger,
    emptyDraft,
    validateDraft,
    withCurrentPipelineTemplates,
} from "./triggerDraft";

describe("draftKey", () => {
    it("is the bare type for the first trigger of a type and type#id for another", () => {
        expect(draftKey(emptyDraft("fileUpload"))).toBe("fileUpload");
        expect(draftKey({ ...emptyDraft("fileUpload"), triggerId: " nightly " })).toBe(
            "fileUpload#nightly"
        );
    });
});

describe("draftFrom", () => {
    it("derives the id from the key for a row that does not report it", () => {
        const draft = draftFrom({ triggerType: "fileUpload#nightly", enabled: false });
        expect(draft).toEqual({
            baseType: "fileUpload",
            triggerId: "nightly",
            editingKey: "fileUpload#nightly",
            enabled: false,
            allow: [],
            exclude: [],
            defaultTemplateIds: {},
        });
    });
});

describe("validateDraft", () => {
    it("rejects a malformed name", () => {
        expect(validateDraft({ ...emptyDraft("fileUpload"), triggerId: "a b" }, [])).toEqual({
            triggerIdInvalid: true,
            keyCollides: false,
        });
    });

    // A taken key would REPLACE that trigger rather than add one.
    it("refuses to add under a key that is already in use", () => {
        const draft = { ...emptyDraft("fileUpload"), triggerId: "nightly" };
        expect(validateDraft(draft, ["fileUpload#nightly"]).keyCollides).toBe(true);
    });

    it("does not count an edit of the same key as a collision", () => {
        const draft = draftFrom({ triggerType: "fileUpload#nightly" });
        expect(validateDraft(draft, ["fileUpload#nightly"]).keyCollides).toBe(false);
    });
});

describe("draftToTrigger", () => {
    it("writes the flat request shape under the draft's key", () => {
        const draft = {
            ...emptyDraft("fileUpload"),
            triggerId: "nightly",
            allow: ["*.glb"],
            defaultTemplateIds: { "db1:p1": "t1" },
        };
        expect(draftToTrigger(draft)).toEqual({
            triggerType: "fileUpload#nightly",
            enabled: true,
            inputFileFilters: { allow: ["*.glb"], exclude: [] },
            defaultTemplateIds: { "db1:p1": "t1" },
        });
    });
});

describe("withCurrentPipelineTemplates", () => {
    it("drops default templates for pipelines no longer in the workflow", () => {
        const trigger = {
            triggerType: "fileUpload",
            defaultTemplateIds: { "db1:p1": "t1", "db1:removed": "t9" },
        };
        expect(
            withCurrentPipelineTemplates(trigger, [{ pipelineId: "p1", pipelineDatabaseId: "db1" }])
                .defaultTemplateIds
        ).toEqual({ "db1:p1": "t1" });
    });

    it("tolerates a trigger with no default templates", () => {
        expect(
            withCurrentPipelineTemplates({ triggerType: "fileUpload" }, []).defaultTemplateIds
        ).toEqual({});
    });
});
