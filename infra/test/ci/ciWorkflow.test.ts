/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Properties of `.github/workflows/ci.yml` that no build step checks: the workflow is only run by
 * GitHub, so an edit that drops one of them passes every local gate.
 *
 * Steps are found by their content (the command a `run:` line calls), never by job, step name or
 * line, so the checks hold when steps move between jobs.
 */

import { read } from "../docs/support/docsText";

const CI = ".github/workflows/ci.yml";

/** Every `run:` command in the workflow, single-line and block form, one entry per command line. */
function runCommands(text: string): string[] {
    const lines = text.split("\n");
    const commands: string[] = [];
    for (let i = 0; i < lines.length; i++) {
        const m = /^(\s*)(?:- )?run:\s*(.*)$/.exec(lines[i]);
        if (!m) {
            continue;
        }
        const inline = m[2].trim();
        if (inline !== "|" && inline !== ">" && inline !== "") {
            commands.push(inline);
            continue;
        }
        const indent = m[1].length;
        for (let j = i + 1; j < lines.length; j++) {
            const body = lines[j];
            if (body.trim() === "") {
                continue;
            }
            if (body.length - body.trimStart().length <= indent) {
                break;
            }
            commands.push(body.trim());
        }
    }
    return commands;
}

const jestCommands = (text: string) =>
    runCommands(text).filter((command) => /(^|\s)(npx\s+)?jest(\s|$)/.test(command));

const indentOf = (line: string): number => line.length - line.trimStart().length;

/**
 * The `python-version` input of every `actions/setup-python` step, in workflow order, with quotes
 * and a trailing comment removed; `undefined` for a step that sets none.
 */
function setupPythonVersions(text: string): (string | undefined)[] {
    const lines = text.split("\n");
    const versions: (string | undefined)[] = [];
    lines.forEach((line, i) => {
        const m = /^(\s*)(- )?uses:\s*actions\/setup-python@/.exec(line);
        if (!m) {
            return;
        }
        // The step's keys sit at the indent of `uses:`; the step ends at the first line indented less.
        const keyIndent = m[1].length + (m[2] ? 2 : 0);
        let start = i;
        while (start > 0 && (lines[start].trim() === "" || indentOf(lines[start]) >= keyIndent)) {
            start--;
        }
        let end = i + 1;
        while (
            end < lines.length &&
            (lines[end].trim() === "" || indentOf(lines[end]) >= keyIndent)
        ) {
            end++;
        }
        const step = lines.slice(start, end);
        const withAt = step.findIndex((l) => indentOf(l) === keyIndent && /^\s*with:\s*$/.test(l));
        let version: string | undefined;
        for (let j = withAt + 1; withAt >= 0 && j < step.length; j++) {
            if (step[j].trim() !== "" && indentOf(step[j]) <= keyIndent) {
                break;
            }
            const input = /^\s+python-version:\s*(.*?)\s*(?:#.*)?$/.exec(step[j]);
            if (input) {
                version = input[1].replace(/^(["'])(.*)\1$/, "$2");
            }
        }
        versions.push(version);
    });
    return versions;
}

/**
 * Python releases that the setup-python versions manifest
 * (https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json) has linux
 * x64 builds for on every GitHub-hosted Ubuntu runner image: 22.04, 24.04 and 26.04. Read from the
 * manifest on 2026-11-05. setup-python takes a release from the runner's tool cache or, failing
 * that, from the manifest, and fails at "Setup Python Version" when neither has the pinned release
 * for the runner's image (3.12.7, for example, has no 26.04 build). To rebuild the list, for the
 * 3.12 and 3.13 minors:
 *   curl -s <manifest URL> | python3 -c "import json,sys; imgs={'22.04','24.04','26.04'}; print([r['version'] for r in json.load(sys.stdin) if r['version'][:4] in ('3.12','3.13') and imgs <= {f.get('platform_version') for f in r['files'] if f.get('platform')=='linux' and f.get('arch')=='x64'}])"
 */
const PYTHON_BUILT_FOR_EVERY_UBUNTU_IMAGE = [
    "3.12.13",
    "3.12.14",
    "3.12.15",
    "3.12.16",
    "3.13.13",
    "3.13.14",
    "3.13.15",
    "3.13.16",
    "3.13.17",
    "3.13.18",
];

/**
 * A pinned release must be on the list. A minor-only version (`3.13`) resolves to that minor's
 * newest release in the manifest, so it needs one release of that minor on the list.
 */
const hasBuildOnEveryUbuntuImage = (version: string): boolean =>
    /^\d+\.\d+\.\d+$/.test(version)
        ? PYTHON_BUILT_FOR_EVERY_UBUNTU_IMAGE.includes(version)
        : /^\d+\.\d+$/.test(version) &&
          PYTHON_BUILT_FOR_EVERY_UBUNTU_IMAGE.some((release) => release.startsWith(`${version}.`));

describe("CI workflow", () => {
    const text = read(CI);

    test("the workflow runs the web Jest suite (control)", () => {
        expect(jestCommands(text).length).toBeGreaterThan(0);
    });

    test("every Jest run collects coverage, so the web coverage thresholds apply", () => {
        jestCommands(text).forEach((command) => {
            expect(command).toContain("--coverage");
        });
    });

    test("the web and backend suites run on pull requests as well as pushes", () => {
        expect(runCommands(text).some((command) => /^pytest\b/.test(command))).toBe(true);
        expect(text).not.toMatch(/if:\s*\$?\{?\{?\s*github\.event_name\s*==\s*['"]push['"]/);
    });

    test("the backend suite runs in parallel, one test file per worker", () => {
        const step =
            /- name: Run backend pytest tests\n([\s\S]*?)(\n\s*\n|$)/.exec(text)?.[1] ?? "";
        expect(step).toContain("working-directory: backend");
        const pytestRuns = runCommands(step).filter((command) => /^pytest\b/.test(command));
        expect(pytestRuns.length).toBe(1);
        expect(pytestRuns[0]).toMatch(/(^|\s)-n auto(\s|$)/);
        expect(pytestRuns[0]).toMatch(/(^|\s)--dist loadfile(\s|$)/);
        expect(read("backend/requirements-dev.txt")).toMatch(/^pytest-xdist==\d+\.\d+\.\d+ ;/m);
    });

    test("push and pull_request both filter on main, release, development and development-*", () => {
        const filters = [...text.matchAll(/^\s+branches:\s*\[(.*)\]\s*$/gm)].map((m) =>
            m[1].split(",").map((b) => b.trim().replace(/^"|"$/g, ""))
        );
        expect(filters.length).toBe(2);
        filters.forEach((branches) => {
            expect(branches).toEqual(
                expect.arrayContaining(["main", "release/**", "development", "development-*"])
            );
        });
    });

    test("a new pull request commit cancels that pull request's older run", () => {
        expect(text).toMatch(
            /^concurrency:\n\s+group: \$\{\{ github\.workflow \}\}-\$\{\{ github\.event\.pull_request\.number \|\| github\.run_id \}\}\n\s+cancel-in-progress: true$/m
        );
    });

    test("the infra shards install the root packages the docs prettier test resolves", () => {
        const infraJob = /^ {4}infra-test:\n([\s\S]*?)\n {4}\S/m.exec(text)?.[1] ?? "";
        expect(infraJob).toMatch(/- name: Npm ci root directory\n\s+run: npm ci/);
    });

    describe("infra type check", () => {
        // The infra Jest suite compiles transpile-only, so this is the only place an infra type error
        // fails the build.
        test("the infra Jest transform is transpile-only (control: why the type check is needed)", () => {
            const tsconfig = JSON.parse(read("infra/tsconfig.json"));
            expect(tsconfig.compilerOptions.isolatedModules).toBe(true);
        });

        test("a CI step runs the infra typecheck script", () => {
            const pkg = JSON.parse(read("infra/package.json"));
            expect(pkg.scripts.typecheck).toMatch(/^tsc --noEmit\b/);
            // Not narrowed by an `include`/`files` list, so lib, bin, config and test are all checked.
            const tsconfig = JSON.parse(read("infra/tsconfig.json"));
            expect(tsconfig.include).toBeUndefined();
            expect(tsconfig.files).toBeUndefined();
            const job = /^ {4}infra-typecheck:\n([\s\S]*?)\n(?= {4}\S|\S)/m.exec(text)?.[1] ?? "";
            expect(job).toMatch(/working-directory: infra\n\s+run: npm ci\b/);
            expect(job).toMatch(/working-directory: infra\n\s+run: npm run typecheck$/m);
        });

        test("the type check job grants no permissions beyond the read-only default", () => {
            const job = /^ {4}infra-typecheck:\n([\s\S]*?)\n(?= {4}\S|\S)/m.exec(text)?.[1] ?? "";
            expect(job).not.toBe("");
            expect(job).not.toMatch(/permissions:/);
            expect(text).toMatch(/^permissions:\n\s+contents: read$/m);
        });
    });

    describe("Python setup", () => {
        const versions = setupPythonVersions(text);

        test("every setup-python step is found and sets python-version (control)", () => {
            expect(versions.length).toBeGreaterThan(0);
            expect(versions.length).toBe(
                (text.match(/uses:\s*actions\/setup-python@/g) ?? []).length
            );
            versions.forEach((version) => expect(version).toMatch(/\S/));
        });

        test("every job runs on an Ubuntu image the recorded build list covers", () => {
            const images = [...text.matchAll(/^\s+runs-on:\s*(\S+)\s*$/gm)].map((m) => m[1]);
            expect(images.length).toBeGreaterThan(0);
            // A runs-on that is not a single literal label (a matrix expression, a list, a label
            // array) is not read above, so it would pass unchecked; every runs-on must be read.
            expect(images.length).toBe((text.match(/^\s+runs-on:/gm) ?? []).length);
            images.forEach((image) => {
                expect(["ubuntu-latest", "ubuntu-22.04", "ubuntu-24.04", "ubuntu-26.04"]).toContain(
                    image
                );
            });
        });

        test("every setup-python version has a build on every Ubuntu runner image", () => {
            const missing = versions.filter(
                (version) => version === undefined || !hasBuildOnEveryUbuntuImage(version)
            );
            if (missing.length > 0) {
                throw new Error(
                    `setup-python versions with no recorded build for every Ubuntu image: ${JSON.stringify(
                        missing
                    )}\n` +
                        `  recorded: ${PYTHON_BUILT_FOR_EVERY_UBUNTU_IMAGE.join(", ")}\n` +
                        "Pin a release that versions-manifest.json lists with linux x64 files for " +
                        "platform_version 22.04, 24.04 and 26.04, and add it to " +
                        "PYTHON_BUILT_FOR_EVERY_UBUNTU_IMAGE in this test. When GitHub adds an Ubuntu " +
                        "image, re-read the manifest for it and update the list and its comment."
                );
            }
        });
    });

    describe("pipeline and VamsMCP suites", () => {
        const job = (name: string): string =>
            new RegExp(`^ {4}${name}:\\n([\\s\\S]*?)(?=\\n {4}\\S|$(?![\\s\\S]))`, "m").exec(
                text
            )?.[1] ?? "";

        test("one job per pipeline test group runs the discovering runner", () => {
            const pipelines = job("pipelines-test");
            expect(pipelines).toMatch(/^\s+group: \[1, 2, 3\]$/m);
            const runs = runCommands(pipelines).filter((command) =>
                command.startsWith("python backendPipelines/ci/pipeline_tests.py run")
            );
            expect(runs).toEqual([
                'python backendPipelines/ci/pipeline_tests.py run --group ${{ matrix.group }} --work-dir "$RUNNER_TEMP/pipeline-tests" --low-disk',
            ]);
            // The runner's group count is GROUP_COUNT; backendPipelines/tests/test_ci_pipeline_suites.py
            // holds the matrix to it, and the runner itself to the test directories on disk.
            expect(read("backendPipelines/ci/pipeline_tests.py")).toMatch(/^GROUP_COUNT = 3$/m);
        });

        test("each pipeline group installs the system packages the runner names, before its tests", () => {
            const pipelines = job("pipelines-test");
            const install = pipelines.indexOf(
                "python backendPipelines/ci/pipeline_tests.py system-packages --group ${{ matrix.group }}"
            );
            const run = pipelines.indexOf("python backendPipelines/ci/pipeline_tests.py run");
            expect(install).toBeGreaterThanOrEqual(0);
            expect(run).toBeGreaterThan(install);
            expect(pipelines).toContain(
                'sudo apt-get install -y --no-install-recommends "${packages[@]}"'
            );
            // A rendering suite runs under xvfb-run; the runner names xvfb for its group.
            expect(read("backendPipelines/ci/pipeline_tests.py")).toMatch(
                /^DISPLAY_SYSTEM_PACKAGES = \("xvfb",/m
            );
        });

        test("the VamsMCP suite installs VamsCLI before itself, then runs pytest", () => {
            const commands = runCommands(job("vamsmcp-test"));
            const cli = commands.indexOf("pip install -e ../VamsCLI");
            const mcp = commands.indexOf('pip install -e ".[dev]"');
            expect(cli).toBeGreaterThanOrEqual(0);
            expect(mcp).toBeGreaterThan(cli);
            expect(commands.some((command) => /^pytest\b/.test(command))).toBe(true);
        });

        test("both jobs carry a timeout", () => {
            ["pipelines-test", "vamsmcp-test"].forEach((name) => {
                expect(job(name)).toMatch(/^ {8}timeout-minutes: \d+$/m);
            });
        });
    });

    describe("automated security helper scan", () => {
        const ashCheckout = (): string =>
            /- name: Clone automated security helper\n([\s\S]*?)\n\s*\n/.exec(text)?.[1] ?? "";

        test("the scan is cloned at a pinned commit", () => {
            expect(ashCheckout()).toContain("repository: aws-samples/automated-security-helper");
            expect(ashCheckout()).toMatch(/^\s+ref: [0-9a-f]{40}(\s+#.*)?$/m);
        });

        test("the scan's exit code is captured, not discarded", () => {
            const ashRuns = runCommands(text).filter((command) => /^ash\s/.test(command));
            expect(ashRuns.length).toBe(1);
            expect(ashRuns[0]).not.toMatch(/\|\|\s*true\s*$/);
            expect(ashRuns[0]).toContain("|| rc=$?");
        });

        test("results go to a non-hidden directory the report upload reads", () => {
            const outputDir = /ash .*--output-dir "\$GITHUB_WORKSPACE\/([^"]+)"/.exec(text)?.[1];
            expect(outputDir).toBeDefined();
            expect(outputDir?.startsWith(".")).toBe(false);
            expect(text).toMatch(new RegExp(`^\\s+path: ${outputDir}/?$`, "m"));
        });

        test("the summary step fails only when the scan produced no results", () => {
            const summary =
                /- name: Summarize automated security helper results\n([\s\S]*?)\n\s*\n/.exec(
                    text
                )?.[1];
            expect(summary).toBeDefined();
            expect(summary).toContain("set +e");
            expect(summary).toContain("GITHUB_STEP_SUMMARY");
            // A missing results file exits 3, which is the only path to a failed step.
            expect(summary).toContain("raise SystemExit(3)");
            expect(summary).toMatch(/if \[ "\$summary_rc" -eq 3 \]; then\n\s+exit 1\n/);
            expect(summary?.match(/exit 1/g)?.length).toBe(1);
            expect(summary?.trimEnd().endsWith("exit 0")).toBe(true);
        });

        test("the base image is pulled with retries before the scan, with a Docker Hub fallback", () => {
            const pull = /- name: Pull the ASH base image\n([\s\S]*?)\n\s*\n/.exec(text)?.[1];
            expect(pull).toBeDefined();
            expect(pull).toContain("ash/Dockerfile");
            expect(pull).toContain("--build-arg BASE_IMAGE=");
            expect(text.indexOf("- name: Pull the ASH base image")).toBeLessThan(
                text.indexOf("- name: Run automated security helper")
            );
        });

        describe("Trivy for the ASH image", () => {
            const step =
                /- name: Provide a pinned Trivy to the ASH image build\n([\s\S]*?)\n\s*\n/.exec(
                    text
                )?.[1] ?? "";
            const commands = runCommands(step);

            test("the Trivy version and the archive's SHA-256 are pinned in the workflow", () => {
                expect(step).toMatch(/^\s+TRIVY_VERSION: "\d+\.\d+\.\d+"$/m);
                expect(step).toMatch(/^\s+TRIVY_SHA256: [0-9a-f]{64}(\s+#.*)?$/m);
            });

            test("the archive comes from the GitHub release and is verified before use", () => {
                const download = commands.findIndex((command) =>
                    command.includes(
                        "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/"
                    )
                );
                const verify = commands.findIndex(
                    (command) =>
                        command.includes("${TRIVY_SHA256}") &&
                        command.includes("sha256sum --check --strict")
                );
                const extract = commands.findIndex((command) => /^tar -xzf /.test(command));
                expect(download).toBeGreaterThanOrEqual(0);
                expect(verify).toBeGreaterThan(download);
                expect(extract).toBeGreaterThan(verify);
                expect(commands[0]).toBe("set -euo pipefail");
            });

            test("no install script or get.trivy.dev download is used", () => {
                expect(step).not.toContain("get.trivy.dev");
                expect(step).not.toMatch(/\|\s*(ba)?sh\b/);
                expect(text).not.toContain("get.trivy.dev/trivy");
            });

            test("the ASH install line is swapped for the verified binary, failing when absent", () => {
                expect(step).toContain("ash/Dockerfile");
                expect(step).toContain('ARG TRIVY_VERSION="v{version}"');
                expect(step).toContain("COPY ci-trivy/trivy /usr/local/bin/trivy");
                expect(step).toMatch(
                    /if count != 1:\n\s+print\(f"::error[^\n]*\n\s+sys\.exit\(1\)/
                );
            });

            // The Trivy pins are only valid for the ASH commit they were taken from: ASH's Dockerfile
            // names the Trivy version, and the step refuses any other. Moving one without the other
            // fails here first, with the steps to follow, rather than in the ash job on GitHub.
            const RECORDED_PINS = {
                ashRef: "af1206aff596f1288ad066828058b3011de2a998", // pragma: allowlist secret
                trivyVersion: "0.69.3",
                trivySha256: "1816b632dfe529869c740c0913e36bd1629cb7688bd5634f4a858c1d57c88b75", // pragma: allowlist secret
            };

            test("the Trivy pins move together with the ASH ref", () => {
                const current = {
                    ashRef: /^\s+ref: ([0-9a-f]{40})/m.exec(ashCheckout())?.[1] ?? "",
                    trivyVersion: /^\s+TRIVY_VERSION: "([^"]+)"/m.exec(step)?.[1] ?? "",
                    trivySha256: /^\s+TRIVY_SHA256: ([0-9a-f]{64})/m.exec(step)?.[1] ?? "",
                };
                if (JSON.stringify(current) !== JSON.stringify(RECORDED_PINS)) {
                    throw new Error(
                        "The ASH ref and the Trivy pins in .github/workflows/ci.yml changed apart.\n" +
                            `  recorded: ${JSON.stringify(RECORDED_PINS)}\n` +
                            `  workflow: ${JSON.stringify(current)}\n` +
                            "After an ASH bump: read ARG TRIVY_VERSION in the ASH Dockerfile at the new ref, " +
                            "set TRIVY_VERSION to it and TRIVY_SHA256 to the sha256 of " +
                            "trivy_<version>_Linux-64bit.tar.gz from that Trivy release's checksums file, " +
                            "then update RECORDED_PINS in this test to all three values."
                    );
                }
            });

            test("it runs after the ASH clone and before the scan", () => {
                const at = text.indexOf("- name: Provide a pinned Trivy to the ASH image build");
                expect(at).toBeGreaterThan(text.indexOf("- name: Clone automated security helper"));
                expect(at).toBeLessThan(text.indexOf("- name: Run automated security helper"));
            });
        });

        // The scan reads `.ash/.ash.yaml` from the checked-out source. It turns off the report
        // formats nothing reads, which is most of the job's time, and nothing else.
        describe("ASH config", () => {
            const config = read(".ash/.ash.yaml");
            const reporters = /^reporters:\n((?:[ #].*\n|\n)*?)(?=^\S)/m.exec(config)?.[1] ?? "";
            const disabled = [...reporters.matchAll(/^ {4}([a-z-]+):\n {8}enabled: false$/gm)].map(
                (m) => m[1]
            );

            test("only the unused report formats are disabled", () => {
                expect(disabled.sort()).toEqual([
                    "csv",
                    "cyclonedx",
                    "flat-json",
                    "gitlab-cyclonedx",
                    "gitlab-sast",
                    "junitxml",
                    "ocsf",
                    "spdx",
                    "yaml",
                ]);
            });

            test("the formats the report artifact keeps stay enabled", () => {
                ["sarif", "html", "markdown", "text"].forEach((format) => {
                    expect(disabled).not.toContain(format);
                });
            });

            test("no scanner, ignore path or threshold is set", () => {
                expect(config).not.toMatch(/^scanners:/m);
                expect(config).not.toMatch(/^\s+ignore_paths:/m);
                expect(config).not.toMatch(/severity_threshold/);
                expect(config).toMatch(/^global_settings:\n {4}suppressions:\n/m);
            });
        });
    });
});
