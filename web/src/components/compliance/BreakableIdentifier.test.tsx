/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * An identifier wraps only at an `_` or a camelCase boundary (spaces and hyphens break on their
 * own): BreakableIdentifier places a word-break opportunity at each such boundary and nowhere else.
 */

import React from "react";
import { render } from "@testing-library/react";
import { BreakableIdentifier, identifierParts } from "./BreakableIdentifier";

describe("identifierParts", () => {
    it.each([
        ["compliance_check", ["compliance_", "check"]],
        ["schema_unbound_from_asset", ["schema_", "unbound_", "from_", "asset"]],
        ["exceptionSchemaVersion", ["exception", "Schema", "Version"]],
        ["pipelineRulesPending", ["pipeline", "Rules", "Pending"]],
        ["schemaVersion2Rule", ["schema", "Version2", "Rule"]],
        ["SYSTEM_USER", ["SYSTEM_", "USER"]],
        ["verdict", ["verdict"]],
        ["glb-present, residual-bound", ["glb-present, residual-bound"]],
        ["", []],
    ])("%s", (text, parts) => {
        expect(identifierParts(text)).toEqual(parts);
    });
});

describe("BreakableIdentifier", () => {
    it("renders the whole text with a break opportunity between its parts only", () => {
        const { container } = render(<BreakableIdentifier text="exceptionSchemaVersion" />);
        const span = container.querySelector("[data-identifier]") as HTMLElement;
        expect(span.textContent).toBe("exceptionSchemaVersion");
        expect(span.querySelectorAll("wbr")).toHaveLength(2);
        // Nothing lets it break anywhere else.
        expect(span.style.overflowWrap).toBe("");
        expect(span.style.wordBreak).toBe("");
    });

    it("renders a word without break opportunities", () => {
        const { container } = render(<BreakableIdentifier text="quarantined" />);
        expect(container.querySelectorAll("wbr")).toHaveLength(0);
        expect(container.textContent).toBe("quarantined");
    });
});
