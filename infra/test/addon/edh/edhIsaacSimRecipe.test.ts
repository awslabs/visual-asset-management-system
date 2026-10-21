/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The shape of the shipped Isaac Sim recipe, decided from the files on disk without a build.
 *
 * The placeholder guard is the load-bearing case: a component that names an asset or parameter the
 * manifest does not declare, or a deployment value the image builder does not supply, fails the
 * Lambda's substitution at build time, ~45 minutes into a pipeline run, so it is caught here instead.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { spawnSync } from "child_process";
import {
    EDH_SESSION_SECRET_PREFIX_MAX_LENGTH,
    loadEdhAmiRecipe,
} from "../../../config/addon/edhWorkstationsConfig";

const REPO = path.join(__dirname, "..", "..", "..", "..");
const DIR = "backendAddons/edh/amiRecipes/isaacsim";
const CLI_PATH = "/opt/vams/vamscli-venv/bin/vamscli";

const loaded = loadEdhAmiRecipe(REPO, DIR, false);
const componentBodies = loaded.componentFiles.map((relative) => ({
    relative,
    body: fs.readFileSync(path.join(REPO, relative), "utf8"),
}));

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

// The session secret as the backend writes it, read from the bootstrap module itself: the revoked
// marker's literal, and the members of each document a launch, a start or a renewal writes.
const bootstrapSource = fs.readFileSync(
    path.join(REPO, "backend/backend/handlers/addon/edh/common/edhSessionBootstrap.py"),
    "utf8"
);
const REVOKED_SECRET_LITERAL =
    /^REVOKED_SECRET_VALUE = json\.dumps\((\{.*\})\)/m.exec(bootstrapSource)?.[1] ?? "";
const SECRET_DOCUMENT_KEYS = [
    ...bootstrapSource.matchAll(
        /(?:create_session_secret|_write_session_secret)\(bootstrap_id, \{([^}]*)\}\)/g
    ),
].map((match) => [...match[1].matchAll(/^\s+"(\w+)":/gm)].map((key) => key[1]));

/** A flat document as the backend's json.dumps writes it: ", " between members, ": " after each key. */
function pythonJson(document: Record<string, unknown>): string {
    const members = Object.entries(document).map(
        ([key, value]) => `${JSON.stringify(key)}: ${JSON.stringify(value)}`
    );
    return `{${members.join(", ")}}`;
}

/** The revoked marker's value exactly as the backend writes it; empty when the literal is not found. */
const REVOKED_SECRET_VALUE = REVOKED_SECRET_LITERAL
    ? pythonJson(
          JSON.parse(
              REVOKED_SECRET_LITERAL.replace(/\bTrue\b/g, "true").replace(/\bFalse\b/g, "false")
          )
      )
    : "";

/** The session secret prefix getConfig() gives a deployment that configures none. */
const DEFAULT_SECRET_PREFIX = "vams/edh-session/";
const DEFAULT_DEPLOYMENT = { sessionSecretPrefix: DEFAULT_SECRET_PREFIX };

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

describe("the shipped Isaac Sim recipe", () => {
    test("is a DCV-streamable Ubuntu 24.04 x86_64 desktop", () => {
        expect(loaded.recipeId).toBe("isaacsim");
        expect(loaded.manifest.kind).toBe("vdi");
        expect(loaded.manifest.osFamily).toBe("linux");
        expect(loaded.manifest.baseOs).toBe("ubuntu2404");
        expect(loaded.manifest.architecture).toBe("x86_64");
        expect(loaded.manifest.parentImage).toBe(
            "ssm:/aws/service/canonical/ubuntu/server/24.04/stable/20260714/amd64/hvm/ebs-gp3/ami-id"
        );
        // A dated Canonical serial, never the moving 'current' alias. The alias follows Ubuntu onto
        // kernel series whose linux-modules-extra package the archive does not publish, which the
        // desktop's first boot installs before it builds the GPU driver.
        expect(loaded.manifest.parentImage).toMatch(/\/24\.04\/stable\/\d{8}\/amd64\//);
        expect(loaded.manifest.parentImage).not.toContain("/current/");
        expect(loaded.manifest.buildInstanceType).toEqual([
            "m7i.xlarge",
            "m6i.xlarge",
            "m5.xlarge",
        ]);
        expect(loaded.manifest.rootSizeGb).toBe(150);
        expect(loaded.manifest.profileId).toBe(1);
    });

    test("declares the two repository assets it installs from", () => {
        expect(loaded.assets).toEqual([
            { id: "vamscli", repoPath: "tools/VamsCLI" },
            { id: "connector", repoPath: "tools/ExternalIntegrations/isaacsim_vams_integration" },
        ]);
    });

    test("carries the Isaac Sim parameters, with the archive digest pinned", () => {
        expect(loaded.manifest.parameters).toEqual({
            isaacSimVersion: "5.1.0",
            isaacSimDownloadUrl:
                "https://downloads.isaacsim.nvidia.com/isaac-sim-standalone-5.1.0-linux-x86_64.zip",
            isaacSimSha256: "6ab0109278c4868b6d796d5e435a0116874cd50a392edfa7cc3350debfb7a187",
            isaacSimLauncher: "isaac-sim.sh",
            nvidiaDriverMinMajor: "570",
        });
    });

    test("holds the kernel, then runs the AWS update component and the seven others in order", () => {
        // The hold has to precede the OS update, which would otherwise take the running kernel to a
        // series with no extra modules to install.
        expect(loaded.manifest.components[0].file).toBe("components/010-kernel.yml");
        expect(loaded.manifest.components[1].arn).toBe(
            "arn:${AWS::Partition}:imagebuilder:${AWS::Region}:aws:component/update-linux/x.x.x"
        );
        expect(loaded.componentFiles.map((f) => path.basename(f))).toEqual([
            "010-kernel.yml",
            "020-runtime-deps.yml",
            "030-isaacsim.yml",
            "040-vamscli.yml",
            "050-connector.yml",
            "060-session-bootstrap.yml",
            "065-profile-cleanup.yml",
            "070-autolaunch.yml",
        ]);
    });

    test("the kernel component holds the metapackages and installs the running kernel", () => {
        const kernel = componentBodies.find(({ relative }) =>
            relative.endsWith("010-kernel.yml")
        )!.body;
        expect(kernel).toContain(
            "apt-mark hold linux-aws linux-image-aws linux-headers-aws linux-modules-extra-aws linux-tools-aws"
        );
        expect(kernel).toContain("linux-headers-$(uname -r)");
        expect(kernel).toContain("linux-modules-extra-$(uname -r)");
        expect(kernel).toContain("set -euxo pipefail");
    });

    test("every asset, parameter and deployment placeholder a component uses is declared", () => {
        const declared = new Set([
            ...loaded.assets.map((a) => `\${ASSET_${a.id}}`),
            ...Object.keys(loaded.manifest.parameters ?? {}).map((k) => `\${PARAM_${k}}`),
            ...DEPLOYMENT_KEYS.map((k) => `\${DEPLOY_${k}}`),
        ]);
        const used = new Set<string>();
        for (const { body } of componentBodies) {
            for (const token of body.match(/\$\{(ASSET|PARAM|DEPLOY)_([^}]*)\}/g) ?? []) {
                used.add(token);
            }
        }
        expect([...used].filter((token) => !declared.has(token))).toEqual([]);
        // The sign-in reads the session secrets under the prefix the deployment configures.
        expect(used.has("${DEPLOY_sessionSecretPrefix}")).toBe(true);
        // Both assets must actually be fetched, or the upload is dead weight.
        expect(used.has("${ASSET_vamscli}")).toBe(true);
        expect(used.has("${ASSET_connector}")).toBe(true);
        // The digest parameter is what turns the archive download into a verified one.
        expect(used.has("${PARAM_isaacSimSha256}")).toBe(true);
        const isaacSim = componentBodies.find(({ relative }) =>
            relative.endsWith("030-isaacsim.yml")
        )!.body;
        expect(isaacSim).toContain("sha256sum -c");
    });

    test("every component is a schemaVersion 1.0 document with a build phase", () => {
        for (const { relative, body } of componentBodies) {
            expect(body).toContain("schemaVersion: 1.0");
            expect(body).toMatch(/^\s+- name: build$/m);
            // AMI_RECIPE_COMPONENT_DATA_MAX_BYTES: CreateComponent carries the document inline.
            expect(Buffer.byteLength(body, "utf8")).toBeLessThanOrEqual(16000);
            expect(relative.startsWith(`${DIR}/components/`)).toBe(true);
        }
    });

    test("every component stays within the limit resolved with the longest prefix getConfig() accepts", () => {
        // The image builder applies the limit to the document as it resolves it.
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
        // The full path must appear literally (not via ${VAMSCLI}) in components that install it,
        // write its configuration, or define the VAMSCLI variable. User-facing messages and the
        // launcher use the short form 'vamscli' (060's sign-in hint, 070's notify message) so humans
        // can type it, and the session-bootstrap and profile-cleanup scripts invoke "${VAMSCLI}"
        // rather than the literal path.
        const users = componentBodies.filter(({ body }) => body.includes(CLI_PATH));
        expect(users.map(({ relative }) => path.basename(relative)).sort()).toEqual([
            "040-vamscli.yml",
            "050-connector.yml",
            "060-session-bootstrap.yml",
            "065-profile-cleanup.yml",
        ]);
        for (const { relative, body } of componentBodies) {
            const spellings = body.match(/\/opt\/vams\/[^\s"']*vamscli(?=[\s"'])/g) ?? [];
            expect({ relative, spellings: [...new Set(spellings)] }).toEqual({
                relative,
                spellings: spellings.length ? [CLI_PATH] : [],
            });
        }
    });

    test("the recipe installs no DCV server, desktop environment or GPU driver", () => {
        // EDH's ubuntu2404 first boot installs all three. A recipe that also installed them would
        // fight that bootstrap, and the failure surfaces only on a launched desktop.
        for (const { relative, body } of componentBodies) {
            for (const forbidden of [
                "nice-dcv",
                "ubuntu-desktop",
                "gdm3",
                "nvidia-driver",
                "cuda-drivers",
            ]) {
                expect({ relative, hit: body.includes(forbidden) }).toEqual({
                    relative,
                    hit: false,
                });
            }
        }
    });

    test("the launcher and the sign-in agree on the per-desktop marker paths and the Kit flags", () => {
        const bootstrap = componentBodies.find(({ relative }) =>
            relative.endsWith("060-session-bootstrap.yml")
        )!.body;
        const launcher = componentBodies.find(({ relative }) =>
            relative.endsWith("070-autolaunch.yml")
        )!.body;
        expect(bootstrap).toContain("/etc/xdg/autostart/vams-session-bootstrap.desktop");
        // Every desktop of a user mounts one home directory, so both sides key it by instance id.
        for (const script of [bootstrap, launcher]) {
            expect(script).toContain('INSTANCE_ID="$(/usr/local/bin/vams-instance-id)"');
            expect(script).toContain('HOST_DIR="${HOME}/.vams/hosts/${INSTANCE_ID}"');
            expect(script).toContain('MARKER="${HOST_DIR}/session-ready"');
            expect(script).toContain('NO_SESSION="${HOST_DIR}/session-none"');
        }
        expect(launcher).toContain("--enable vams.connector.isaacsim");
        expect(launcher).toContain('"--/exts/vams.connector.isaacsim/profile=${PROFILE}"');
        // Kit reads each child of an --ext-folder as an extension, so the one search path is the
        // folder the connector component installs the extension into, never the extension itself.
        const connector = componentBodies.find(({ relative }) =>
            relative.endsWith("050-connector.yml")
        )!.body;
        expect(connector).toContain(
            "mkdir -p /opt/vams/isaacsim-connector/vams.connector.isaacsim"
        );
        expect(launcher).toContain("EXT_PARENT=/opt/vams/isaacsim-connector\n");
        expect(launcher.match(/--ext-folder "[^"]*"/g)).toEqual(['--ext-folder "${EXT_PARENT}"']);
    });

    test("a driver-version comparison is always guarded by a numeric-form test", () => {
        const launcher = componentBodies.find(({ relative }) =>
            relative.endsWith("070-autolaunch.yml")
        )!.body;
        // nvidia-smi answers 'N/A' on a boot that found no GPU, which an integer comparison reads
        // as a syntax error rather than as a refusal.
        const numericTests = launcher.match(/=~ \^\[0-9\]\+/g) ?? [];
        const comparisons = launcher.match(/-lt "\$\{MIN_DRIVER_MAJOR\}"/g) ?? [];
        expect(comparisons.length).toBeGreaterThan(0);
        expect(numericTests.length).toBe(comparisons.length);
    });

    test("the sign-in keeps its error capture out of the shared temporary directory", () => {
        const bootstrap = componentBodies.find(({ relative }) =>
            relative.endsWith("060-session-bootstrap.yml")
        )!.body;
        // One desktop carries several users' sessions, and the capture holds whatever Secrets
        // Manager answered for one of them.
        expect(bootstrap).toContain('ERR="${HOST_DIR}/session-bootstrap.err"');
        expect(bootstrap).not.toContain("/tmp/");
    });

    test("recipe documents are pinned to LF endings in gitattributes and carry no CR bytes", () => {
        // Recipe documents are uploaded byte-for-byte and hashed by the seeding, so their endings
        // must not depend on the checkout platform. The .gitattributes file of the recipe tree pins
        // every file under it to LF.
        const gitattributes = fs.readFileSync(
            path.join(REPO, path.dirname(DIR), ".gitattributes"),
            "utf8"
        );
        expect(gitattributes.split(/\r?\n/)).toContain("* text eol=lf");
        // Verify no component or manifest carries any CR bytes. The recipe.json sits beside the
        // components directory; loaded.componentFiles gives us the component paths.
        const recipeJson = path.join(REPO, DIR, "recipe.json");
        const allRecipeFiles = [
            recipeJson,
            ...loaded.componentFiles.map((r) => path.join(REPO, r)),
        ];
        for (const filePath of allRecipeFiles) {
            const bytes = fs.readFileSync(filePath);
            const crCount = bytes.filter((b) => b === 0x0d).length;
            expect({ file: path.relative(REPO, filePath), crCount }).toEqual({
                file: path.relative(REPO, filePath),
                crCount: 0,
            });
        }
    });
});

const INSTANCE_ID = "i-0123456789abcdef0";
const OTHER_INSTANCE_ID = "i-0fedcba9876543210";
const HOST = `.vams/hosts/${INSTANCE_ID}`;
const BOOT_ID = "6f1c2a4e-0b7d-4e55-9a51-3c2f8e1d9b70";
const EARLIER_BOOT_ID = "0d9e8f7a-6b5c-4d3e-2f1a-0b9c8d7e6f5a";
const API_KEY = "vams_fixturekey";
/** The uid this suite runs as, which names the refresher's lock. */
const DESKTOP_UID = spawnSync("bash", ["-c", "id -u"], { encoding: "utf8" }).stdout.trim();
const SECRET = {
    baseUrl: "https://vams.example.com",
    userId: "user@example.com",
    expiresAt: "2026-09-28T00:00:00Z",
    apiKey: API_KEY,
};
const LOGOUT = `--profile edh-${INSTANCE_ID} auth logout --json-output`;

/** The lines of a file a run left, without empty ones. */
function lines(text: string | undefined): string[] {
    return (text ?? "").split("\n").filter(Boolean);
}

/**
 * A script a component installs through `cat > <path> <<'<tag>'`, with the block's indentation removed,
 * taken from the component as the image builder resolves it with the deployment's values.
 */
function installedScript(
    component: string,
    tag: string,
    deployment: Record<string, string> = DEFAULT_DEPLOYMENT
): string {
    const lines = withDeploymentValues(
        componentBodies.find(({ relative }) => relative.endsWith(component))!.body,
        deployment
    ).split("\n");
    const start = lines.findIndex((line) => line.includes(`<<'${tag}'`));
    const end = lines.findIndex((line, i) => i > start && line.trim() === tag);
    const body = lines.slice(start + 1, end);
    const indent = Math.min(
        ...body.filter((line) => line.trim()).map((line) => line.length - line.trimStart().length)
    );
    return body.map((line) => line.slice(indent)).join("\n") + "\n";
}

/**
 * Every file under `dir`, keyed by its path relative to `dir` with forward slashes; a symbolic link
 * reads as `-> <target>` and is not followed.
 */
function snapshot(dir: string, prefix = ""): Record<string, string> {
    const files: Record<string, string> = {};
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const relative = `${prefix}${entry.name}`;
        if (entry.isSymbolicLink()) {
            files[relative] = `-> ${fs.readlinkSync(path.join(dir, entry.name))}`;
        } else if (entry.isDirectory()) {
            Object.assign(files, snapshot(path.join(dir, entry.name), `${relative}/`));
        } else {
            files[relative] = fs.readFileSync(path.join(dir, entry.name), "utf8");
        }
    }
    return files;
}

/**
 * Runs an installed script in real bash with a scratch directory as HOME and, as under autostart, as
 * the working directory: the instance id helper answers INSTANCE_ID, the boot id reads BOOT_ID, and
 * `prelude` (stub functions) is defined ahead of the script's first line. `seed` files (relative to
 * the home) are written first; one that starts with a shebang is made executable. `links` are
 * symbolic links (relative to the home) to the given targets. The script runs with `args`. Returns
 * the exit status, stdout, stderr and every file the home holds afterwards.
 */
function runInstalled(
    script: string,
    options: {
        prelude?: string[];
        seed?: Record<string, string>;
        links?: Record<string, string>;
        args?: string[];
        env?: Record<string, string | undefined>;
    } = {}
) {
    const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "vams-desktop-"));
    // Forward slashes, so the same paths work under Git Bash on a Windows checkout.
    const home = scratch.split(path.sep).join("/");
    try {
        for (const [relative, content] of Object.entries(options.seed ?? {})) {
            fs.mkdirSync(path.dirname(path.join(scratch, relative)), { recursive: true });
            fs.writeFileSync(path.join(scratch, relative), content, {
                mode: content.startsWith("#!") ? 0o755 : 0o644,
            });
        }
        for (const [relative, target] of Object.entries(options.links ?? {})) {
            fs.mkdirSync(path.dirname(path.join(scratch, relative)), { recursive: true });
            fs.symlinkSync(target, path.join(scratch, relative), "dir");
        }
        fs.writeFileSync(path.join(scratch, "boot_id"), `${BOOT_ID}\n`);
        const prelude = [`instance_id_stub() { echo ${INSTANCE_ID}; }`, ...(options.prelude ?? [])];
        const localized = script
            .split("/usr/local/bin/vams-instance-id")
            .join("instance_id_stub")
            .split("/proc/sys/kernel/random/boot_id")
            .join(`${home}/boot_id`)
            .replace(/^#!.*\n/, (shebang) => `${shebang}${prelude.join("\n")}\n`);
        fs.writeFileSync(path.join(scratch, "script.sh"), localized);
        const result = spawnSync("bash", [`${home}/script.sh`, ...(options.args ?? [])], {
            cwd: scratch,
            encoding: "utf8",
            env: { ...process.env, HOME: home, ...(options.env ?? {}) },
        });
        return {
            status: result.status,
            stdout: result.stdout,
            stderr: result.stderr,
            files: snapshot(scratch),
        };
    } finally {
        fs.rmSync(scratch, { recursive: true, force: true });
    }
}

/** Prints whether the refresher's lock descriptor, fd 9, is open where it runs. */
const FD9_STATE = "{ { : >&9; } 2>/dev/null && echo open || echo closed; }";

/**
 * `sleep` advances bash's SECONDS instead of waiting. Once the refresher holds its lock, the sleep
 * after POLLS checks ends the run with status 99, since the refresher itself never stops; each of
 * the refresher's sleeps records whether it holds the lock's descriptor.
 */
const FAKE_SLEEP = [
    "sleep() {",
    '    if [ -e "${HOME}/refreshing" ]; then',
    "        POLLED=$((${POLLED:-0} + 1))",
    '        [ "${POLLED}" -le "${POLLS:-0}" ] || exit 99',
    `        ${FD9_STATE} >> "\${HOME}/fd9-at-sleep"`,
    "    fi",
    '    echo "$1" >> "${HOME}/sleeps"',
    "    SECONDS=$((SECONDS + $1))",
    "}",
];

/**
 * The refresher's lock: flock is free, or held by another refresher of this user on this desktop
 * (recording its arguments either way), with the session's runtime directory in the home; or there
 * is no flock at all.
 */
function flockStub(lock: "free" | "held" | "missing"): string[] {
    if (lock === "missing") {
        return ['command() { if [ "$*" = "-v flock" ]; then return 1; fi; builtin command "$@"; }'];
    }
    return [
        'XDG_RUNTIME_DIR="${HOME}/runtime"',
        'mkdir -p "${XDG_RUNTIME_DIR}"',
        "flock() {",
        '    printf "%s\\n" "$*" >> "${HOME}/flock-calls"',
        lock === "held" ? "    return 1" : '    touch "${HOME}/refreshing"',
        "}",
    ];
}

/** Answers the active profile's name from the file named last, as `jq -r '.active_profile // empty'`. */
const ACTIVE_JQ = [
    "jq() {",
    '    printf "%s\\n" "$*" >> "${HOME}/jq-calls"',
    '    [[ "$(cat "${@: -1}")" =~ \\"active_profile\\":\\ *\\"([^\\"]*)\\" ]] && echo "${BASH_REMATCH[1]}"',
    "    return 0",
    "}",
];

/**
 * A profile cleanup that records its arguments, which of this boot's markers exist when it runs and
 * whether the sign-in had given up by then, writes to both its outputs, and exits with CLEANUP_EXIT.
 */
const CLEANUP_STUB = [
    "#!/usr/bin/env bash",
    'printf "%s\\n" "$*" >> "${HOME}/cleanup-args"',
    `host="\${HOME}/${HOST}"`,
    'printf "ready=%s none=%s gave-up=%s\\n" "$(cat "${host}/session-ready" 2>/dev/null)" \\',
    '    "$(cat "${host}/session-none" 2>/dev/null)" \\',
    '    "$(grep -c "did not complete within 20 minutes" "${host}/session-bootstrap.log")" \\',
    '    >> "${HOME}/cleanup-calls"',
    "echo cleanup output",
    "echo cleanup error >&2",
    'exit "${CLEANUP_EXIT:-0}"',
    "",
].join("\n");

/**
 * Runs the retry wrapper against a stub sign-in whose call n exits with `exits[n - 1]` (the last value
 * repeating), recording its arguments and whether it holds the lock's descriptor and printing one
 * line, and against the stub profile cleanup (none when `cleanup` is false) exiting with
 * `cleanupExit`. The refresher's lock is held unless `lock` says otherwise, so a run ends once the
 * sign-in settles; a free lock lets the refresher check `polls` times. `active` is the CLI's active
 * profile, or undefined for no active_profile.json.
 */
function runRetryWrapper(
    exits: number[],
    seed: Record<string, string> = {},
    {
        lock = "held",
        polls = 0,
        cleanup = true,
        cleanupExit = 0,
        active,
    }: {
        lock?: "free" | "held" | "missing";
        polls?: number;
        cleanup?: boolean;
        cleanupExit?: number;
        active?: string;
    } = {}
) {
    const signIn = [
        "sign_in_stub() {",
        '    local n=$(( $(cat "${HOME}/attempts" 2>/dev/null || echo 0) + 1 ))',
        '    echo "$n" > "${HOME}/attempts"',
        '    echo "$*" >> "${HOME}/sign-in-args"',
        `    ${FD9_STATE} >> "\${HOME}/sign-in-fd9"`,
        '    echo "sign-in $n $*"',
        `    local exits=(${exits.join(" ")})`,
        "    local i=$(( n <= ${#exits[@]} ? n - 1 : ${#exits[@]} - 1 ))",
        '    return "${exits[$i]}"',
        "}",
    ];
    const wrapper = installedScript("060-session-bootstrap.yml", "RETRY")
        .split("/usr/local/bin/vams-session-bootstrap.sh")
        .join("sign_in_stub")
        .split("/usr/local/bin/vams-profile-cleanup.sh")
        .join('"${HOME}/cleanup.sh"');
    const run = runInstalled(wrapper, {
        prelude: [...FAKE_SLEEP, ...flockStub(lock), ...ACTIVE_JQ, ...signIn],
        seed: {
            ...(cleanup ? { "cleanup.sh": CLEANUP_STUB } : {}),
            ...(active === undefined
                ? {}
                : { ".config/vamscli/active_profile.json": `{"active_profile": "${active}"}\n` }),
            ...seed,
        },
        env: { POLLS: String(polls), CLEANUP_EXIT: String(cleanupExit) },
    });
    return {
        ...run,
        log: run.files[`${HOST}/session-bootstrap.log`] ?? "",
        sleeps: lines(run.files["sleeps"]).map(Number),
        attempts: Number((run.files["attempts"] ?? "0").trim()),
        signInArgs: lines(run.files["sign-in-args"]),
        cleanupCalls: lines(run.files["cleanup-calls"]),
        cleanupArgs: run.files["cleanup-args"],
        cleanupLog: run.files[`${HOST}/profile-cleanup.log`],
        flockCalls: lines(run.files["flock-calls"]),
        sleepFd9: lines(run.files["fd9-at-sleep"]),
        signInFd9: lines(run.files["sign-in-fd9"]),
        marker: run.files[`${HOST}/session-ready`],
        noSession: run.files[`${HOST}/session-none`],
    };
}

describe("the session sign-in retry wrapper", () => {
    const burst = Array(11).fill(10);

    test("retries every 10 s twelve times, then every minute until 20 minutes have passed", () => {
        // The sweep that repairs a binding the launch could not write runs every 15 minutes, so the
        // wrapper outlasts it rather than giving up after the first two minutes.
        const run = runRetryWrapper([1]);
        expect(run.status).toBe(1);
        expect(run.marker).toBeUndefined();
        expect(run.sleeps).toEqual([...burst, ...Array(18).fill(60)]);
        expect(run.attempts).toBe(30);
        const slept = run.sleeps.reduce((total, seconds) => total + seconds, 0);
        expect(slept).toBeLessThanOrEqual(20 * 60);
        expect(slept + 60).toBeGreaterThan(20 * 60);
        // The connector signs in through this desktop's own profile, so that is the one to name.
        const gaveUp = lines(run.log).find((line) => line.includes("did not complete"));
        expect(gaveUp).toContain(`sign in with 'vamscli --profile edh-${INSTANCE_ID} auth login'`);
        expect(gaveUp).toContain(
            "did not complete within 20 minutes; it keeps retrying every minute"
        );
    });

    test("signs in on a later attempt once the binding is repaired", () => {
        const run = runRetryWrapper([...Array(19).fill(1), 0]);
        expect(run.status).toBe(0);
        expect(run.marker).toBe(`${BOOT_ID}\n`);
        expect(run.attempts).toBe(20);
        expect(run.sleeps).toEqual([...burst, ...Array(8).fill(60)]);
        expect(run.log).toContain("attempt 20: signed in");
        // At login the sign-in re-creates this desktop's own profile.
        expect(run.signInArgs).toEqual(Array(20).fill("--fresh"));
    });

    test("stops at once when there is nothing to sign in, and records that for this boot", () => {
        const run = runRetryWrapper([3]);
        expect(run.status).toBe(0);
        expect(run.marker).toBeUndefined();
        expect(run.noSession).toBe(`${BOOT_ID}\n`);
        expect(run.attempts).toBe(1);
        expect(run.sleeps).toEqual([]);
    });

    test("keeps its log and markers under this desktop's instance id in the shared home", () => {
        const run = runRetryWrapper([0], {
            [`.vams/hosts/${OTHER_INSTANCE_ID}/session-ready`]: `${BOOT_ID}\n`,
            [`.vams/hosts/${OTHER_INSTANCE_ID}/session-bootstrap.log`]: "another desktop\n",
        });
        expect(run.marker).toBe(`${BOOT_ID}\n`);
        expect(run.log).toContain(`boot ${BOOT_ID}`);
        expect(run.log).toContain("attempt 1: signed in");
        // Another desktop of the same user is left exactly as it was.
        expect(run.files[`.vams/hosts/${OTHER_INSTANCE_ID}/session-ready`]).toBe(`${BOOT_ID}\n`);
        expect(run.files[`.vams/hosts/${OTHER_INSTANCE_ID}/session-bootstrap.log`]).toBe(
            "another desktop\n"
        );
        // Nothing lands directly under ~/.vams, where every desktop would read it.
        expect(Object.keys(run.files).filter((file) => /^\.vams\/[^/]+$/.test(file))).toEqual([]);
    });

    test("clears the markers an earlier boot of this desktop left", () => {
        const run = runRetryWrapper([1], {
            [`${HOST}/session-ready`]: `${EARLIER_BOOT_ID}\n`,
            [`${HOST}/session-none`]: `${EARLIER_BOOT_ID}\n`,
            [`${HOST}/session-key-applied`]: "2026-09-27T12:00:00Z\n",
        });
        expect(run.marker).toBeUndefined();
        expect(run.noSession).toBeUndefined();
        // So is the record of the key last signed in, so a check after a failed login signs in.
        expect(run.files[`${HOST}/session-key-applied`]).toBeUndefined();
    });

    test("runs the profile cleanup once, after writing this boot's marker", () => {
        const run = runRetryWrapper([0], {}, { lock: "free", polls: 3 });
        expect(run.cleanupCalls).toEqual([`ready=${BOOT_ID} none= gave-up=0`]);
    });

    test("runs the profile cleanup once when there is nothing to sign in", () => {
        const run = runRetryWrapper([3], {}, { lock: "free", polls: 3 });
        expect(run.cleanupCalls).toEqual([`ready= none=${BOOT_ID} gave-up=0`]);
    });

    test("runs the profile cleanup once after the 20-minute window", () => {
        const run = runRetryWrapper([1], {}, { lock: "free", polls: 3 });
        expect(run.cleanupCalls).toEqual(["ready= none= gave-up=1"]);
    });

    const outcomes: [string, number[], number][] = [
        ["it signed in", [0], 0],
        ["there is nothing to sign in", [3], 0],
        ["it gave up", [1], 1],
    ];
    test.each(outcomes)(
        "a failing profile cleanup leaves the outcome as it was when %s",
        (_case, exits, status) => {
            const run = runRetryWrapper(exits, {}, { cleanupExit: 7 });
            expect(run.status).toBe(status);
            expect(run.cleanupCalls).toHaveLength(1);
        }
    );

    test("keeps the profile cleanup's output in a log of its own beside the sign-in's", () => {
        const run = runRetryWrapper([0]);
        expect(run.cleanupLog).toBe("cleanup output\ncleanup error\n");
        expect(run.log).not.toContain("cleanup");
    });

    test("skips the profile cleanup on an image that does not install it", () => {
        const run = runRetryWrapper([0], {}, { cleanup: false });
        expect(run.status).toBe(0);
        expect(run.cleanupLog).toBeUndefined();
    });

    test("gives the profile cleanup the name of the profile that was active before the sign-in", () => {
        expect(runRetryWrapper([0], {}, { active: "prod" }).cleanupArgs).toBe("prod\n");
        // None at all on a home the CLI has not been set up in.
        expect(runRetryWrapper([0]).cleanupArgs).toBe("\n");
    });

    test("then checks the sign-in every minute for the rest of the session", () => {
        const run = runRetryWrapper([0], {}, { lock: "free", polls: 3 });
        // 99: the refresher was still checking when the stub sleep ended the run.
        expect(run.status).toBe(99);
        expect(run.signInArgs).toEqual(["--fresh", ...Array(3).fill("--if-changed")]);
        expect(run.sleeps).toEqual([60, 60, 60]);
        expect(run.log).toContain("sign-in 4 --if-changed");
        // Only the refresher itself holds its lock: a wait or a check left behind by a refresher that
        // was killed would keep the next login's refresher from starting.
        expect(run.sleepFd9).toEqual(Array(3).fill("closed"));
        expect(run.signInFd9).toEqual(Array(4).fill("closed"));
    });

    test("takes its lock without waiting, one per user, in the session's runtime directory", () => {
        const run = runRetryWrapper([0], {}, { lock: "free", polls: 1 });
        expect(run.flockCalls).toEqual(["-n 9"]);
        expect(Object.keys(run.files).filter((file) => file.startsWith("runtime/"))).toEqual([
            `runtime/vams-session-refresh-${DESKTOP_UID}.lock`,
        ]);
        // A session without a runtime directory locks under /tmp, still per user.
        expect(installedScript("060-session-bootstrap.yml", "RETRY")).toContain(
            '"${XDG_RUNTIME_DIR:-/tmp}/vams-session-refresh-$(id -u).lock"'
        );
    });

    test("keeps checking whatever a check returns, a revoked secret included", () => {
        const run = runRetryWrapper([0, 3, 1, 3, 0], {}, { lock: "free", polls: 4 });
        expect(run.status).toBe(99);
        expect(run.signInArgs).toEqual(["--fresh", ...Array(4).fill("--if-changed")]);
    });

    test("keeps trying every minute once the 20-minute window has passed", () => {
        const run = runRetryWrapper([1], {}, { lock: "free", polls: 2 });
        expect(run.status).toBe(99);
        expect(run.signInArgs).toEqual([
            ...Array(30).fill("--fresh"),
            "--if-changed",
            "--if-changed",
        ]);
    });

    test("leaves the checking to the refresher already running for this user on this desktop", () => {
        const run = runRetryWrapper([0], {}, { lock: "held", polls: 3 });
        expect(run.status).toBe(0);
        expect(run.flockCalls).toEqual(["-n 9"]);
        expect(run.signInArgs).toEqual(["--fresh"]);
        expect(run.sleeps).toEqual([]);
        expect(run.cleanupCalls).toHaveLength(1);
        expect(run.log).toContain("attempt 1: signed in");
        expect(run.log.trim().split("\n").pop()).toMatch(
            /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z this user's refresher from an earlier login checks the sign-in$/
        );
    });

    test("does not check the sign-in again, and says so, where flock is missing", () => {
        const run = runRetryWrapper([0], {}, { lock: "missing", polls: 3 });
        expect(run.status).toBe(0);
        expect(run.signInArgs).toEqual(["--fresh"]);
        expect(run.log.trim().split("\n").pop()).toBe(
            "flock is not available, so the sign-in is not checked again in this session"
        );
    });
});

/**
 * A script a component installs, for a scratch home to hold and another script to run: the instance
 * id helper answers INSTANCE_ID and `prelude` is defined ahead of its first line.
 */
function seededScript(component: string, tag: string, prelude: string[]): string {
    const stubs = [`instance_id_stub() { echo ${INSTANCE_ID}; }`, ...prelude];
    return installedScript(component, tag)
        .split("/usr/local/bin/vams-instance-id")
        .join("instance_id_stub")
        .replace(/^#!.*\n/, (shebang) => `${shebang}${stubs.join("\n")}\n`);
}

describe("the sign-in refresher", () => {
    const RENEWED = { ...SECRET, expiresAt: "2026-09-28T08:00:00Z", apiKey: "vams_renewedkey" };

    /**
     * Runs the retry wrapper with the real sign-in, against the sign-in's stub commands, through the
     * login and `polls` checks; read n of the session secret answers `secrets[n - 1]`, the last
     * repeating.
     */
    function refresh(secrets: (Record<string, unknown> | string | null)[], polls: number) {
        const signIn = seededScript(
            "060-session-bootstrap.yml",
            "BOOTSTRAP",
            signInStubs({ secrets })
        );
        const wrapper = installedScript("060-session-bootstrap.yml", "RETRY")
            .split("/usr/local/bin/vams-session-bootstrap.sh")
            .join('"${HOME}/sign-in.sh"')
            .split("/usr/local/bin/vams-profile-cleanup.sh")
            .join('"${HOME}/no-cleanup.sh"');
        const run = runInstalled(wrapper, {
            prelude: [...FAKE_SLEEP, ...flockStub("free")],
            seed: { "sign-in.sh": signIn },
            env: { POLLS: String(polls), VAMSCLI: "vamscli_stub" },
        });
        const log = run.files[`${HOST}/session-bootstrap.log`] ?? "";
        return {
            ...run,
            argv: lines(run.files["vamscli-argv"]),
            stdin: lines(run.files["vamscli-stdin"]),
            secretReads: lines(run.files["aws-calls"]).length,
            // What the checks logged after the login signed in.
            checked: log.split("attempt 1: signed in\n")[1],
            applied: run.files[`${HOST}/session-key-applied`],
        };
    }

    test("says nothing, and asks the CLI nothing, while the secret holds the key it signed in with", () => {
        const run = refresh([SECRET], 3);
        expect(run.status).toBe(99);
        expect(run.argv).toEqual(signedInArgv(SECRET, " --force"));
        expect(run.checked).toBe("");
        // Each check reads the secret again.
        expect(run.secretReads).toBe(4);
    });

    test("signs both profiles in again, unforced, at the first check after a renew re-issued the key", () => {
        const run = refresh([SECRET, SECRET, RENEWED], 3);
        expect(run.status).toBe(99);
        expect(run.argv).toEqual([...signedInArgv(SECRET, " --force"), ...signedInArgv(RENEWED)]);
        expect(run.stdin).toEqual([
            `edh-${INSTANCE_ID} ${API_KEY}`,
            `edh ${API_KEY}`,
            `edh-${INSTANCE_ID} ${RENEWED.apiKey}`,
            `edh ${RENEWED.apiKey}`,
        ]);
        expect(run.applied).toBe(`${RENEWED.expiresAt}\n`);
        expect(run.checked).toMatch(
            /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z signed in; the key expires at 2026-09-28T08:00:00Z$/m
        );
    });

    test("keeps checking after the secret is revoked, signing nothing out, and signs in again once a renew re-issues the key", () => {
        // The revoked marker exactly as the backend writes it.
        const run = refresh([SECRET, REVOKED_SECRET_VALUE, REVOKED_SECRET_VALUE, RENEWED], 4);
        expect(run.status).toBe(99);
        // A sign-in the user made by hand once the key expired is left as it is, and only the first
        // revoked check logs.
        expect(run.argv).toEqual([...signedInArgv(SECRET, " --force"), ...signedInArgv(RENEWED)]);
        expect(
            (run.checked ?? "").match(
                /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z the VAMS session secret is revoked; nothing to sign in$/gm
            )
        ).toHaveLength(1);
        expect(run.applied).toBe(`${RENEWED.expiresAt}\n`);
    });
});

describe("the instance id helper", () => {
    function runHelper(answer: string) {
        const curl = [
            "curl() {",
            '    printf "%s\\n" "$*" >> "${HOME}/curl-calls"',
            '    case "$*" in',
            "        *api/token*) echo imds-token ;;",
            `        *meta-data/instance-id*) echo '${answer}' ;;`,
            "        *) return 22 ;;",
            "    esac",
            "}",
        ];
        return runInstalled(installedScript("060-session-bootstrap.yml", "INSTANCEID"), {
            prelude: curl,
        });
    }

    test("prints the instance id the metadata service answers over IMDSv2", () => {
        const run = runHelper(INSTANCE_ID);
        expect(run.status).toBe(0);
        expect(run.stdout).toBe(`${INSTANCE_ID}\n`);
        expect(run.files["curl-calls"]).toContain("X-aws-ec2-metadata-token: imds-token");
    });

    test("refuses an answer that is not an instance id, since it names a directory and a profile", () => {
        const run = runHelper("<html>busy</html>");
        expect(run.status).not.toBe(0);
        expect(run.stdout).toBe("");
    });
});

/**
 * Stub metadata, AWS, jq and vamscli commands for the sign-in. Read n of the session secret answers
 * `secrets[n - 1]`, the last repeating, with null standing for a secret that does not exist: a
 * document as the backend's json.dumps writes it, and a string as it is.
 */
function signInStubs({
    jobName = "0123456789abcdef0123456789abcdef",
    secrets = [SECRET],
    logoutExit = 0,
    loginExit = 0,
}: {
    /** The desktop's edh:JobName tag. */
    jobName?: string;
    secrets?: (Record<string, unknown> | string | null)[];
    /** The status the CLI's sign-out exits with. */
    logoutExit?: number;
    /** The status the CLI's token sign-in exits with. */
    loginExit?: number;
} = {}): string[] {
    const answers = secrets.map((secret) =>
        secret === null
            ? "'MISSING'"
            : `'${typeof secret === "string" ? secret : pythonJson(secret)}'`
    );
    return [
        "curl() {",
        '    case "$*" in',
        "        *api/token*) echo imds-token ;;",
        "        *placement/region*) echo us-west-2 ;;",
        "        *) return 22 ;;",
        "    esac",
        "}",
        "aws() {",
        '    case "$1 $2" in',
        `        "ec2 describe-tags") echo '${jobName}' ;;`,
        '        "secretsmanager get-secret-value")',
        `            printf '%s\\n' "$*" >> "\${HOME}/aws-calls"`,
        `            local answers=(${answers.join(" ")})`,
        '            local n="$(wc -l < "${HOME}/aws-calls")"',
        '            local answer="${answers[$(( n <= ${#answers[@]} ? n - 1 : ${#answers[@]} - 1 ))]}"',
        '            if [ "${answer}" = MISSING ]; then',
        "                echo 'An error occurred (ResourceNotFoundException) when calling the GetSecretValue operation' >&2",
        "                return 254",
        "            fi",
        '            echo "${answer}" ;;',
        "        *) return 1 ;;",
        "    esac",
        "}",
        // Answers a `jq -r .<key>` filter from the document on stdin, whatever the space after a colon.
        "jq() {",
        "    local json key",
        '    json="$(cat)"',
        '    key="${2#.}"',
        '    key="${key%% *}"',
        '    if [[ "${json}" =~ \\"${key}\\":[[:space:]]*\\"([^\\"]*)\\" ]]; then echo "${BASH_REMATCH[1]}";',
        '    elif [[ "${json}" =~ \\"${key}\\":[[:space:]]*(true|false) ]]; then echo "${BASH_REMATCH[1]}";',
        "    else echo false; fi",
        "}",
        "vamscli_stub() {",
        '    printf "%s\\n" "$*" >> "${HOME}/vamscli-argv"',
        '    if [[ " $* " == *" auth logout "* ]]; then',
        `        echo '{"success": true}'; return ${logoutExit}`,
        "    fi",
        '    if [[ " $* " == *" --token-override-stdin "* ]]; then',
        '        printf "%s %s\\n" "$2" "$(cat)" >> "${HOME}/vamscli-stdin"',
        `        [ ${loginExit} -eq 0 ] || return ${loginExit}`,
        "    fi",
        '    echo "{}"',
        "}",
    ];
}

/** What the CLI is asked, in order, when the sign-in signs in this desktop's own profile and the alias. */
function signedInArgv(secret: typeof SECRET = SECRET, ownSetup = ""): string[] {
    const setup = `setup ${secret.baseUrl} --skip-version-check --json-output`;
    const login = `auth login --user-id ${secret.userId} --token-override-stdin --expires-at ${secret.expiresAt} --skip-version-check --json-output`;
    return [
        `--profile edh-${INSTANCE_ID} ${setup}${ownSetup}`,
        `--profile edh-${INSTANCE_ID} ${login}`,
        `--profile edh ${setup}`,
        `--profile edh ${login}`,
    ];
}

describe("the session secret the backend writes", () => {
    test("[control] the bootstrap module's revoked marker and both secret documents are read", () => {
        expect(REVOKED_SECRET_VALUE).not.toBe("");
        expect(SECRET_DOCUMENT_KEYS).toHaveLength(2);
    });

    test("the sign-in reads the members each document carries and the revoked marker's, and no other", () => {
        const signIn = installedScript("060-session-bootstrap.yml", "BOOTSTRAP");
        const read = new Set([...signIn.matchAll(/\bjq -r '?\.(\w+)/g)].map((match) => match[1]));
        const revoked = Object.keys(JSON.parse(REVOKED_SECRET_VALUE));
        for (const keys of SECRET_DOCUMENT_KEYS) {
            expect([...keys].sort()).toEqual(Object.keys(SECRET).sort());
            expect([...read].sort()).toEqual([...keys, ...revoked].sort());
        }
    });
});

describe("the session sign-in", () => {
    /** Where the sign-in records the expiry of the key it last signed in with. */
    const APPLIED = `${HOST}/session-key-applied`;
    const EARLIER_EXPIRY = "2026-09-27T12:00:00Z\n";

    type SignIn = {
        /** The desktop's edh:JobName tag. */
        jobName?: string;
        /** The session secret's document; null for a desktop whose secret does not exist. */
        secret?: Record<string, unknown> | null;
        /** The status the CLI's sign-out exits with. */
        logoutExit?: number;
        /** The status the CLI's token sign-in exits with. */
        loginExit?: number;
        /** The deployment's session secret prefix, which the image builder writes into the script. */
        secretPrefix?: string;
        /** VAMS_SESSION_SECRET_PREFIX in the sign-in's environment. */
        prefixOverride?: string;
        /** None by hand, --fresh at login, --if-changed on a refresher check. */
        args?: string[];
        /** What the record of the key last signed in holds before the run; undefined for none. */
        applied?: string;
    };

    /** Runs the sign-in against stub metadata, AWS, jq and vamscli commands. */
    function run(options: SignIn = {}) {
        const {
            secret = SECRET,
            secretPrefix = DEFAULT_SECRET_PREFIX,
            prefixOverride,
            args = [],
            applied,
            ...stubs
        } = options;
        const script = installedScript("060-session-bootstrap.yml", "BOOTSTRAP", {
            sessionSecretPrefix: secretPrefix,
        });
        return runInstalled(script, {
            prelude: signInStubs({ ...stubs, secrets: [secret] }),
            args,
            seed: applied === undefined ? {} : { [APPLIED]: applied },
            env: {
                VAMSCLI: "vamscli_stub",
                ...(prefixOverride === undefined
                    ? {}
                    : { VAMS_SESSION_SECRET_PREFIX: prefixOverride }),
            },
        });
    }

    test("signs in this desktop's own profile and the edh alias, with the key on stdin only", () => {
        const result = run();
        expect(result.status).toBe(0);
        const login =
            "auth login --user-id user@example.com --token-override-stdin --expires-at 2026-09-28T00:00:00Z --skip-version-check --json-output";
        const setup = "setup https://vams.example.com --skip-version-check --json-output";
        expect(result.files["vamscli-argv"].trim().split("\n")).toEqual([
            `--profile edh-${INSTANCE_ID} ${setup}`,
            `--profile edh-${INSTANCE_ID} ${login}`,
            `--profile edh ${setup}`,
            `--profile edh ${login}`,
        ]);
        expect(result.files["vamscli-argv"]).not.toContain(API_KEY);
        expect(result.files["vamscli-argv"]).not.toContain("auth logout");
        expect(result.files["vamscli-stdin"].trim().split("\n")).toEqual([
            `edh-${INSTANCE_ID} ${API_KEY}`,
            `edh ${API_KEY}`,
        ]);
        expect(result.files["aws-calls"]).toContain(
            "--secret-id vams/edh-session/01234567-89ab-cdef-0123-456789abcdef"
        );
    });

    // The prefix is the deployment's sessionBootstrap.secretNamePrefix, written into the script when
    // the image is built; VAMS_SESSION_SECRET_PREFIX in the environment still takes precedence.
    const secretPrefixes: [string, SignIn, string][] = [
        ["the default prefix", {}, "vams/edh-session/"],
        ["a configured prefix", { secretPrefix: "team-a/edh-session/" }, "team-a/edh-session/"],
        [
            "the environment's prefix over the configured one",
            { secretPrefix: "team-a/edh-session/", prefixOverride: "break-glass/" },
            "break-glass/",
        ],
    ];
    test.each(secretPrefixes)("reads the session secret under %s", (_case, options, prefix) => {
        const result = run(options);
        expect(result.status).toBe(0);
        expect(result.files["aws-calls"]).toContain(
            `--secret-id ${prefix}01234567-89ab-cdef-0123-456789abcdef`
        );
    });

    // A stopped and restarted desktop keeps its instance id, so its own profile still holds the key
    // VAMS revoked at the stop; the shared alias may hold another running desktop's key.
    const nothingToSignIn: [string, SignIn, string][] = [
        [
            "its session secret is revoked",
            { secret: { ...SECRET, revoked: true } },
            "the VAMS session secret is revoked",
        ],
        ["it has no session secret", { secret: null }, "no VAMS session secret for this desktop"],
        ["it is not a VAMS launch", { jobName: "None" }, "edh:JobName 'None' is not a VAMS launch"],
    ];
    test.each(nothingToSignIn)(
        "signs out only this desktop's own profile when %s",
        (_case, options, reason) => {
            const result = run(options);
            expect(result.status).toBe(3);
            expect(result.stderr).toContain(`${reason}; nothing to sign in`);
            expect(result.files["vamscli-argv"].trim().split("\n")).toEqual([LOGOUT]);
            expect(result.files["vamscli-stdin"]).toBeUndefined();
            expect(result.files[APPLIED]).toBe("none\n");
        }
    );

    test("still reports nothing to sign in when the sign-out itself fails", () => {
        const result = run({ secret: { ...SECRET, revoked: true }, logoutExit: 1 });
        expect(result.status).toBe(3);
        expect(result.files["vamscli-argv"].trim().split("\n")).toEqual([LOGOUT]);
        // Recorded all the same, so a refresher does not sign out again every minute.
        expect(result.files[APPLIED]).toBe("none\n");
    });

    test("records the expiry of the key it signed in with, and never the key", () => {
        const result = run();
        expect(result.status).toBe(0);
        expect(result.files[APPLIED]).toBe(`${SECRET.expiresAt}\n`);
        // Written through a temporary file beside it, which the rename leaves no trace of.
        expect(
            Object.keys(result.files)
                .filter((file) => file.startsWith(`${HOST}/`))
                .sort()
        ).toEqual([`${HOST}/session-bootstrap.err`, APPLIED]);
    });

    test("at login re-creates this desktop's own profile, and never the edh alias", () => {
        const result = run({ args: ["--fresh"] });
        expect(result.status).toBe(0);
        expect(lines(result.files["vamscli-argv"])).toEqual(signedInArgv(SECRET, " --force"));
        expect(result.files[APPLIED]).toBe(`${SECRET.expiresAt}\n`);
    });

    test("on a check does nothing, and says nothing, while the secret holds the key last signed in", () => {
        const result = run({ args: ["--if-changed"], applied: `${SECRET.expiresAt}\n` });
        expect(result.status).toBe(0);
        expect(result.stdout).toBe("");
        expect(result.stderr).toBe("");
        expect(result.files["vamscli-argv"]).toBeUndefined();
        expect(result.files[APPLIED]).toBe(`${SECRET.expiresAt}\n`);
    });

    test("on a check signs both profiles in again, unforced, once the secret holds another key", () => {
        const result = run({ args: ["--if-changed"], applied: EARLIER_EXPIRY });
        expect(result.status).toBe(0);
        expect(lines(result.files["vamscli-argv"])).toEqual(signedInArgv());
        expect(lines(result.files["vamscli-stdin"])).toEqual([
            `edh-${INSTANCE_ID} ${API_KEY}`,
            `edh ${API_KEY}`,
        ]);
        expect(result.files[APPLIED]).toBe(`${SECRET.expiresAt}\n`);
    });

    test.each(nothingToSignIn)(
        "on a check says nothing and signs nothing out again while %s",
        (_case, options) => {
            const result = run({ ...options, args: ["--if-changed"], applied: "none\n" });
            expect(result.status).toBe(3);
            expect(result.stdout).toBe("");
            expect(result.stderr).toBe("");
            expect(result.files["vamscli-argv"]).toBeUndefined();
            expect(result.files[APPLIED]).toBe("none\n");
        }
    );

    // The user may have signed this desktop's own profile in by hand since its key expired.
    test.each(nothingToSignIn)(
        "on a check signs nothing out, and records that, once %s",
        (_case, options, reason) => {
            const result = run({
                ...options,
                args: ["--if-changed"],
                applied: `${SECRET.expiresAt}\n`,
            });
            expect(result.status).toBe(3);
            expect(result.stderr).toContain(`${reason}; nothing to sign in`);
            expect(result.files["vamscli-argv"]).toBeUndefined();
            expect(result.files[APPLIED]).toBe("none\n");
        }
    );

    test.each(nothingToSignIn)(
        "at login still signs this desktop's own profile out when %s",
        (_case, options) => {
            const result = run({ ...options, args: ["--fresh"], applied: `${SECRET.expiresAt}\n` });
            expect(result.status).toBe(3);
            expect(lines(result.files["vamscli-argv"])).toEqual([LOGOUT]);
            expect(result.files[APPLIED]).toBe("none\n");
        }
    );

    const modes: [string, string[]][] = [
        ["by hand", []],
        ["at login", ["--fresh"]],
        ["on a check", ["--if-changed"]],
    ];
    test.each(modes)(
        "a sign-in that fails %s leaves the record as it was, so the next check tries again",
        (_mode, args) => {
            const result = run({ args, loginExit: 1, applied: EARLIER_EXPIRY });
            expect([0, 3]).not.toContain(result.status);
            expect(result.files[APPLIED]).toBe(EARLIER_EXPIRY);
        }
    );

    test("refuses an argument it does not know, before any call", () => {
        const result = run({ args: ["--force"] });
        expect(result.status).toBe(2);
        expect(result.stderr).toContain(
            "usage: vams-session-bootstrap.sh [--fresh | --if-changed]"
        );
        expect(result.files["vamscli-argv"]).toBeUndefined();
        expect(result.files["aws-calls"]).toBeUndefined();
    });

    test("keeps its error capture under this desktop's directory of the shared home", () => {
        const result = run();
        expect(result.files).toHaveProperty([`${HOST}/session-bootstrap.err`]);
        expect(Object.keys(result.files).filter((file) => /^\.vams\/[^/]+$/.test(file))).toEqual(
            []
        );
    });

    test("the per-desktop profile name passes the CLI's profile name rules", () => {
        const constants = fs.readFileSync(
            path.join(REPO, "tools/VamsCLI/vamscli/constants.py"),
            "utf8"
        );
        const min = Number(/^PROFILE_NAME_MIN_LENGTH = (\d+)/m.exec(constants)![1]);
        const max = Number(/^PROFILE_NAME_MAX_LENGTH = (\d+)/m.exec(constants)![1]);
        const allowed = new RegExp(/re\.match\(r'([^']+)', profile_name\)/.exec(constants)![1]);
        // The instance id helper admits only i- followed by 8 to 17 hexadecimal digits.
        for (const name of [`edh-i-${"f".repeat(8)}`, `edh-i-${"f".repeat(17)}`]) {
            expect(name.length).toBeGreaterThanOrEqual(min);
            expect(name.length).toBeLessThanOrEqual(max);
            expect(name).toMatch(allowed);
        }
    });
});

/**
 * How the shell lists a directory of the given mode. Git Bash on a Windows checkout lists every
 * directory as drwxr-xr-x, so there the listing cannot tell a private directory from a shared one.
 */
function listedAs(mode: number): string {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vams-mode-"));
    try {
        return spawnSync(
            "bash",
            [
                "-c",
                `chmod ${mode.toString(8)} "$1" && ls -ld "$1" | cut -c1-10`,
                "_",
                dir.split(path.sep).join("/"),
            ],
            { encoding: "utf8" }
        ).stdout.trim();
    } finally {
        fs.rmSync(dir, { recursive: true, force: true });
    }
}

/**
 * Runs the launcher with stub GPU and notification commands, the connector folder moved to a scratch
 * directory beside the home, and a Kit stand-in that records its arguments, its working directory,
 * how the shell lists that directory, whether it could write a file there, and the home and /tmp
 * directories as the shell resolves them. The session's runtime directory is a fresh directory in
 * the same scratch directory unless `runtime` names another one, or is null for a session without
 * XDG_RUNTIME_DIR. When `signInAfterSleeps` is set, the sign-in writes this boot's marker after that
 * many sleeps of the wait.
 */
function runLauncher(
    seed: Record<string, string>,
    signInAfterSleeps = 0,
    {
        connectorInstalled = true,
        runtime,
    }: { connectorInstalled?: boolean; runtime?: string | null } = {}
) {
    const prelude = [
        "SLEEPS=0",
        "sleep() {",
        "    SLEEPS=$((SLEEPS + 1))",
        '    echo "$1" >> "${HOME}/sleeps"',
        '    if [ "${SLEEPS}" = "${SIGN_IN_AFTER_SLEEPS}" ]; then',
        '        cp "${HOME}/boot_id" "${HOME}/.vams/hosts/${SIGN_IN_INSTANCE_ID}/session-ready"',
        "    fi",
        "}",
        "nvidia-smi() { echo 580.65.06; }",
        'notify-send() { printf "%s\\n" "$2" >> "${HOME}/notifications"; }',
    ];
    const launcher = installedScript("070-autolaunch.yml", "LAUNCH")
        .replace("${PARAM_nvidiaDriverMinMajor}", () => "570")
        .replace("/opt/isaacsim/${PARAM_isaacSimLauncher}", () => "${HOME}/kit.sh")
        .split("/etc/profile.d/vams-isaacsim.sh")
        .join("${HOME}/no-profile.sh");
    const forwardSlashes = (dir: string) => dir.split(path.sep).join("/");
    const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "vams-connector-"));
    // The image installs the connector folder root-owned and 0755, so the desktop user cannot write
    // to it.
    const connector = forwardSlashes(path.join(scratch, "connector"));
    if (connectorInstalled) {
        fs.mkdirSync(connector, { mode: 0o755 });
    }
    const runtimeDir = runtime === undefined ? path.join(scratch, "runtime") : runtime;
    if (runtime === undefined) {
        fs.mkdirSync(path.join(scratch, "runtime"), { mode: 0o700 });
    }
    try {
        const run = runInstalled(
            launcher.replace(
                "EXT_PARENT=/opt/vams/isaacsim-connector\n",
                () => `EXT_PARENT=${connector}\n`
            ),
            {
                prelude,
                seed: {
                    ...seed,
                    "kit.sh": [
                        "#!/usr/bin/env bash",
                        'printf "%s\\n" "$@" > "${HOME}/kit-argv"',
                        '{ pwd -P; (cd "${HOME}" && pwd -P); (cd /tmp && pwd -P); ls -ld . | cut -c1-10; } > "${HOME}/kit-cwd"',
                        'if echo kit > kit-wrote; then echo writable; fi >> "${HOME}/kit-cwd"',
                        "",
                    ].join("\n"),
                },
                env: {
                    SIGN_IN_AFTER_SLEEPS: String(signInAfterSleeps),
                    SIGN_IN_INSTANCE_ID: INSTANCE_ID,
                    XDG_RUNTIME_DIR: runtimeDir === null ? undefined : forwardSlashes(runtimeDir),
                },
            }
        );
        const [kitCwd = "", kitHome = "", kitTmp = "", kitCwdListing = "", kitCwdWritable = ""] = (
            run.files["kit-cwd"] ?? ""
        ).split("\n");
        if (runtimeDir === null && /\/vams-isaacsim\.[^/]+$/.test(kitCwd)) {
            // A directory the launcher made under /tmp is outside every scratch directory.
            spawnSync("bash", ["-c", 'rm -rf -- "$1"', "_", kitCwd]);
        }
        const existingRuntime =
            runtimeDir !== null && fs.existsSync(runtimeDir) ? runtimeDir : undefined;
        return {
            ...run,
            connector,
            runtimeDir: runtimeDir === null ? null : forwardSlashes(runtimeDir),
            workDirs: existingRuntime ? fs.readdirSync(existingRuntime).sort() : [],
            workFiles: existingRuntime ? snapshot(existingRuntime) : {},
            kitCwd,
            kitHome,
            kitTmp,
            kitCwdListing,
            kitCwdWritable: kitCwdWritable === "writable",
            sleeps: (run.files["sleeps"] ?? "").split("\n").filter(Boolean).map(Number),
            notifications: (run.files["notifications"] ?? "").split("\n").filter(Boolean),
            kitArgs: (run.files["kit-argv"] ?? "").split("\n").filter(Boolean),
        };
    } finally {
        fs.rmSync(scratch, { recursive: true, force: true });
    }
}

describe("the Isaac Sim launcher", () => {
    const retrying = "still being retried in the background";
    const profileOverride = `--/exts/vams.connector.isaacsim/profile=edh-${INSTANCE_ID}`;
    const workDirName = /^vams-isaacsim\.[A-Za-z0-9]{6}$/;

    test("starts at once on this desktop's own profile when this boot signed in", () => {
        const run = runLauncher({ [`${HOST}/session-ready`]: `${BOOT_ID}\n` });
        expect(run.status).toBe(0);
        expect(run.sleeps).toEqual([]);
        expect(run.notifications.filter((text) => text.includes(retrying))).toEqual([]);
        expect(run.kitArgs).toEqual([
            "--ext-folder",
            run.connector,
            "--enable",
            "vams.connector.isaacsim",
            profileOverride,
        ]);
        expect(run.files[`${HOST}/isaacsim-launch.log`]).toContain("Isaac Sim is starting");
    });

    test("runs Kit from a private directory of its own under the session's runtime directory", () => {
        const run = runLauncher({ [`${HOST}/session-ready`]: `${BOOT_ID}\n` });
        expect(run.status).toBe(0);
        const name = run.kitCwd.split("/").pop();
        expect(name).toMatch(workDirName);
        // The importers write their intermediate files beside Kit's working directory.
        expect(run.workFiles).toEqual({ [`${name}/kit-wrote`]: "kit\n" });
        expect(run.kitCwdWritable).toBe(true);
        expect(run.kitCwdListing).toBe(listedAs(0o700));
        expect(run.kitHome).not.toBe("");
        expect(`${run.kitCwd}/`.startsWith(`${run.kitHome}/`)).toBe(false);
    });

    test("gives each launch a working directory of its own", () => {
        const runtime = fs.mkdtempSync(path.join(os.tmpdir(), "vams-runtime-"));
        try {
            const seed = { [`${HOST}/session-ready`]: `${BOOT_ID}\n` };
            const first = runLauncher(seed, 0, { runtime }).kitCwd.split("/").pop();
            const second = runLauncher(seed, 0, { runtime }).kitCwd.split("/").pop();
            expect(first).toMatch(workDirName);
            expect(second).not.toBe(first);
            expect(fs.readdirSync(runtime).sort()).toEqual([first, second].sort());
        } finally {
            fs.rmSync(runtime, { recursive: true, force: true });
        }
    });

    test("makes the working directory under /tmp when the session has no runtime directory", () => {
        const run = runLauncher({ [`${HOST}/session-ready`]: `${BOOT_ID}\n` }, 0, {
            runtime: null,
        });
        expect(run.status).toBe(0);
        expect(run.kitCwd.split("/").pop()).toMatch(workDirName);
        expect(run.kitCwd.slice(0, run.kitCwd.lastIndexOf("/"))).toBe(run.kitTmp);
        expect(run.kitCwdWritable).toBe(true);
    });

    test("does not start Isaac Sim, and names the directory, when its working directory cannot be made", () => {
        const parent = fs.mkdtempSync(path.join(os.tmpdir(), "vams-runtime-"));
        try {
            const run = runLauncher({ [`${HOST}/session-ready`]: `${BOOT_ID}\n` }, 0, {
                runtime: path.join(parent, "missing"),
            });
            const refusal = `A working directory for Isaac Sim could not be created in ${run.runtimeDir}, so Isaac Sim was not started.`;
            expect(run.status).toBe(1);
            expect(run.kitArgs).toEqual([]);
            expect(run.notifications).toContain(refusal);
            expect(run.files[`${HOST}/isaacsim-launch.log`]).toContain(refusal);
            expect(
                run.notifications.filter((text) => text.startsWith("Isaac Sim is starting"))
            ).toEqual([]);
        } finally {
            fs.rmSync(parent, { recursive: true, force: true });
        }
    });

    test("does not start Isaac Sim, and says why, when the connector folder is missing", () => {
        const run = runLauncher({ [`${HOST}/session-ready`]: `${BOOT_ID}\n` }, 0, {
            connectorInstalled: false,
        });
        expect(run.status).toBe(1);
        expect(run.kitArgs).toEqual([]);
        expect(run.workDirs).toEqual([]);
        expect(run.notifications).toContain(
            `The VAMS connector is missing from ${run.connector}, so Isaac Sim was not started.`
        );
        expect(
            run.notifications.filter((text) => text.startsWith("Isaac Sim is starting"))
        ).toEqual([]);
    });

    test("waits past a marker an earlier boot of this desktop left", () => {
        const run = runLauncher({ [`${HOST}/session-ready`]: `${EARLIER_BOOT_ID}\n` }, 3);
        expect(run.sleeps).toEqual([5, 5, 5]);
        expect(run.notifications.filter((text) => text.includes(retrying))).toEqual([]);
        expect(run.kitArgs).toContain(profileOverride);
    });

    test("does not take another desktop's marker, and says the sign-in is still being retried", () => {
        const run = runLauncher({
            [`.vams/hosts/${OTHER_INSTANCE_ID}/session-ready`]: `${BOOT_ID}\n`,
        });
        expect(run.sleeps).toEqual(Array(24).fill(5));
        const notices = run.notifications.filter((text) => text.includes(retrying));
        expect(notices).toHaveLength(1);
        expect(notices[0]).toMatch(
            new RegExp(
                "^The VAMS sign-in has not completed yet and is still being retried in the background; " +
                    `its log is .+/\\.vams/hosts/${INSTANCE_ID}/session-bootstrap\\.log\\. ` +
                    "Isaac Sim starts now\\. Once the sign-in completes, select Refresh Databases in " +
                    "the VAMS Connector window\\.$"
            )
        );
        expect(run.files[`${HOST}/isaacsim-launch.log`]).toContain(notices[0]);
        // Isaac Sim still starts; the connector picks the session up once the sign-in lands.
        expect(run.kitArgs).toContain(profileOverride);
    });

    test("starts without waiting or a sign-in notice on a desktop with no VAMS session", () => {
        const run = runLauncher({ [`${HOST}/session-none`]: `${BOOT_ID}\n` });
        expect(run.sleeps).toEqual([]);
        expect(run.notifications.filter((text) => text.includes("sign-in"))).toEqual([]);
        expect(run.kitArgs).toContain(profileOverride);
    });

    // Image Builder shows the component's description, so it gives the wait the launcher makes.
    test("its component's description says how long it waits for the sign-in", () => {
        const waited = runLauncher({}).sleeps.reduce((total, seconds) => total + seconds, 0);
        expect(waited).toBeGreaterThan(0);
        const autolaunch = componentBodies.find(({ relative }) =>
            relative.endsWith("070-autolaunch.yml")
        );
        expect(autolaunch).toBeDefined();
        const description = /^description: (.*)$/m.exec(autolaunch?.body ?? "")?.[1] ?? "";
        expect(description).toContain("once the VAMS sign-in completes");
        expect(description).toContain(`or after ${waited} seconds without it`);
    });
});

const SIBLING_ID = "i-0aaaaaaaaaaaaaaa1";
const STOPPED_ID = "i-0bbbbbbbbbbbbbbb2";
const GONE_ID = "i-0ccccccccccccccc3";

/** A CLI profile as the sign-in leaves it, its auth document holding the key. */
function profileFiles(name: string): Record<string, string> {
    return {
        [`.config/vamscli/profiles/${name}/config.json`]:
            '{"base_url": "https://vams.example.com"}\n',
        [`.config/vamscli/profiles/${name}/auth_profile.json`]: `{"token_type": "override", "access_token": "${API_KEY}"}\n`,
    };
}

/** The profile and the ~/.vams/hosts directory a desktop's sign-in leaves in the shared home. */
function desktopFiles(id: string): Record<string, string> {
    return { ...profileFiles(`edh-${id}`), [`.vams/hosts/${id}/session-bootstrap.log`]: `${id}\n` };
}

/** The seeded files a run did not leave exactly as they were written. */
function changedFiles(files: Record<string, string>, seed: Record<string, string>): string[] {
    return Object.keys(seed).filter((file) => files[file] !== seed[file]);
}

/**
 * Stub metadata, EC2, jq and vamscli commands for the profile cleanup. EC2 answers a call with those
 * of the ids it asks about that LISTED holds, once per tag as EC2 does; call FAIL_CALL and every
 * later one fail.
 */
function cleanupStubs(): string[] {
    return [
        "curl() {",
        '    case "$*" in',
        "        *api/token*) echo imds-token ;;",
        "        *placement/region*) echo us-west-2 ;;",
        "        *) return 22 ;;",
        "    esac",
        "}",
        "aws() {",
        '    printf "%s\\n" "$*" >> "${HOME}/aws-calls"',
        '    if [ "${FAIL_CALL}" -gt 0 ] && [ "$(wc -l < "${HOME}/aws-calls")" -ge "${FAIL_CALL}" ]; then',
        "        echo 'An error occurred (UnauthorizedOperation) when calling the DescribeTags operation' >&2",
        "        return 254",
        "    fi",
        "    local id",
        '    for id in $(sed -n "s/.*Name=resource-id,Values=\\([^ ]*\\).*/\\1/p" <<<"$*" | tr , " "); do',
        '        case " ${LISTED} " in *" ${id} "*) printf "%s\\t%s\\t" "${id}" "${id}" ;; esac',
        "    done",
        "    echo",
        "}",
        ...ACTIVE_JQ,
        'vamscli_stub() { printf "%s\\n" "$*" >> "${HOME}/vamscli-argv"; echo \'{"success": true}\'; }',
    ];
}

/**
 * Runs the installed profile cleanup against the stubs, passing `was` as the profile active before
 * the sign-in when it is given. `active` is the CLI's active profile, or undefined for no
 * active_profile.json.
 */
function runCleanup({
    listed = [INSTANCE_ID],
    failCall = 0,
    prelude = [],
    seed = {},
    active,
    was,
    links,
}: {
    listed?: string[];
    failCall?: number;
    /** Stubs defined after the others, replacing them. */
    prelude?: string[];
    seed?: Record<string, string>;
    active?: string;
    was?: string;
    links?: Record<string, string>;
} = {}) {
    const run = runInstalled(installedScript("065-profile-cleanup.yml", "CLEANUP"), {
        prelude: [...cleanupStubs(), ...prelude],
        args: was === undefined ? [] : [was],
        seed: {
            ...seed,
            ...(active === undefined
                ? {}
                : { ".config/vamscli/active_profile.json": `{"active_profile": "${active}"}\n` }),
        },
        links,
        env: { VAMSCLI: "vamscli_stub", LISTED: listed.join(" "), FAIL_CALL: String(failCall) },
    });
    return {
        ...run,
        log: run.stdout,
        awsCalls: lines(run.files["aws-calls"]),
        argv: lines(run.files["vamscli-argv"]),
        jqCalls: lines(run.files["jq-calls"]),
    };
}

/** Whether this checkout can make directory symbolic links (Windows needs developer mode for them). */
const CAN_LINK = (() => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vams-link-"));
    try {
        fs.symlinkSync(dir, path.join(dir, "link"), "dir");
        return true;
    } catch {
        return false;
    } finally {
        fs.rmSync(dir, { recursive: true, force: true });
    }
})();

describe("the profile cleanup", () => {
    const cleanupScript = () => installedScript("065-profile-cleanup.yml", "CLEANUP");

    test("is installed where the sign-in's retry wrapper runs it, and checked when the image is built", () => {
        const component =
            componentBodies.find(({ relative }) => relative.endsWith("065-profile-cleanup.yml"))
                ?.body ?? "";
        expect(component).toContain("cat > /usr/local/bin/vams-profile-cleanup.sh <<'CLEANUP'");
        expect(component).toContain("- chmod 0755 /usr/local/bin/vams-profile-cleanup.sh");
        expect(component).toContain("- bash -n /usr/local/bin/vams-profile-cleanup.sh");
        expect(component).toContain("- test -x /usr/local/bin/vams-profile-cleanup.sh");
        expect(installedScript("060-session-bootstrap.yml", "RETRY")).toContain(
            "if [ -x /usr/local/bin/vams-profile-cleanup.sh ]; then"
        );
        expect(cleanupScript()).toMatch(/^#!\/usr\/bin\/env bash\nset -uo pipefail\n/m);
    });

    test("removes the profile and host directory of a desktop EC2 no longer lists", () => {
        const kept = desktopFiles(INSTANCE_ID);
        const run = runCleanup({ seed: { ...kept, ...desktopFiles(GONE_ID) } });
        expect(run.status).toBe(0);
        expect(Object.keys(run.files).filter((file) => file.includes(GONE_ID))).toEqual([]);
        expect(changedFiles(run.files, kept)).toEqual([]);
        expect(run.log).toContain(
            `removed edh-${GONE_ID} and ~/.vams/hosts/${GONE_ID}: EC2 no longer lists ${GONE_ID}`
        );
        // Asked in this desktop's own region, naming this desktop too, with bounded timeouts.
        expect(run.awsCalls).toEqual([
            "ec2 describe-tags --region us-west-2 --filters Name=resource-type,Values=instance " +
                `Name=resource-id,Values=${INSTANCE_ID},${GONE_ID} --query Tags[].ResourceId ` +
                "--output text --cli-connect-timeout 10 --cli-read-timeout 20",
        ]);
    });

    test("keeps every desktop EC2 still lists, running or stopped, exactly as it was", () => {
        const seed = {
            ...desktopFiles(INSTANCE_ID),
            ...desktopFiles(SIBLING_ID),
            ...desktopFiles(STOPPED_ID),
        };
        const run = runCleanup({ seed, listed: [INSTANCE_ID, SIBLING_ID, STOPPED_ID] });
        expect(run.status).toBe(0);
        expect(changedFiles(run.files, seed)).toEqual([]);
        expect(run.log).not.toContain("removed");
    });

    test("leaves this desktop, the edh alias, default and every other profile alone", () => {
        const seed = {
            ...desktopFiles(INSTANCE_ID),
            ...profileFiles("edh"),
            ...profileFiles("default"),
            ...profileFiles("prod"),
            ...profileFiles("edh-custom"),
            ...profileFiles("edh-i-zzzzzzzz"),
            ".vams/hosts/notes/readme.txt": "mine\n",
            ".vams/hosts/i-0123/session-bootstrap.log": "too short for an instance id\n",
            ".vams/session-ready": "an older image's marker\n",
            ".config/vamscli/logs/vamscli.log": "log\n",
        };
        const run = runCleanup({ seed });
        expect(run.status).toBe(0);
        expect(changedFiles(run.files, seed)).toEqual([]);
        // None of them names another desktop, so EC2 is not even asked.
        expect(run.awsCalls).toEqual([]);
        expect(run.log).toContain("no other desktop left a profile here");
    });

    const cannotAsk: [string, Parameters<typeof runCleanup>[0], string][] = [
        [
            "EC2 refuses the call",
            { failCall: 1 },
            "EC2 could not be asked which desktops still exist",
        ],
        [
            "the region cannot be read",
            { prelude: ["curl() { return 28; }"] },
            "this desktop's region could not be read",
        ],
        [
            "this desktop's instance id cannot be read",
            { prelude: ["instance_id_stub() { return 1; }"] },
            "this desktop's instance id could not be read",
        ],
    ];
    test.each(cannotAsk)("removes nothing when %s", (_case, options, reason) => {
        const seed = { ...desktopFiles(INSTANCE_ID), ...desktopFiles(GONE_ID) };
        const run = runCleanup({ ...options, seed });
        expect(run.status).toBe(0);
        expect(changedFiles(run.files, seed)).toEqual([]);
        expect(run.log).toContain(`${reason}; nothing removed`);
    });

    const withoutSelf: [string, string[]][] = [
        ["lists nothing", []],
        ["lists another desktop but not this one", [GONE_ID]],
    ];
    test.each(withoutSelf)("removes nothing when EC2's answer %s", (_case, listed) => {
        const seed = {
            ...desktopFiles(INSTANCE_ID),
            ...desktopFiles(GONE_ID),
            ...desktopFiles(SIBLING_ID),
        };
        const run = runCleanup({ seed, listed });
        expect(run.status).toBe(0);
        expect(changedFiles(run.files, seed)).toEqual([]);
        expect(run.log).toContain(
            `EC2's answer does not list this desktop (${INSTANCE_ID}); nothing removed`
        );
    });

    test("never writes a key anywhere, and reads nothing a profile holds but the active one's name", () => {
        const seed = {
            ...desktopFiles(INSTANCE_ID),
            ...desktopFiles(GONE_ID),
            ...desktopFiles(SIBLING_ID),
            ...profileFiles("edh"),
            [`.config/vamscli/profiles/edh-${SIBLING_ID}/credentials.json`]: `{"password": "${API_KEY}"}\n`,
        };
        const run = runCleanup({
            seed,
            listed: [INSTANCE_ID, SIBLING_ID],
            active: `edh-${GONE_ID}`,
        });
        for (const output of [
            run.stdout,
            run.stderr,
            ...run.awsCalls,
            ...run.argv,
            ...run.jqCalls,
        ]) {
            expect(output).not.toContain(API_KEY);
        }
        expect(run.jqCalls.length).toBeGreaterThan(0);
        for (const call of run.jqCalls) {
            expect(call).toMatch(/\/\.config\/vamscli\/active_profile\.json$/);
        }
        expect(cleanupScript()).not.toMatch(/auth_profile|credentials/);
        expect(cleanupScript()).not.toMatch(/set -[a-z]*x/);
    });

    // 120 other desktops, a few more than three calls' worth.
    const many = Array.from({ length: 120 }, (_, i) => `i-${i.toString(16).padStart(17, "0")}`);
    const manySeed = Object.assign({}, desktopFiles(INSTANCE_ID), ...many.map(desktopFiles));

    test("asks about at most 50 other desktops per call, naming this one in each", () => {
        const gone = [many[7], many[63], many[111]];
        const run = runCleanup({
            seed: manySeed,
            listed: [INSTANCE_ID, ...many.filter((id) => !gone.includes(id))],
        });
        const asked = run.awsCalls.map((call) =>
            (/Name=resource-id,Values=(\S+)/.exec(call)?.[1] ?? "").split(",")
        );
        expect(asked).toHaveLength(3);
        for (const ids of asked) {
            expect(ids[0]).toBe(INSTANCE_ID);
            expect(ids.length).toBeLessThanOrEqual(51);
        }
        expect(asked.flatMap((ids) => ids.slice(1)).sort()).toEqual([...many].sort());
        // Only the three EC2 no longer lists are removed, one from each call.
        expect(many.filter((id) => !run.files[`.vams/hosts/${id}/session-bootstrap.log`])).toEqual(
            gone
        );
    });

    test("removes nothing when any one of its calls fails", () => {
        const run = runCleanup({ seed: manySeed, failCall: 2 });
        expect(run.status).toBe(0);
        expect(changedFiles(run.files, manySeed)).toEqual([]);
        expect(run.awsCalls).toHaveLength(2);
        expect(run.log).toContain(
            "EC2 could not be asked which desktops still exist; nothing removed"
        );
    });

    // [the active profile, whether the edh alias exists, whether the cleanup points it at edh]
    const activeProfiles: [string, string | undefined, boolean, boolean][] = [
        ["this desktop's own profile", `edh-${INSTANCE_ID}`, true, true],
        ["a deleted desktop's profile", `edh-${GONE_ID}`, true, true],
        ["another desktop's profile", `edh-${SIBLING_ID}`, true, true],
        ["a profile that no longer exists", "staging", true, true],
        ["a profile the user chose", "prod", true, false],
        ["the edh alias", "edh", true, false],
        ["this desktop's own profile, with no alias", `edh-${INSTANCE_ID}`, false, false],
        ["unset", undefined, true, false],
    ];
    test.each(activeProfiles)(
        "with the active profile %s (%s, alias present: %s), switches to the alias: %s",
        (_case, active, alias, switched) => {
            const seed = {
                ...desktopFiles(INSTANCE_ID),
                ...desktopFiles(GONE_ID),
                ...desktopFiles(SIBLING_ID),
                ...profileFiles("prod"),
                ...(alias ? profileFiles("edh") : {}),
            };
            const run = runCleanup({ seed, listed: [INSTANCE_ID, SIBLING_ID], active });
            expect(run.status).toBe(0);
            expect(run.argv).toEqual(
                switched ? ["--profile edh profile switch edh --json-output"] : []
            );
        }
    );

    // [the profile active before the sign-in, the one the cleanup makes active instead of this desktop's]
    const earlierChoices: [string, string, string][] = [
        ["a profile the user chose", "prod", "prod"],
        ["the edh alias", "edh", "edh"],
        ["another desktop's profile", `edh-${SIBLING_ID}`, "edh"],
        ["a profile that no longer exists", "staging", "edh"],
        ["a name the CLI refuses", "../profiles/prod", "edh"],
    ];
    test.each(earlierChoices)(
        "with %s (%s) active before the sign-in, makes %s the active profile",
        (_case, was, target) => {
            const seed = {
                ...desktopFiles(INSTANCE_ID),
                ...desktopFiles(SIBLING_ID),
                ...profileFiles("prod"),
                ...profileFiles("edh"),
            };
            const run = runCleanup({
                seed,
                listed: [INSTANCE_ID, SIBLING_ID],
                active: `edh-${INSTANCE_ID}`,
                was,
            });
            expect(run.status).toBe(0);
            expect(run.argv).toEqual([
                `--profile ${target} profile switch ${target} --json-output`,
            ]);
        }
    );

    test("leaves the active profile alone while it is the one active before the sign-in", () => {
        const run = runCleanup({
            seed: { ...desktopFiles(INSTANCE_ID), ...profileFiles("prod"), ...profileFiles("edh") },
            active: "prod",
            was: "prod",
        });
        expect(run.argv).toEqual([]);
    });

    /** The CLI's effect on the profile files: a setup saves a profile and makes it active; so does a switch. */
    const CLI_PROFILE_FILES = [
        "vamscli_stub() {",
        '    local dir="${HOME}/.config/vamscli/profiles/$2" active="${HOME}/.config/vamscli/active_profile.json"',
        '    printf "%s\\n" "$*" >> "${HOME}/vamscli-argv"',
        '    case "$3 $4" in',
        '        "setup "*)',
        '            if [[ " $* " == *" --force "* ]] || [ ! -f "${dir}/config.json" ]; then',
        '                rm -rf "${dir}" && mkdir -p "${dir}" && echo "{}" > "${dir}/config.json"',
        '                printf \'{"active_profile": "%s"}\\n\' "$2" > "${active}"',
        "            fi ;;",
        '        "auth login") cat > /dev/null ;;',
        '        "profile switch") printf \'{"active_profile": "%s"}\\n\' "$5" > "${active}" ;;',
        "    esac",
        '    echo "{}"',
        "}",
    ];

    /**
     * Logs in through the retry wrapper with the real sign-in and the real profile cleanup, against a
     * CLI that writes the profile files as the CLI does; returns the profile active afterwards.
     */
    function logIn(seed: Record<string, string>) {
        const wrapper = installedScript("060-session-bootstrap.yml", "RETRY")
            .split("/usr/local/bin/vams-session-bootstrap.sh")
            .join('"${HOME}/sign-in.sh"')
            .split("/usr/local/bin/vams-profile-cleanup.sh")
            .join('"${HOME}/cleanup.sh"');
        const run = runInstalled(wrapper, {
            prelude: [...FAKE_SLEEP, ...flockStub("held"), ...ACTIVE_JQ],
            seed: {
                ...seed,
                "sign-in.sh": seededScript("060-session-bootstrap.yml", "BOOTSTRAP", [
                    ...signInStubs(),
                    ...CLI_PROFILE_FILES,
                ]),
                "cleanup.sh": seededScript("065-profile-cleanup.yml", "CLEANUP", [
                    ...cleanupStubs(),
                    ...CLI_PROFILE_FILES,
                ]),
            },
            env: { VAMSCLI: "vamscli_stub", LISTED: INSTANCE_ID, FAIL_CALL: "0" },
        });
        return {
            ...run,
            argv: lines(run.files["vamscli-argv"]),
            marker: run.files[`${HOST}/session-ready`],
            active: /"active_profile": "([^"]*)"/.exec(
                run.files[".config/vamscli/active_profile.json"] ?? ""
            )?.[1],
        };
    }

    test("keeps the profile the user chose active across a login that re-creates this desktop's own", () => {
        const login = logIn({
            ...profileFiles("prod"),
            ...profileFiles("edh"),
            ...profileFiles(`edh-${INSTANCE_ID}`),
            ".config/vamscli/active_profile.json": '{"active_profile": "prod"}\n',
        });
        expect(login.marker).toBe(`${BOOT_ID}\n`);
        expect(login.argv).toContain(
            `--profile edh-${INSTANCE_ID} setup ${SECRET.baseUrl} --skip-version-check --json-output --force`
        );
        expect(login.active).toBe("prod");
    });

    test("points the active profile at the alias after a login when it named this desktop's own", () => {
        const login = logIn({
            ...profileFiles("edh"),
            ...profileFiles(`edh-${INSTANCE_ID}`),
            ".config/vamscli/active_profile.json": `{"active_profile": "edh-${INSTANCE_ID}"}\n`,
        });
        expect(login.marker).toBe(`${BOOT_ID}\n`);
        expect(login.active).toBe("edh");
    });

    test("logs a directory it could not remove, removes the others, and exits 0", () => {
        const run = runCleanup({
            seed: {
                ...desktopFiles(INSTANCE_ID),
                ...desktopFiles(GONE_ID),
                ...desktopFiles(SIBLING_ID),
            },
            prelude: [
                `rm() { if [ "\${@: -1}" = "\${HOME}/.vams/hosts/${GONE_ID}" ]; then return 1; fi; command rm "$@"; }`,
            ],
        });
        expect(run.status).toBe(0);
        expect(run.log).toContain(
            `could not remove all of edh-${GONE_ID} and ~/.vams/hosts/${GONE_ID}; the next sign-in tries again`
        );
        expect(run.log).not.toContain(`removed edh-${GONE_ID}`);
        // Left for the next sign-in to try again.
        expect(run.files[`.vams/hosts/${GONE_ID}/session-bootstrap.log`]).toBe(`${GONE_ID}\n`);
        expect(Object.keys(run.files).filter((file) => file.includes(SIBLING_ID))).toEqual([]);
        expect(run.log).toContain(
            `removed edh-${SIBLING_ID} and ~/.vams/hosts/${SIBLING_ID}: EC2 no longer lists ${SIBLING_ID}`
        );
    });

    test("points the active profile at the alias even when EC2 cannot be asked", () => {
        const run = runCleanup({
            seed: {
                ...desktopFiles(INSTANCE_ID),
                ...desktopFiles(GONE_ID),
                ...profileFiles("edh"),
            },
            failCall: 1,
            active: `edh-${INSTANCE_ID}`,
        });
        expect(run.argv).toEqual(["--profile edh profile switch edh --json-output"]);
    });

    (CAN_LINK ? test : test.skip)("does not follow a symlinked profile or host directory", () => {
        const outside = fs.mkdtempSync(path.join(os.tmpdir(), "vams-outside-"));
        try {
            fs.writeFileSync(path.join(outside, "keep.txt"), "keep\n");
            const run = runCleanup({
                seed: desktopFiles(INSTANCE_ID),
                links: {
                    [`.config/vamscli/profiles/edh-${GONE_ID}`]: outside,
                    [`.vams/hosts/${GONE_ID}`]: outside,
                },
            });
            expect(run.status).toBe(0);
            expect(fs.readFileSync(path.join(outside, "keep.txt"), "utf8")).toBe("keep\n");
            expect(run.files[`.vams/hosts/${GONE_ID}`]).toMatch(/^-> /);
            expect(run.awsCalls).toEqual([]);
        } finally {
            fs.rmSync(outside, { recursive: true, force: true });
        }
    });

    test("timestamps every line it logs", () => {
        const run = runCleanup({
            seed: {
                ...desktopFiles(INSTANCE_ID),
                ...desktopFiles(GONE_ID),
                ...profileFiles("edh"),
            },
            active: `edh-${INSTANCE_ID}`,
        });
        const logged = lines(run.log);
        expect(logged.length).toBeGreaterThan(1);
        for (const line of logged) {
            expect(line).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z /);
        }
    });

    test("looks for profiles, and the active one, where the CLI keeps them on Linux", () => {
        const constants = fs.readFileSync(
            path.join(REPO, "tools/VamsCLI/vamscli/constants.py"),
            "utf8"
        );
        const value = (name: string) =>
            new RegExp(`^${name} = "([^"]+)"`, "m").exec(constants)?.[1];
        expect(constants).toContain('config_dir = Path.home() / ".config" / PROFILE_DIR_NAME');
        const root = `\${HOME}/.config/${value("PROFILE_DIR_NAME")}`;
        expect(cleanupScript()).toContain(`PROFILES="${root}/${value("PROFILES_SUBDIR")}"`);
        expect(cleanupScript()).toContain(`${root}/${value("ACTIVE_PROFILE_FILE")}`);
        const profileSource = fs.readFileSync(
            path.join(REPO, "tools/VamsCLI/vamscli/utils/profile.py"),
            "utf8"
        );
        expect(profileSource).toContain(".get('active_profile', DEFAULT_PROFILE_NAME)");
        expect(cleanupScript()).toContain("'.active_profile // empty'");
    });

    test("recognises an instance id exactly as the instance id helper does", () => {
        const helper = installedScript("060-session-bootstrap.yml", "INSTANCEID");
        const pattern = /=~ (\^i-\S+\$) \]\]/.exec(helper)?.[1];
        expect(pattern).toBe("^i-[0-9a-f]{8,17}$");
        expect(cleanupScript()).toContain(`ID_PATTERN='${pattern}'`);
    });
});
