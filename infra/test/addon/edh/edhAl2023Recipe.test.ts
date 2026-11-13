/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The shape of the shipped Amazon Linux 2023 desktop recipe, and the sign-in and screen-lock
 * defaults every shipped Linux desktop recipe installs, decided from the files on disk without a
 * build.
 *
 * The session sign-in, the profile cleanup and the screen-lock defaults are byte-identical copies
 * of the Isaac Sim recipe's components, so the runtime suites in edhIsaacSimRecipe.test.ts exercise
 * the scripts both recipes install, and the screen-lock suite here runs the one component's build.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { spawnSync } from "child_process";
import {
    EDH_SESSION_SECRET_PREFIX_MAX_LENGTH,
    EDH_SHIPPED_AMI_RECIPE_DIRS,
    loadEdhAmiRecipe,
} from "../../../config/addon/edhWorkstationsConfig";

const REPO = path.join(__dirname, "..", "..", "..", "..");
const RECIPES = "backendAddons/edh/amiRecipes";
const DIR = `${RECIPES}/al2023-desktop`;
const CLI_PATH = "/opt/vams/vamscli-venv/bin/vamscli";
/** The components that install the CLI, sign it in at login and clear deleted desktops' profiles. */
const SIGN_IN_COMPONENTS = [
    "040-vamscli.yml",
    "060-session-bootstrap.yml",
    "065-profile-cleanup.yml",
];
/** The component that turns the GNOME screen lock and idle blanking off. */
const SCREEN_LOCK_COMPONENT = "066-no-screen-lock.yml";
/** The ones every shipped Linux desktop recipe carries byte for byte. */
const SHARED_COMPONENTS = [
    "060-session-bootstrap.yml",
    "065-profile-cleanup.yml",
    SCREEN_LOCK_COMPONENT,
];

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

// The deployment values a component reaches as ${DEPLOY_<key>}: the closed set the image builder
// substitutes, read from the builder itself.
const builderSource = fs.readFileSync(
    path.join(REPO, "backend/backend/handlers/addon/edh/common/edhImageBuilder.py"),
    "utf8"
);
const DEPLOYMENT_KEYS = [
    ...(/^DEPLOYMENT_PARAMETERS = \{([\s\S]*?)^\}/m.exec(builderSource)?.[1] ?? "").matchAll(
        /^\s+"(\w+)": \(/gm
    ),
].map((match) => match[1]);

/**
 * A component document with its ${DEPLOY_<key>} placeholders resolved as the image builder resolves
 * them, in one pass; a key outside the closed set, or one given no value, is refused.
 */
function withDeploymentValues(body: string, values: Record<string, string>): string {
    return body.replace(/\$\{DEPLOY_([^}]*)\}/g, (token, key: string) => {
        if (!DEPLOYMENT_KEYS.includes(key) || !values[key]) {
            throw new Error(`unresolved placeholder in component document: ${token}`);
        }
        return values[key];
    });
}

/** Every dnf or yum install line of a component, with the package and group names it names. */
function dnfInstalls(body: string): { line: string; names: string[] }[] {
    return body
        .split("\n")
        .filter((line) => /\b(dnf|yum)\b.*\binstall\b/.test(line))
        .map((line) => ({
            line: line.trim(),
            names: line
                .replace(/^.*?\binstall\b/, "")
                .trim()
                .split(/\s+/)
                .filter((word) => word && !word.startsWith("-")),
        }));
}

describe("the shipped Amazon Linux 2023 desktop recipe", () => {
    test("is an Amazon Linux 2023 x86_64 desktop on the moving 6.1 kernel image", () => {
        expect(loaded.recipeId).toBe("al2023-desktop");
        expect(loaded.manifest.kind).toBe("vdi");
        expect(loaded.manifest.osFamily).toBe("linux");
        expect(loaded.manifest.baseOs).toBe("amazonlinux2023");
        expect(loaded.manifest.architecture).toBe("x86_64");
        expect(loaded.manifest.parentImage).toBe(
            "ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64"
        );
        expect(loaded.manifest.buildInstanceType).toBe("m7i.large");
        expect(loaded.manifest.rootSizeGb).toBe(60);
        expect(loaded.manifest.profileId).toBe(1);
    });

    test("runs the AWS update component, then the baseline, the CLI, the sign-in, the cleanup and the screen-lock defaults", () => {
        expect(loaded.manifest.components[0].arn).toBe(
            "arn:${AWS::Partition}:imagebuilder:${AWS::Region}:aws:component/update-linux/x.x.x"
        );
        expect(loaded.componentFiles.map((f) => path.basename(f))).toEqual([
            "010-baseline.yml",
            ...SIGN_IN_COMPONENTS,
            SCREEN_LOCK_COMPONENT,
        ]);
    });

    test("declares the CLI repository asset it installs from", () => {
        expect(loaded.assets).toEqual([{ id: "vamscli", repoPath: "tools/VamsCLI" }]);
    });

    test("every asset and deployment placeholder a component uses is declared, and both are used", () => {
        const declared = new Set([
            ...loaded.assets.map((a) => `\${ASSET_${a.id}}`),
            ...DEPLOYMENT_KEYS.map((k) => `\${DEPLOY_${k}}`),
        ]);
        const used = new Set<string>();
        for (const { body } of componentBodies) {
            for (const token of body.match(/\$\{(ASSET|PARAM|DEPLOY)_([^}]*)\}/g) ?? []) {
                used.add(token);
            }
        }
        expect([...used].filter((token) => !declared.has(token))).toEqual([]);
        // The CLI is fetched from its asset, and the sign-in reads the session secrets under the
        // prefix the deployment configures.
        expect(used.has("${ASSET_vamscli}")).toBe(true);
        expect(used.has("${DEPLOY_sessionSecretPrefix}")).toBe(true);
    });

    test("every component is a schemaVersion 1.0 document with a build phase, inside the recipe", () => {
        for (const { relative, body } of componentBodies) {
            expect(body).toContain("schemaVersion: 1.0");
            expect(body).toMatch(/^\s+- name: build$/m);
            // AMI_RECIPE_COMPONENT_DATA_MAX_BYTES: CreateComponent carries the document inline.
            expect(Buffer.byteLength(body, "utf8")).toBeLessThanOrEqual(16000);
            expect(relative.startsWith(`${DIR}/components/`)).toBe(true);
        }
    });

    test("every component stays within the limit resolved with the longest prefix getConfig() accepts", () => {
        const longest = {
            sessionSecretPrefix: `${"a".repeat(EDH_SESSION_SECRET_PREFIX_MAX_LENGTH - 1)}/`,
        };
        for (const { body } of componentBodies) {
            expect(
                Buffer.byteLength(withDeploymentValues(body, longest), "utf8")
            ).toBeLessThanOrEqual(16000);
        }
    });

    test("the CLI virtual environment path is spelled the same everywhere it is used", () => {
        const users = componentBodies.filter(({ body }) => body.includes(CLI_PATH));
        expect(users.map(({ relative }) => path.basename(relative)).sort()).toEqual(
            SIGN_IN_COMPONENTS
        );
        for (const { relative, body } of componentBodies) {
            const spellings = body.match(/\/opt\/vams\/[^\s"']*vamscli(?=[\s"'])/g) ?? [];
            expect({ relative, spellings: [...new Set(spellings)] }).toEqual({
                relative,
                spellings: spellings.length ? [CLI_PATH] : [],
            });
        }
    });

    test("the CLI environment is built from python3.12 and fetched with the image's AWS CLI", () => {
        // Amazon Linux 2023 keeps python3 at 3.9 and ships the AWS CLI v2 at /usr/bin/aws; the CLI
        // needs Python 3.12.
        const cli = component("040-vamscli.yml");
        expect(cli).toContain(
            '/usr/bin/aws s3 cp --recursive --only-show-errors "${ASSET_vamscli}" /opt/vams/vamscli-src'
        );
        expect(cli).toContain("/usr/bin/python3.12 -m venv /opt/vams/vamscli-venv");
        expect(cli).not.toContain("/usr/local/bin/aws");
        expect(cli).not.toMatch(/(^|\s)python3 -m venv/m);
        expect(dnfInstalls(component("010-baseline.yml")).flatMap(({ names }) => names)).toContain(
            "python3.12"
        );
    });

    test("the recipe installs no DCV server, desktop environment, GPU driver or gnome-terminal", () => {
        // EDH's amazonlinux2023 first boot installs the DCV server and the GNOME desktop, and skips
        // the desktop altogether when gnome-terminal is already installed.
        const installs = componentBodies.flatMap(({ relative, body }) =>
            dnfInstalls(body).map((install) => ({ relative, ...install }))
        );
        expect(installs.length).toBeGreaterThan(0);
        const forbidden =
            /^(gnome-terminal|gnome-shell|gnome-session.*|gdm|nice-dcv.*|nvidia-.*|cuda-.*|kmod-nvidia.*|@.*)$/;
        for (const { relative, line, names } of installs) {
            expect({
                relative,
                line,
                group: /\bgroup\s*install\b|groupinstall/.test(line),
            }).toEqual({
                relative,
                line,
                group: false,
            });
            for (const name of names) {
                expect({ relative, name, forbidden: forbidden.test(name) }).toEqual({
                    relative,
                    name,
                    forbidden: false,
                });
            }
        }
        for (const { relative, body } of componentBodies) {
            for (const text of ["nice-dcv", "nvidia-driver", "cuda-drivers"]) {
                expect({ relative, hit: body.includes(text) }).toEqual({ relative, hit: false });
            }
        }
    });

    test("the baseline's validate checks the tools the sign-in runs and refuses gnome-terminal", () => {
        const baseline = component("010-baseline.yml");
        const validate = baseline.slice(baseline.indexOf("- name: validate"));
        expect(validate).toContain("aws --version 2>&1 | grep 'aws-cli/2'");
        expect(validate).toContain("/usr/bin/python3.12 -c 'import venv, ensurepip'");
        // One name per check: command -v succeeds when any one of several names resolves.
        for (const tool of ["curl", "jq", "flock"]) {
            expect(validate).toContain(`- command -v ${tool}\n`);
        }
        expect(validate).not.toMatch(/command -v \S+ \S+/);
        expect(validate).toContain("if rpm -q gnome-terminal; then exit 1; fi");
    });

    test("the sign-in keeps its error capture out of the shared temporary directory", () => {
        const bootstrap = component("060-session-bootstrap.yml");
        expect(bootstrap).toContain('ERR="${HOST_DIR}/session-bootstrap.err"');
        expect(bootstrap).not.toContain("/tmp/");
    });
});

describe("the shipped Linux desktop recipes", () => {
    const recipes = EDH_SHIPPED_AMI_RECIPE_DIRS.map((dir) => loadEdhAmiRecipe(REPO, dir, false));
    const signInOf = (recipe: (typeof recipes)[number]) =>
        recipe.componentFiles
            .map((f) => path.basename(f))
            .filter((name) => SIGN_IN_COMPONENTS.includes(name));

    test("each installs the CLI, the session sign-in and the profile cleanup, from the CLI asset", () => {
        const linuxDesktops = recipes.filter(
            (r) => r.manifest.osFamily === "linux" && r.manifest.kind === "vdi"
        );
        expect(linuxDesktops.map((r) => r.recipeId).sort()).toEqual(["al2023-desktop", "isaacsim"]);
        for (const recipe of linuxDesktops) {
            expect({
                recipe: recipe.recipeId,
                signIn: signInOf(recipe),
                cli: recipe.assets.filter((asset) => asset.id === "vamscli"),
            }).toEqual({
                recipe: recipe.recipeId,
                signIn: SIGN_IN_COMPONENTS,
                cli: [{ id: "vamscli", repoPath: "tools/VamsCLI" }],
            });
        }
    });

    test("a Windows desktop recipe installs none of them and declares no asset", () => {
        const windows = recipes.filter((r) => r.manifest.osFamily === "windows");
        expect(windows.map((r) => r.recipeId)).toEqual(["windows2022-desktop"]);
        for (const recipe of windows) {
            expect({
                recipe: recipe.recipeId,
                signIn: signInOf(recipe),
                assets: recipe.assets,
            }).toEqual({ recipe: recipe.recipeId, signIn: [], assets: [] });
        }
    });

    test("only the Isaac Sim recipe restricts the instance types its desktops launch on", () => {
        const restricting = recipes
            .filter((r) => r.manifest.launchInstanceTypes !== undefined)
            .map((r) => r.recipeId);
        expect(restricting).toEqual(["isaacsim"]);
        const general = recipes.filter((r) =>
            ["al2023-desktop", "windows2022-desktop"].includes(r.recipeId)
        );
        expect(general.map((r) => r.recipeId).sort()).toEqual([
            "al2023-desktop",
            "windows2022-desktop",
        ]);
        for (const recipe of general) {
            expect(recipe.manifest.launchInstanceTypes).toBeUndefined();
        }
    });
});

describe("the components the Linux desktop recipes share", () => {
    const read = (recipeId: string, name: string) =>
        fs.readFileSync(path.join(REPO, RECIPES, recipeId, "components", name));

    test("are byte-identical in both recipes", () => {
        for (const name of SHARED_COMPONENTS) {
            expect({
                name,
                identical: read("al2023-desktop", name).equals(read("isaacsim", name)),
            }).toEqual({ name, identical: true });
        }
    });

    test("name neither recipe nor its operating system, so one text reads true in both", () => {
        for (const name of SHARED_COMPONENTS) {
            const text = read("isaacsim", name).toString("utf8");
            expect({
                name,
                mentions: /isaac|ubuntu|amazon linux|al2023|\blauncher\b/i.test(text),
            }).toEqual({
                name,
                mentions: false,
            });
        }
    });

    test("leave the CLI component each recipe's own", () => {
        // The Isaac Sim image installs its own AWS CLI and builds the environment from the system
        // Python 3.12; Amazon Linux 2023 uses the image's AWS CLI and the python3.12 package.
        expect(
            read("al2023-desktop", "040-vamscli.yml").equals(read("isaacsim", "040-vamscli.yml"))
        ).toBe(false);
    });
});

/** A line's leading spaces. */
function indentOf(line: string): number {
    return line.length - line.trimStart().length;
}

/**
 * The commands of one phase of a component document, in order, each as bash receives it: a plain
 * list item as written, and a literal block (`- |`) with the indentation YAML strips from it
 * removed, so a heredoc terminator written one column off is no longer a terminator. YAML comments
 * between the items are dropped.
 */
function phaseCommands(body: string, phase: string): string[] {
    const lines = body.split("\n");
    const start = lines.findIndex((line) => line.trim() === `- name: ${phase}`);
    if (start < 0) {
        throw new Error(`the component has no ${phase} phase`);
    }
    const phaseIndent = indentOf(lines[start]);
    const next = lines.findIndex(
        (line, i) =>
            i > start && line.trim().startsWith("- name: ") && indentOf(line) === phaseIndent
    );
    const end = next < 0 ? lines.length : next;
    const commands: string[] = [];
    // -1 outside a commands list, 0 at its first item, else the indentation of its items.
    let listIndent = -1;
    for (let i = start + 1; i < end; i++) {
        const line = lines[i];
        if (line.trim() === "commands:") {
            listIndent = 0;
            continue;
        }
        if (listIndent < 0 || !line.trim() || line.trim().startsWith("#")) {
            continue;
        }
        if (listIndent === 0) {
            listIndent = indentOf(line);
        }
        if (indentOf(line) < listIndent) {
            listIndent = -1;
            continue;
        }
        if (line.trim() === "- |") {
            const block: string[] = [];
            let blockIndent = -1;
            let j = i + 1;
            for (; j < end; j++) {
                if (!lines[j].trim()) {
                    block.push("");
                    continue;
                }
                if (blockIndent < 0) {
                    blockIndent = indentOf(lines[j]);
                }
                if (indentOf(lines[j]) < blockIndent) {
                    break;
                }
                block.push(lines[j].slice(blockIndent));
            }
            while (block.length && block[block.length - 1] === "") {
                block.pop();
            }
            commands.push(block.join("\n"));
            i = j - 1;
        } else if (line.trim().startsWith("- ")) {
            commands.push(line.trim().slice(2));
        }
    }
    return commands;
}

/** The body of the quoted heredoc `cat > <file> <<'<tag>'` in a script, or undefined without one. */
function heredoc(script: string, file: string, tag: string): string | undefined {
    const lines = script.split("\n");
    const start = lines.indexOf(`cat > ${file} <<'${tag}'`);
    const end = lines.findIndex((line, i) => i > start && line === tag);
    return start < 0 || end < 0 ? undefined : lines.slice(start + 1, end).join("\n") + "\n";
}

/** A dconf keyfile's groups, each with its keys and their values as written. */
function keyfileGroups(text: string): Record<string, Record<string, string>> {
    const groups: Record<string, Record<string, string>> = {};
    let group = "";
    for (const line of text.split("\n")) {
        const header = /^\[([^\]]+)\]$/.exec(line);
        if (header) {
            group = header[1];
            groups[group] = {};
        } else if (line.trim() && !line.startsWith("#")) {
            const at = line.indexOf("=");
            groups[group][line.slice(0, at)] = line.slice(at + 1);
        }
    }
    return groups;
}

describe("the screen-lock defaults the Linux desktop recipes install", () => {
    // EDH creates the desktop user with a local password nobody holds, and Amazon DCV signs the
    // connection in, so a GNOME screen lock locks the user out of their own desktop.
    const PROFILE = "/etc/dconf/profile/user";
    const DEFAULTS = "/etc/dconf/db/local.d/00-vams-no-screen-lock";
    const LOCKS = "/etc/dconf/db/local.d/locks/00-vams-no-screen-lock";
    /** The user profile the dconf package of Amazon Linux 2023 ships (dconf-0.40.0-3.amzn2023). */
    const DISTRIBUTION_PROFILE =
        "user-db:user\nsystem-db:local\nsystem-db:site\nsystem-db:distro\n";
    const body = fs.readFileSync(
        path.join(REPO, RECIPES, "isaacsim", "components", SCREEN_LOCK_COMPONENT),
        "utf8"
    );
    const build = phaseCommands(body, "build");
    const validate = phaseCommands(body, "validate");
    const buildScript = build.join("\n") + "\n";

    /** Runs `fn` with a scratch directory, removed afterwards, as a forward-slash path. */
    function inScratch<T>(fn: (root: string) => T): T {
        const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "vams-dconf-"));
        try {
            // Forward slashes, so the same paths work under Git Bash on a Windows checkout.
            return fn(scratch.split(path.sep).join("/"));
        } finally {
            fs.rmSync(scratch, { recursive: true, force: true });
        }
    }

    /** A phase's script with /etc/dconf moved under `root`, behind the given stub functions. */
    function rooted(root: string, commands: string[], stubs: string[]): string {
        return [...stubs, commands.join("\n").split("/etc/dconf").join(`${root}/etc/dconf`)].join(
            "\n"
        );
    }

    /** dconf, apt-get and dnf stubs that record their calls in `${root}/calls`. */
    function recordingStubs(root: string): string[] {
        return [
            `dconf() { printf '%s\\n' "dconf $*" >> '${root}/calls'; }`,
            `apt-get() { printf '%s\\n' "apt-get $*" >> '${root}/calls'; return 1; }`,
            `dnf() { printf '%s\\n' "dnf $*" >> '${root}/calls'; return 1; }`,
        ];
    }

    /**
     * Runs the build phase `times` times in real bash against a scratch /etc/dconf, holding `seed` as
     * the user profile beforehand when given. dconf, apt-get and dnf only record their calls. Returns
     * the exit status and stderr of the last run, the profile after each run, the files written and
     * the calls.
     */
    function runBuild(seed?: string, times = 1) {
        return inScratch((root) => {
            if (seed !== undefined) {
                fs.mkdirSync(`${root}/etc/dconf/profile`, { recursive: true });
                fs.writeFileSync(`${root}${PROFILE}`, seed);
            }
            const script = rooted(root, build, recordingStubs(root));
            const profiles: string[] = [];
            let run = spawnSync("bash", ["-c", script], { encoding: "utf8" });
            for (let i = 0; i < times; i++) {
                if (i > 0) {
                    run = spawnSync("bash", ["-c", script], { encoding: "utf8" });
                }
                profiles.push(fs.readFileSync(`${root}${PROFILE}`, "utf8"));
            }
            return {
                status: run.status,
                stderr: run.stderr,
                profiles,
                defaults: fs.readFileSync(`${root}${DEFAULTS}`, "utf8"),
                locks: fs.readFileSync(`${root}${LOCKS}`, "utf8"),
                calls: fs.readFileSync(`${root}/calls`, "utf8").split("\n").filter(Boolean),
            };
        });
    }

    /**
     * Runs the build phase, then the validate phase, in real bash against a scratch /etc/dconf. A
     * placeholder stands in for the compiled database, `change` edits the tree between the phases,
     * and the validate phase's dconf answers each read with `answers[key]`, recording the profile
     * it was given. Returns the validate phase's exit status and dconf's calls.
     */
    function runValidate(answers: Record<string, string>, change?: (root: string) => void) {
        return inScratch((root) => {
            const built = spawnSync("bash", ["-c", rooted(root, build, recordingStubs(root))], {
                encoding: "utf8",
            });
            expect(built.status).toBe(0);
            fs.writeFileSync(`${root}/etc/dconf/db/local`, "compiled");
            fs.writeFileSync(`${root}/calls`, "");
            change?.(root);
            const answer = Object.entries(answers)
                .map(([key, value]) => `${key}) echo '${value}' ;;`)
                .join(" ");
            const stub =
                `dconf() { printf '%s with %s\\n' "dconf $*" "$(tr '\\n' ' ' < "\${DCONF_PROFILE}")" >> '${root}/calls'; ` +
                `case "$2" in ${answer} esac; }`;
            const run = spawnSync("bash", ["-c", rooted(root, validate, [stub])], {
                encoding: "utf8",
            });
            return {
                status: run.status,
                calls: fs.readFileSync(`${root}/calls`, "utf8").split("\n").filter(Boolean),
            };
        });
    }

    const COMPILED = {
        "/org/gnome/desktop/screensaver/lock-enabled": "false",
        "/org/gnome/desktop/lockdown/disable-lock-screen": "true",
    };

    test("each Linux desktop recipe installs them after the profile cleanup, and Windows does not", () => {
        const recipes = EDH_SHIPPED_AMI_RECIPE_DIRS.map((dir) =>
            loadEdhAmiRecipe(REPO, dir, false)
        );
        for (const recipe of recipes) {
            const names = recipe.componentFiles.map((f) => path.basename(f));
            const linux = recipe.manifest.osFamily === "linux";
            expect({
                recipe: recipe.recipeId,
                installs: names.includes(SCREEN_LOCK_COMPONENT),
                afterCleanup:
                    !linux ||
                    names.indexOf(SCREEN_LOCK_COMPONENT) ===
                        names.indexOf("065-profile-cleanup.yml") + 1,
            }).toEqual({ recipe: recipe.recipeId, installs: linux, afterCleanup: true });
        }
    });

    test("the defaults turn the lock, the idle blanking and the lock command off, in GNOME's types", () => {
        const defaults = heredoc(buildScript, DEFAULTS, "DCONF");
        expect(defaults).toBeDefined();
        expect(keyfileGroups(defaults ?? "")).toEqual({
            "org/gnome/desktop/screensaver": {
                "lock-enabled": "false",
                "idle-activation-enabled": "false",
                "lock-delay": "uint32 0",
            },
            "org/gnome/desktop/session": { "idle-delay": "uint32 0" },
            // GNOME Shell refuses every lock while this is set: idle, keyboard shortcut, system menu
            // and a logind lock request alike.
            "org/gnome/desktop/lockdown": { "disable-lock-screen": "true" },
            "org/gnome/settings-daemon/plugins/power": {
                "idle-dim": "false",
                "sleep-inactive-ac-type": "'nothing'",
                "sleep-inactive-battery-type": "'nothing'",
            },
        });
    });

    test("the locks keep a user's own settings from turning the lock or the idle delay back on", () => {
        const locks = heredoc(buildScript, LOCKS, "LOCKS");
        expect(locks).toBeDefined();
        const paths = (locks ?? "").split("\n").filter(Boolean);
        expect(paths).toEqual([
            "/org/gnome/desktop/screensaver/lock-enabled",
            "/org/gnome/desktop/screensaver/idle-activation-enabled",
            "/org/gnome/desktop/session/idle-delay",
            "/org/gnome/desktop/lockdown/disable-lock-screen",
        ]);
        // Each lock names a key the defaults set, so a locked key is never left at GNOME's default.
        const groups = keyfileGroups(heredoc(buildScript, DEFAULTS, "DCONF") ?? "");
        for (const locked of paths) {
            const at = locked.lastIndexOf("/");
            expect({
                locked,
                set: locked.slice(at + 1) in (groups[locked.slice(1, at)] ?? {}),
            }).toEqual({ locked, set: true });
        }
    });

    test("the build writes both files, then compiles them with dconf update", () => {
        const defaultsAt = build.findIndex((c) => c.includes(`cat > ${DEFAULTS} <<'DCONF'`));
        const locksAt = build.findIndex((c) => c.includes(`cat > ${LOCKS} <<'LOCKS'`));
        const updateAt = build.indexOf("dconf update");
        expect(build[0]).toBe("set -euo pipefail");
        expect(defaultsAt).toBeGreaterThan(0);
        expect(locksAt).toBeGreaterThan(0);
        expect(updateAt).toBeGreaterThan(Math.max(defaultsAt, locksAt));
        // dconf is installed when the parent image lacks it, under the package name of each family.
        expect(buildScript).toContain("install -y --no-install-recommends dconf-cli");
        expect(buildScript).toContain("dnf install -y dconf\n");
    });

    test.each([
        ["no user profile", undefined, "user-db:user\nsystem-db:local\n"],
        [
            "the distribution's profile, which reads the local database",
            DISTRIBUTION_PROFILE,
            DISTRIBUTION_PROFILE,
        ],
        [
            "a profile without the local database",
            "user-db:user\nsystem-db:site\n",
            "user-db:user\nsystem-db:site\nsystem-db:local\n",
        ],
    ])(
        "a build run on %s leaves a user profile that reads the local database, unchanged by a second run",
        (_, seed, expected) => {
            const run = runBuild(seed, 2);
            expect({ status: run.status, stderr: run.stderr }).toEqual({ status: 0, stderr: "" });
            expect(run.profiles).toEqual([expected, expected]);
            expect(run.defaults).toBe(heredoc(buildScript, DEFAULTS, "DCONF"));
            expect(run.locks).toBe(heredoc(buildScript, LOCKS, "LOCKS"));
            // dconf was found, so nothing was installed, and each run compiled the database once.
            expect(run.calls).toEqual(["dconf update", "dconf update"]);
        }
    );

    test("the validate phase checks the profile, the default, the locks and the compiled database", () => {
        const script = validate.join("\n");
        expect(validate[0]).toBe("set -euo pipefail");
        expect(validate).toEqual(
            expect.arrayContaining([
                `grep -qx 'system-db:local' ${PROFILE}`,
                `grep -qx 'lock-enabled=false' ${DEFAULTS}`,
                `grep -qx 'disable-lock-screen=true' ${DEFAULTS}`,
                `grep -qx '/org/gnome/desktop/screensaver/lock-enabled' ${LOCKS}`,
                `grep -qx '/org/gnome/desktop/lockdown/disable-lock-screen' ${LOCKS}`,
                "test -s /etc/dconf/db/local",
            ])
        );
        // The compiled database is read on its own, through a profile naming only it, so the check
        // proves dconf update compiled the defaults rather than that the source file exists.
        expect(script).toContain("printf 'system-db:local\\n' > \"${probe}\"");
        expect(script).toContain(
            'DCONF_PROFILE="${probe}" dconf read /org/gnome/desktop/screensaver/lock-enabled'
        );
        expect(script).toContain('test "${lock}" = false');
        expect(script).toContain('test "${lockdown}" = true');
    });

    test("the validate phase passes on what the build wrote and a database holding the defaults", () => {
        const run = runValidate(COMPILED);
        expect(run.status).toBe(0);
        expect(run.calls).toEqual([
            "dconf read /org/gnome/desktop/screensaver/lock-enabled with system-db:local ",
            "dconf read /org/gnome/desktop/lockdown/disable-lock-screen with system-db:local ",
        ]);
    });

    test.each([
        [
            "the profile does not read the local database",
            COMPILED,
            (root: string) => fs.writeFileSync(`${root}${PROFILE}`, "user-db:user\n"),
        ],
        [
            "the locks leave lock-enabled to the user",
            COMPILED,
            (root: string) =>
                fs.writeFileSync(
                    `${root}${LOCKS}`,
                    fs
                        .readFileSync(`${root}${LOCKS}`, "utf8")
                        .replace("/org/gnome/desktop/screensaver/lock-enabled\n", "")
                ),
        ],
        [
            "nothing was compiled",
            COMPILED,
            (root: string) => fs.rmSync(`${root}/etc/dconf/db/local`),
        ],
        [
            "the compiled database still turns the lock on",
            { ...COMPILED, "/org/gnome/desktop/screensaver/lock-enabled": "true" },
            undefined,
        ],
        [
            "the compiled database leaves the lock command on",
            { ...COMPILED, "/org/gnome/desktop/lockdown/disable-lock-screen": "" },
            undefined,
        ],
    ])("the validate phase fails the build when %s", (_, answers, change) => {
        expect(runValidate(answers, change).status).not.toBe(0);
    });
});
