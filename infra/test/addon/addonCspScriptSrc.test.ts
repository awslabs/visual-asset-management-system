/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** `generateContentSecurityPolicy()` merges the add-on `scriptSrc` lane into `script-src`. */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as AddonCsp from "../../lib/nestedStacks/addon/addonCsp";
import { generateContentSecurityPolicy } from "../../lib/helper/security";

const TEMPLATES: Record<string, { region: string; partition: string }> = {
    commercial: { region: "us-east-1", partition: "aws" },
    govcloud: { region: "us-gov-west-1", partition: "aws-us-gov" },
    eusovereign: { region: "eusc-de-east-1", partition: "aws-eusc" },
};

function templateConfig(name: string, mutate?: (c: any) => void): Config.Config {
    const config = JSON.parse(
        fs.readFileSync(
            path.join(__dirname, "..", "..", "config", `config.template.${name}.json`),
            "utf8"
        )
    );
    config.env.account = "123456789012";
    config.env.region = TEMPLATES[name].region;
    config.env.partition = TEMPLATES[name].partition;
    mutate?.(config);
    return config as Config.Config;
}

function policy(config: Config.Config): string {
    Service.SetConfig(config);
    return generateContentSecurityPolicy(
        {} as any,
        "https://auth.example.com",
        "api.example.com",
        config
    );
}

function directive(csp: string, name: string): string[] {
    const found = csp
        .split(";")
        .map((part) => part.trim())
        .find((part) => part.startsWith(`${name} `));
    if (!found) throw new Error(`no ${name} directive in: ${csp}`);
    return found.split(/\s+/).slice(1);
}

/** Every directive except the one named, for checking that a merge touched only its own. */
function otherDirectives(csp: string, name: string): string[] {
    return csp
        .split(";")
        .map((part) => part.trim())
        .filter((part) => part.length > 0 && !part.startsWith(`${name} `));
}

/** Replaces the add-on sources with a stand-in add-on that contributes the given script sources. */
function withAddonScriptSrc(scriptSrc: string[]): void {
    jest.spyOn(AddonCsp, "addonCspSources").mockReturnValue({
        connectSrc: [],
        frameSrc: [],
        scriptSrc,
    });
}

const silenceConfigLog = () => jest.spyOn(console, "log").mockImplementation(() => undefined);

describe("add-on script-src lane", () => {
    beforeEach(() => {
        silenceConfigLog();
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    describe.each(Object.keys(TEMPLATES))("%s template", (name) => {
        it("contributes no add-on source with every add-on off", () => {
            expect(AddonCsp.addonCspSources(templateConfig(name))).toEqual({
                connectSrc: [],
                frameSrc: [],
                scriptSrc: [],
            });
        });

        it("merges an add-on script source into script-src and nowhere else", () => {
            const config = templateConfig(name);
            const before = policy(config);

            withAddonScriptSrc(["blob:", "https://cdn.addon.example"]);
            const after = policy(config);

            expect(directive(after, "script-src")).toEqual([
                ...directive(before, "script-src"),
                "blob:",
                "https://cdn.addon.example",
            ]);
            expect(otherDirectives(after, "script-src")).toEqual(
                otherDirectives(before, "script-src")
            );
        });

        it("does not repeat a source script-src already carries", () => {
            const config = templateConfig(name);
            const before = directive(policy(config), "script-src");

            withAddonScriptSrc(["'self'", "https://cdn.addon.example"]);

            expect(directive(policy(config), "script-src")).toEqual([
                ...before,
                "https://cdn.addon.example",
            ]);
        });
    });
});
