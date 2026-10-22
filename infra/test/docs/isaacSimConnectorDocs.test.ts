/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Isaac Sim connector pages and the connector's own messages give the VamsCLI install, the Kit
 * extension folder and the connector copy, and nothing else ties them to how the CLI and the
 * extension are packaged:
 *   * VamsCLI is installed from the repository, or from a wheel built from it, never from a package
 *     index;
 *   * the folder a page points Kit at holds the connector as `vams.connector.isaacsim`, and a page that
 *     copies the connector there removes the earlier copy first and configures the copy Kit loads.
 *
 * Durable (root CLAUDE.md Rule 13): every install instruction, folder and copy can be written wrong
 * again.
 */

import * as fs from "fs";
import * as path from "path";
import { REPO_ROOT, docsPages, exists, read } from "./support/docsText";

const CONNECTOR = "tools/ExternalIntegrations/isaacsim_vams_integration";
const PAGES = [
    `${CONNECTOR}/README.md`,
    "documentation/docusaurus-site/docs/developer/external-integrations/isaacsim-connector.md",
];

describe("Isaac Sim connector documentation matches how the connector is packaged", () => {
    describe("VamsCLI install", () => {
        // VamsCLI is installed from the repository, or from a wheel built from it. It is not published
        // on PyPI, so a bare `pip install vamscli` asks a package index for a package VAMS never put there.
        const FROM_INDEX = /pip install vamscli(?![\w./-])/;

        test("the installation page says VamsCLI is not on PyPI (control)", () => {
            expect(read("documentation/docusaurus-site/docs/cli/installation.md")).toContain(
                "VamsCLI is not available on PyPI"
            );
            expect(FROM_INDEX.test("pip install vamscli\n")).toBe(true);
            expect(FROM_INDEX.test("pip install vamscli-2.6.2-py3-none-any.whl")).toBe(false);
        });

        test("no documentation page or connector install file installs vamscli from a package index", () => {
            const files = [
                ...docsPages().map((rel) => `documentation/docusaurus-site/docs/${rel}`),
                `${CONNECTOR}/README.md`,
                `${CONNECTOR}/requirements.txt`,
            ];
            expect(files.filter((rel) => FROM_INDEX.test(read(rel)))).toEqual([]);
        });

        /** The connector's Python sources, whose docstrings and error messages give the install. */
        const connectorSources = (dir = `${CONNECTOR}/vams`): string[] =>
            fs.readdirSync(path.join(REPO_ROOT, dir), { withFileTypes: true }).flatMap((e) => {
                if (e.isDirectory()) {
                    return e.name === "__pycache__" ? [] : connectorSources(`${dir}/${e.name}`);
                }
                return e.name.endsWith(".py") ? [`${dir}/${e.name}`] : [];
            });

        test("no connector message or docstring installs vamscli from a package index", () => {
            const sources = connectorSources();
            expect(sources).toContain(`${CONNECTOR}/vams/connector/isaacsim/vams_cli_service.py`);
            expect(sources).toContain(`${CONNECTOR}/vams/connector/isaacsim/extension.py`);
            expect(sources.filter((rel) => FROM_INDEX.test(read(rel)))).toEqual([]);
        });
    });

    describe("Kit extension folder", () => {
        // Kit treats each child of an extension folder as an extension named after that child, so the
        // folder a page points Kit at holds the connector as vams.connector.isaacsim; the connector
        // directory itself is never that folder.
        /** The folders a page points Kit at: --ext-folder, a .kit `folders` list and a search-path block. */
        const extFolders = (text: string): string[] => [
            ...Array.from(text.matchAll(/--ext-folder (\S+)/g), (m) => m[1]),
            ...Array.from(text.matchAll(/^folders = \["([^"]+)"\]/gm), (m) => m[1]),
            ...Array.from(
                text.matchAll(/search path[^\n]*:\n\n```\w*\n([^\n]+)\n```/g),
                (m) => m[1]
            ),
        ];
        /** The folders a page places the connector in as `vams.connector.isaacsim`. */
        const parents = (text: string): string[] =>
            Array.from(text.matchAll(/(\S+)\/vams\.connector\.isaacsim(?![\w.])/g), (m) => m[1]);
        /** The folders a page copies the connector to, each on its `cp -r` line. */
        const copyLines = (lines: string[]): Array<[number, string]> =>
            lines.flatMap((l, i): Array<[number, string]> => {
                const m = /^cp -r \S+ (\S+\/vams\.connector\.isaacsim)$/.exec(l);
                return m ? [[i, m[1]]] : [];
            });

        test("the connector directory holds the extension, and the readers parse a page's folders (control)", () => {
            expect(exists(`${CONNECTOR}/config/extension.toml`)).toBe(true);
            const sample = [
                "isaac-sim.sh --ext-folder /opt/kit-exts",
                'folders = ["/opt/kit-exts"]',
                "Add this search path:",
                "",
                "```text",
                "/opt/kit-exts",
                "```",
                "rm -rf /opt/kit-exts/vams.connector.isaacsim",
                "cp -r isaacsim_vams_integration /opt/kit-exts/vams.connector.isaacsim",
            ].join("\n");
            expect(extFolders(sample)).toEqual(["/opt/kit-exts", "/opt/kit-exts", "/opt/kit-exts"]);
            expect(parents(sample)).toEqual(["/opt/kit-exts", "/opt/kit-exts"]);
            expect(copyLines(sample.split("\n"))).toEqual([
                [8, "/opt/kit-exts/vams.connector.isaacsim"],
            ]);
        });

        test.each(PAGES)(
            "%s points Kit at the folder that holds vams.connector.isaacsim",
            (rel) => {
                const text = read(rel);
                const folders = extFolders(text);
                expect(folders.length).toBeGreaterThanOrEqual(3);
                expect(parents(text).length).toBeGreaterThan(0);
                expect(folders.filter((f) => !parents(text).includes(f))).toEqual([]);
            }
        );

        // `cp -r` into a folder that exists copies the source inside it, where Kit does not look, so a
        // page that copies the connector removes the earlier copy first.
        test.each(PAGES)("%s removes the earlier copy before copying the connector", (rel) => {
            const lines = read(rel).split("\n");
            const copies = copyLines(lines);
            expect(copies.length).toBeGreaterThan(0);
            for (const [at, dest] of copies) {
                const fence = lines
                    .slice(0, at)
                    .map((l) => l.startsWith("```"))
                    .lastIndexOf(true);
                expect(lines.slice(fence + 1, at)).toContain(`rm -rf ${dest}`);
            }
        });

        // Kit loads the copy, so the CLI path setting and the certificate bundle go into it.
        test.each(PAGES)(
            "%s puts the settings and the certificate bundle into the copy Kit loads",
            (rel) => {
                const text = read(rel);
                const copies = copyLines(text.split("\n"));
                expect(copies.length).toBeGreaterThan(0);
                for (const [, dest] of copies) {
                    expect(text).toContain(`${dest}/config/extension.toml`);
                    expect(text).toContain(`${dest}/certs/`);
                }
                expect(text).not.toContain("isaacsim_vams_integration/certs/");
            }
        );
    });
});
