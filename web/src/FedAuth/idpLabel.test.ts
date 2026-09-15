/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { idpButtonLabel, DEFAULT_IDP_LABEL } from "./idpLabel";

describe("idpButtonLabel", () => {
    it("uses the configured display name", () => {
        expect(idpButtonLabel("Okta")).toBe("Okta");
    });

    it("falls back for the string 'undefined'", () => {
        // /api/amplify-config renders an unset field as the literal string "undefined", so this is
        // the shape an unconfigured deployment actually sends — not a hypothetical.
        expect(idpButtonLabel("undefined")).toBe(DEFAULT_IDP_LABEL);
    });

    it("falls back for absent, empty, and whitespace-only values", () => {
        expect(idpButtonLabel(undefined)).toBe(DEFAULT_IDP_LABEL);
        expect(idpButtonLabel(null)).toBe(DEFAULT_IDP_LABEL);
        expect(idpButtonLabel("")).toBe(DEFAULT_IDP_LABEL);
        expect(idpButtonLabel("   ")).toBe(DEFAULT_IDP_LABEL);
    });

    it("trims a padded name rather than rendering the padding", () => {
        expect(idpButtonLabel("  Midway  ")).toBe("Midway");
    });

    it("never returns an empty label", () => {
        // The button reads "Log in with {label}", so an empty value would render a dangling sentence.
        for (const value of [undefined, null, "", " ", "undefined"]) {
            expect(idpButtonLabel(value as any).length).toBeGreaterThan(0);
        }
    });
});
