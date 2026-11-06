/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * @jest-environment node
 */

/**
 * The 7-day `min-release-age` floor in web/.npmrc does not reach the nested viewer installs that the
 * web `postinstall` chain runs (#334). npm reads a project `.npmrc` only from the directory holding
 * the package.json it installs, and it does not pass `min-release-age` down to a child `npm install`.
 * So:
 *
 * - every web/customInstalls/<viewer>/ directory with its own package.json carries a `.npmrc` that
 *   matches web/.npmrc, and
 * - every install script that runs `npm install` inside a cloned upstream repository (SuperSplat,
 *   Potree, Needle USD), where no VAMS-owned `.npmrc` exists, passes the floor on the command line.
 *
 * Durable guard (root CLAUDE.md Rule 13): a new viewer directory or a new install call fails here
 * until it is covered.
 */

import fs from "fs";
import path from "path";

const WEB_ROOT = path.resolve(__dirname, "..", "..", "..");
const CUSTOM_INSTALLS = path.join(WEB_ROOT, "customInstalls");

/**
 * Viewer directories with a package.json that are deliberately left without a `.npmrc`.
 * VEERUM installs from the vendor's private AWS CodeArtifact registry (see its README), which this
 * repository cannot test the release-age floor against; its install script runs only `npm run build`.
 */
const EXEMPT_PACKAGE_DIRS: Record<string, string> = {
    veerum: "installed manually from the vendor's private CodeArtifact registry",
};

/** Install scripts that run `npm install` inside an upstream clone rather than a VAMS package dir. */
const CLONE_INSTALL_SCRIPTS = [
    "supersplat/superSplatInstall.js",
    "potree/potreeInstall.js",
    "needletools-usd-viewer/usdViewerInstall.js",
];

/** `key = value` pairs of an .npmrc, ignoring comments and blank lines. */
function readNpmrc(file: string): Record<string, string> {
    const settings: Record<string, string> = {};
    for (const raw of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
        const line = raw.trim();
        if (!line || line.startsWith("#") || line.startsWith(";")) continue;
        const at = line.indexOf("=");
        if (at < 0) continue;
        settings[line.slice(0, at).trim()] = line.slice(at + 1).trim();
    }
    return settings;
}

const webNpmrc = readNpmrc(path.join(WEB_ROOT, ".npmrc"));

const packageDirs = fs
    .readdirSync(CUSTOM_INSTALLS, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .filter((name) => fs.existsSync(path.join(CUSTOM_INSTALLS, name, "package.json")))
    .sort();

/** The command strings passed to execSync in a script that start with `npm install`. */
function npmInstallCommands(source: string): string[] {
    const commands: string[] = [];
    const call = /execSync\(\s*(["'`])(npm install[^"'`]*)\1/g;
    let match: RegExpExecArray | null;
    while ((match = call.exec(source)) !== null) commands.push(match[2]);
    return commands;
}

describe("web/.npmrc carries the intake floor the nested installs copy", () => {
    it("sets min-release-age and engine-strict", () => {
        expect(webNpmrc["min-release-age"]).toBe("7");
        expect(webNpmrc["engine-strict"]).toBe("true");
    });
});

describe("viewer package directories carry their own .npmrc (#334)", () => {
    it("finds the viewer package directories", () => {
        // Count control: a reader that matched nothing would make every check below vacuous.
        expect(packageDirs.length).toBeGreaterThanOrEqual(5);
        expect(packageDirs).toEqual(expect.arrayContaining(["cesium", "online3dviewer"]));
    });

    it.each(packageDirs.filter((dir) => !(dir in EXEMPT_PACKAGE_DIRS)))(
        "%s/.npmrc matches web/.npmrc",
        (dir) => {
            const file = path.join(CUSTOM_INSTALLS, dir, ".npmrc");
            expect(fs.existsSync(file)).toBe(true);
            expect(readNpmrc(file)).toEqual(webNpmrc);
        }
    );

    it("exempts only directories that exist", () => {
        Object.keys(EXEMPT_PACKAGE_DIRS).forEach((dir) => expect(packageDirs).toContain(dir));
    });
});

describe("installs inside upstream clones pass the floor on the command line (#334)", () => {
    const floor = `--min-release-age=${webNpmrc["min-release-age"]}`;

    it.each(CLONE_INSTALL_SCRIPTS)("%s", (script) => {
        const commands = npmInstallCommands(
            fs.readFileSync(path.join(CUSTOM_INSTALLS, script), "utf8")
        );
        expect(commands.length).toBeGreaterThan(0);
        expect(commands.filter((command) => !command.includes(floor))).toEqual([]);
    });

    it("[control] reports a bare npm install", () => {
        const commands = npmInstallCommands('execSync("npm install", { cwd: dir });');
        expect(commands).toEqual(["npm install"]);
        expect(commands.filter((command) => !command.includes(floor))).toHaveLength(1);
    });

    it("lists every install script that clones an upstream repository", () => {
        // A script that clones a repository and installs in it needs the command-line floor; one
        // that is not listed above would escape the check.
        const cloning = fs
            .readdirSync(CUSTOM_INSTALLS, { withFileTypes: true })
            .filter((entry) => entry.isDirectory())
            .flatMap((entry) =>
                fs
                    .readdirSync(path.join(CUSTOM_INSTALLS, entry.name))
                    .filter((name) => name.endsWith(".js"))
                    .map((name) => `${entry.name}/${name}`)
            )
            .filter((script) => {
                const source = fs.readFileSync(path.join(CUSTOM_INSTALLS, script), "utf8");
                return /git clone|\.clone\(/.test(source) && npmInstallCommands(source).length > 0;
            })
            .sort();
        expect(cloning).toEqual([...CLONE_INSTALL_SCRIPTS].sort());
    });
});
