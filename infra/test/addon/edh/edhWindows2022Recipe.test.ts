/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The shape of the shipped Windows Server 2022 desktop recipe, decided from the files on disk
 * without a build: its components, and the screen-lock component that keeps an idle desktop from
 * asking for the Windows password again. Nothing here runs PowerShell; the registry paths are the
 * ones Microsoft documents for each policy.
 */

import * as fs from "fs";
import * as path from "path";
import { loadEdhAmiRecipe } from "../../../config/addon/edhWorkstationsConfig";

const REPO = path.join(__dirname, "..", "..", "..", "..");
const DIR = "backendAddons/edh/amiRecipes/windows2022-desktop";
const SCREEN_LOCK_COMPONENT = "020-no-screen-lock.yml";

const loaded = loadEdhAmiRecipe(REPO, DIR, false);
const componentBodies = loaded.componentFiles.map((relative) => ({
    relative,
    body: fs.readFileSync(path.join(REPO, relative), "utf8"),
}));

function component(name: string): string {
    const found = componentBodies.find(({ relative }) => path.basename(relative) === name);
    if (!found) {
        throw new Error(`${DIR} installs no ${name}`);
    }
    return found.body;
}

/** The text of one phase of a component document, from its `- name:` line to the next phase. */
function phase(body: string, name: string): string {
    const start = body.indexOf(`    - name: ${name}\n`);
    if (start < 0) {
        throw new Error(`the component has no ${name} phase`);
    }
    const next = body.indexOf("\n    - name: ", start + 1);
    return body.slice(start, next < 0 ? undefined : next + 1);
}

/** The machine policies the component sets: Computer Configuration keys under HKLM. */
const MACHINE_POLICIES = [
    // Control Panel > Personalization > Do not display the lock screen.
    {
        key: "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\Personalization",
        name: "NoLockScreen",
        value: 1,
    },
    // System > Power Management > Sleep Settings > Require a password when a computer wakes
    // (plugged in, and on battery).
    {
        key: "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Power\\PowerSettings\\0e796bdb-100d-47d6-a2d5-f7d2daa51f51",
        name: "ACSettingIndex",
        value: 0,
    },
    {
        key: "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Power\\PowerSettings\\0e796bdb-100d-47d6-a2d5-f7d2daa51f51",
        name: "DCSettingIndex",
        value: 0,
    },
];
/**
 * The screen saver policies are User Configuration policies, read from each user's own
 * HKCU\Software\Policies key, so the component writes them into the default user profile that
 * every new account's profile, the one EDH creates for the desktop user included, is copied from.
 */
const USER_POLICY_KEY = "Software\\Policies\\Microsoft\\Windows\\Control Panel\\Desktop";
const USER_POLICIES = ["ScreenSaveActive", "ScreenSaverIsSecure", "ScreenSaveTimeOut"];

describe("the shipped Windows Server 2022 desktop recipe", () => {
    test("runs the AWS update component, then the baseline and the screen-lock component", () => {
        expect(loaded.manifest.osFamily).toBe("windows");
        expect(loaded.manifest.components[0].arn).toBe(
            "arn:${AWS::Partition}:imagebuilder:${AWS::Region}:aws:component/update-windows/x.x.x"
        );
        expect(loaded.componentFiles.map((f) => path.basename(f))).toEqual([
            "010-baseline.yml",
            SCREEN_LOCK_COMPONENT,
        ]);
    });

    test("every component is a schemaVersion 1.0 PowerShell document with a build and a validate phase", () => {
        for (const { relative, body } of componentBodies) {
            expect(body).toContain("schemaVersion: 1.0");
            expect(body).toMatch(/^ {4}- name: build$/m);
            expect(body).toMatch(/^ {4}- name: validate$/m);
            expect(
                body.match(/action: (\w+)/g)?.every((a) => a === "action: ExecutePowerShell")
            ).toBe(true);
            // AMI_RECIPE_COMPONENT_DATA_MAX_BYTES: CreateComponent carries the document inline.
            expect(Buffer.byteLength(body, "utf8")).toBeLessThanOrEqual(16000);
            expect(relative.startsWith(`${DIR}/components/`)).toBe(true);
        }
    });
});

describe("the Windows screen-lock component", () => {
    // Read inside each test, so a recipe without the component fails each check on its own.
    let body = "";
    let build = "";
    let validate = "";
    beforeEach(() => {
        body = component(SCREEN_LOCK_COMPONENT);
        build = phase(body, "build");
        validate = phase(body, "validate");
    });

    test("sets each machine policy as a DWORD, creating its key only when it is missing", () => {
        for (const { key, name, value } of MACHINE_POLICIES) {
            expect({ name, set: build.includes(`'${key}'`) }).toEqual({ name, set: true });
            expect(build).toMatch(
                new RegExp(
                    `New-ItemProperty -Path \\$\\w+ -Name ${name} -PropertyType DWord -Value ${value} -Force`
                )
            );
        }
        // New-Item -Force on a registry key that exists replaces it, dropping the values a cluster
        // or an administrator already set there, so each key is created only behind Test-Path.
        const creates = build.match(/New-Item -Path \$\w+/g) ?? [];
        expect(creates.length).toBeGreaterThan(0);
        for (const create of creates) {
            const variable = create.slice("New-Item -Path ".length);
            expect(build).toContain(
                `if (-not (Test-Path ${variable})) { ${create} -Force | Out-Null }`
            );
        }
    });

    test("turns the display and sleep timeouts off, failing the build when powercfg fails", () => {
        for (const setting of ["monitor-timeout-ac", "standby-timeout-ac"]) {
            expect(build).toContain(`powercfg /change ${setting} 0\n`);
            expect(build).toContain(
                `if ($LASTEXITCODE -ne 0) { throw "powercfg /change ${setting} exited $LASTEXITCODE" }`
            );
        }
    });

    test("writes the screen saver policies off into the default user profile, then unloads it", () => {
        expect(build).toContain("reg.exe load $hive 'C:\\Users\\Default\\NTUSER.DAT'");
        expect(build).toContain(`$desktop = "$hive\\${USER_POLICY_KEY}"`);
        expect(build).toContain(
            `foreach ($name in ${USER_POLICIES.map((p) => `'${p}'`).join(", ")})`
        );
        expect(build).toContain("reg.exe add $desktop /v $name /t REG_SZ /d 0 /f");
        // The hive is unloaded on every path, and a failed unload fails the build: the unload is
        // what writes the change to the profile file before the image is captured.
        expect(build).toMatch(/\} finally \{\n\s+\[GC\]::Collect\(\)\n\s+& reg\.exe unload \$hive/);
        expect(build).toContain(
            'throw "reg.exe unload of the default user profile exited $LASTEXITCODE"'
        );
        // The documented location is the user's own Software\Policies key, never a machine one.
        expect(body).not.toContain("HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\Control Panel");
    });

    test("the validate phase reads every policy back and throws on a missing or wrong value", () => {
        expect(validate).toContain("$ErrorActionPreference = 'Stop'");
        for (const { key, name, value } of MACHINE_POLICIES) {
            expect({ name, read: validate.includes(`'${key}'`) }).toEqual({ name, read: true });
            expect(validate).toMatch(new RegExp(`\\.${name} -ne ${value}\\) \\{ throw `));
        }
        expect(validate).toContain("reg.exe load $hive 'C:\\Users\\Default\\NTUSER.DAT'");
        expect(validate).toContain(
            `foreach ($name in ${USER_POLICIES.map((p) => `'${p}'`).join(", ")})`
        );
        expect(validate).toContain("reg.exe query $desktop /v $name");
        expect(validate).toContain('-match "^\\s+$name\\s+REG_SZ\\s+0\\s*$"');
        expect(validate).toMatch(
            /\} finally \{\n\s+\[GC\]::Collect\(\)\n\s+& reg\.exe unload \$hive/
        );
    });

    test("leaves the Windows sign-in and the Amazon DCV settings alone", () => {
        // The first sign-in uses the password VAMS shows, and EDH's first boot writes the DCV
        // settings, including the lock when the last connection closes.
        for (const text of [
            "Winlogon",
            "AutoAdminLogon",
            "DisableCAD",
            "nicesoftware",
            "os-auto-lock",
        ]) {
            expect({ text, hit: body.includes(text) }).toEqual({ text, hit: false });
        }
    });
});
