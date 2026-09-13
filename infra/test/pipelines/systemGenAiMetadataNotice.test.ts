/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** NOTICE.md lists every pinned package of the three system GenAI metadata images at its pinned version. */

import * as fs from "fs";
import * as path from "path";

const REPO = path.join(__dirname, "..", "..", "..");
const REQUIREMENTS = [
    "backendPipelines/system/genAiMetadata/containers/blender/requirements.txt",
    "backendPipelines/system/genAiMetadata/containers/media/requirements.txt",
    "backendPipelines/preview/3dThumbnail/container/requirements.lambda.txt",
];
const notice = fs.readFileSync(path.join(REPO, "NOTICE.md"), "utf8");
const SECTION_HEADING = "### System GenAI Metadata Generation Pipeline";
const sectionStart = notice.indexOf(SECTION_HEADING);
const section =
    sectionStart === -1
        ? ""
        : notice.slice(
              sectionStart,
              (() => {
                  const next = notice.indexOf("\n### ", sectionStart + SECTION_HEADING.length);
                  return next === -1 ? notice.length : next;
              })()
          );

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function pins(file: string): Array<[string, string]> {
    return fs
        .readFileSync(path.join(REPO, file), "utf8")
        .split(/\r?\n/)
        .map((l) => /^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^\s;#]+)/.exec(l.trim()))
        .filter((m): m is RegExpExecArray => m !== null)
        .map((m) => [m[1], m[2]]);
}

describe("NOTICE.md system GenAI metadata section", () => {
    test("exists and is bounded by the next section", () => {
        expect(section.length).toBeGreaterThan(0);
        expect(section).toContain("**Blender render image**");
        expect(section).toContain("**3D render Lambda image (Dockerfile.lambda)**");
        expect(section).toContain("**Media extraction image**");
    });

    test.each(REQUIREMENTS)("lists every pin of %s", (file) => {
        const found = pins(file);
        expect(found.length).toBeGreaterThan(3); // control: the requirements file was parsed
        const missing = found.filter(([name, version]) => {
            const row = new RegExp(
                `^\\|\\s*${escape(name)}\\s*\\|\\s*${escape(version)}\\s*\\|`,
                "mi"
            );
            return !row.test(section);
        });
        expect(missing).toEqual([]);
    });

    test("names Blender at the image's version and the bundled FFmpeg", () => {
        const blender = /ARG BLENDER_VERSION=(\S+)/.exec(
            fs.readFileSync(
                path.join(
                    REPO,
                    "backendPipelines/system/genAiMetadata/containers/blender/Dockerfile"
                ),
                "utf8"
            )
        )![1];
        expect(section).toMatch(new RegExp(`\\|\\s*Blender\\s*\\|\\s*${escape(blender)}\\s*\\|`));
        expect(section).toMatch(/FFmpeg/);
        expect(section).toMatch(/GPL/);
        expect(section).toContain("**Blender License Note**");
    });

    test("every row of the section carries a license", () => {
        const rows = section
            .split(/\r?\n/)
            .filter((l) => l.startsWith("| ") && !l.startsWith("| Name") && !l.startsWith("| :"));
        expect(rows.length).toBeGreaterThan(30);
        for (const row of rows) {
            const cells = row.split("|").map((c) => c.trim());
            expect({ row, license: cells[3] }).toEqual({
                row,
                license: expect.stringMatching(/\S/),
            });
        }
    });

    test("the retired pipeline tables are gone", () => {
        expect(notice).not.toContain("GenAI Metadata 3D Labeling Pipeline");
        expect(notice).not.toContain("Mesh/CAD Metadata Extraction Pipeline");
        expect(notice).not.toContain("GENAI 3D METADATA GENERATION");
    });
});
