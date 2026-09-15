/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Response security headers on the two web distribution paths.
 *
 * The CloudFront path sets headers through a `ResponseHeadersPolicy`; the ALB path can set only the
 * handful exposed as `routing.http.response.*` listener attributes. The asymmetry is a platform
 * limitation, so it is asserted rather than treated as drift — otherwise a reader cannot tell an
 * unsupported header from a forgotten one.
 *
 * The Permissions-Policy assertions are deliberately narrow. VAMS bundles viewers that enter WebXR
 * (three.js VRButton/ARButton, Babylon, PlayCanvas), read device orientation for camera control, and
 * build maps on OpenLayers, which reads geolocation. Denying any of those features breaks a viewer only
 * when that viewer is opened, so the policy names hardware APIs nothing in the tree uses and the test
 * pins both what is denied AND what must stay unrestricted.
 */

import * as fs from "fs";
import * as path from "path";
import { synthTemplate } from "../support/templateSynth";

/** The single ResponseHeadersPolicy config from a CloudFront-mode synthesis. */
function headersPolicyConfig(templateName: "commercial") {
    const synth = synthTemplate(templateName);
    const policies = synth.ofType("AWS::CloudFront::ResponseHeadersPolicy");
    return { synth, policies };
}

describe("CloudFront response security headers", () => {
    const { policies } = headersPolicyConfig("commercial");

    it("a response headers policy exists, so the assertions below are not vacuous", () => {
        // The commercial template is the CloudFront path; the ALB templates emit no such policy.
        expect(policies.length).toBeGreaterThan(0);
    });

    it("sets Referrer-Policy to strict-origin-when-cross-origin", () => {
        for (const policy of policies) {
            const security =
                policy.properties.ResponseHeadersPolicyConfig?.SecurityHeadersConfig ?? {};
            expect(security.ReferrerPolicy?.ReferrerPolicy).toBe("strict-origin-when-cross-origin");
            expect(security.ReferrerPolicy?.Override).toBe(true);
        }
    });

    it("keeps the headers that were already set", () => {
        // Control against a refactor of the policy dropping an existing header while adding new ones.
        for (const policy of policies) {
            const security =
                policy.properties.ResponseHeadersPolicyConfig?.SecurityHeadersConfig ?? {};
            expect(security.StrictTransportSecurity?.AccessControlMaxAgeSec).toBeGreaterThan(0);
            expect(security.ContentTypeOptions).toBeDefined();
            expect(security.FrameOptions?.FrameOption).toBe("SAMEORIGIN");
            expect(security.ContentSecurityPolicy?.ContentSecurityPolicy).toBeTruthy();
        }
    });

    describe("Permissions-Policy", () => {
        function permissionsPolicyValue(): string {
            for (const policy of policies) {
                const custom =
                    policy.properties.ResponseHeadersPolicyConfig?.CustomHeadersConfig?.Items ?? [];
                const hit = custom.find(
                    (h: { Header?: string }) => h.Header === "Permissions-Policy"
                );
                if (hit) return String(hit.Value ?? "");
            }
            return "";
        }

        it("is present", () => {
            expect(permissionsPolicyValue()).not.toBe("");
        });

        it.each(["microphone", "payment", "usb", "serial", "bluetooth", "hid", "midi"])(
            "denies %s",
            (feature) => {
                expect(permissionsPolicyValue()).toContain(`${feature}=()`);
            }
        );

        it.each([
            ["fullscreen", "the viewers offer a fullscreen control"],
            [
                "xr-spatial-tracking",
                "three.js VRButton/ARButton, Babylon and PlayCanvas enter WebXR",
            ],
            ["geolocation", "the Potree map builds on OpenLayers, which reads it"],
            ["accelerometer", "DeviceOrientation drives viewer camera control"],
            ["gyroscope", "DeviceOrientation drives viewer camera control"],
            ["camera", "immersive-ar sessions need it"],
        ])("does NOT restrict %s (%s)", (feature) => {
            // A denial added here would disable a shipped viewer feature, and only when that viewer is
            // opened — which no other test in this repository would catch.
            expect(permissionsPolicyValue()).not.toContain(`${feature}=`);
        });
    });
});

describe("the ALB path sets what the platform allows, and no more", () => {
    /**
     * Asserted on the listener attributes rather than on a headers policy: an Application Load Balancer
     * has no equivalent of `ResponseHeadersPolicy`, and emits only its documented
     * `routing.http.response.*` set. Referrer-Policy and Permissions-Policy have no attribute at all, so
     * the ALB deployment cannot carry them.
     */
    const source = fs.readFileSync(
        path.join(
            __dirname,
            "..",
            "..",
            "lib",
            "nestedStacks",
            "staticWebApp",
            "constructs",
            "alb-s3-website-albDeploy-construct.ts"
        ),
        "utf-8"
    ) as string;

    it("declares the three supported response headers", () => {
        expect(source).toContain("routing.http.response.strict_transport_security.header_value");
        expect(source).toContain("routing.http.response.x_content_type_options.header_value");
        expect(source).toContain("routing.http.response.x_frame_options.header_value");
    });

    it("records the headers it cannot set, so the gap is not read as an omission", () => {
        // If AWS later adds a listener attribute for either, this comment is what tells a reader the
        // ALB path is now behind rather than limited.
        expect(source).toContain("Referrer-Policy");
        expect(source).toContain("Permissions-Policy");
        expect(source).toContain("no ALB equivalent");
    });
});
