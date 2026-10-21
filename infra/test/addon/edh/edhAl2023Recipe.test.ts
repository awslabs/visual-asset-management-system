/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The shape of the shipped Amazon Linux 2023 desktop recipe, and the sign-in every shipped Linux
 * desktop recipe installs, decided from the files on disk without a build.
 *
 * The session sign-in and the profile cleanup are byte-identical copies of the Isaac Sim recipe's
 * components, so the runtime suites in edhIsaacSimRecipe.test.ts exercise the scripts both recipes
 * install.
 */

import * as fs from "fs";
import * as path from "path";
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
/** The ones every shipped Linux desktop recipe carries byte for byte. */
const SHARED_COMPONENTS = ["060-session-bootstrap.yml", "065-profile-cleanup.yml"];

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

    test("runs the AWS update component, then the baseline, the CLI, the sign-in and the cleanup", () => {
        expect(loaded.manifest.components[0].arn).toBe(
            "arn:${AWS::Partition}:imagebuilder:${AWS::Region}:aws:component/update-linux/x.x.x"
        );
        expect(loaded.componentFiles.map((f) => path.basename(f))).toEqual([
            "010-baseline.yml",
            ...SIGN_IN_COMPONENTS,
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
});

describe("the sign-in components the Linux desktop recipes share", () => {
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
