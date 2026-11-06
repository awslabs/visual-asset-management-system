#!/usr/bin/env node
/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Prepares the Gaussian splat samples the BabylonJS viewer render checks in e2e/viewers.spec.ts open,
 * and optionally uploads them with vamscli.
 *
 * Downloads the sample named in e2e/fixtures/viewer-samples.json, checks its SHA-256, and derives the
 * other formats from it: a .ply, SPZ version 3 and version 4 files (with @playcanvas/splat-transform,
 * installed through npm with the 7-day minimum release age), a raw .splat file, and the .sog itself.
 * Nothing is committed; the files land in the output directory.
 *
 *     node e2e/support/prepareSplatSamples.mjs --out /tmp/vams-splat-samples
 *     node e2e/support/prepareSplatSamples.mjs --out /tmp/vams-splat-samples \
 *         --database <databaseId> --asset <assetId> [--profile <vamscli profile>]
 *
 * The asset must allow file distribution (the download API refuses the files of one that does not).
 * Then run the spec against that asset:
 *
 *     E2E_VIEWER_ASSET=<databaseId>/<assetId> npx playwright test e2e/viewers.spec.ts
 */

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SPLAT_TRANSFORM = "@playcanvas/splat-transform@3.10.3";
const SAMPLE_ID = "gaussian-splat-apartment";
const SPLAT_COUNT = 150000;

const here = dirname(fileURLToPath(import.meta.url));
const manifest = JSON.parse(
    readFileSync(join(here, "..", "fixtures", "viewer-samples.json"), "utf8")
);

const args = process.argv.slice(2);
const arg = (name) => {
    const i = args.indexOf(`--${name}`);
    return i >= 0 ? args[i + 1] : undefined;
};
const out = resolve(arg("out") ?? "vams-splat-samples");
mkdirSync(out, { recursive: true });

const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

const download = async (sample) => {
    const target = join(out, sample.file);
    if (existsSync(target) && sha256(readFileSync(target)) === sample.sha256) return target;
    const response = await fetch(sample.url);
    if (!response.ok) throw new Error(`${sample.url}: HTTP ${response.status}`);
    const body = Buffer.from(await response.arrayBuffer());
    const digest = sha256(body);
    if (digest !== sample.sha256) {
        throw new Error(`${sample.file}: SHA-256 ${digest} does not match the manifest`);
    }
    writeFileSync(target, body);
    return target;
};

const splatTransform = (...cliArgs) =>
    execFileSync(
        "npm",
        [
            "exec",
            "--yes",
            "--min-release-age=7",
            `--package=${SPLAT_TRANSFORM}`,
            "--",
            "splat-transform",
            "-q",
            "-w",
            ...cliArgs,
        ],
        { stdio: "inherit" }
    );

/** A binary little-endian 3DGS .ply (float properties) written as raw 32-byte .splat rows. */
const plyToSplat = (plyPath, splatPath) => {
    const buf = readFileSync(plyPath);
    const marker = "end_header\n";
    const end = buf.indexOf(marker) + marker.length;
    const header = buf.subarray(0, end).toString("latin1");
    const count = Number(/element vertex (\d+)/.exec(header)[1]);
    const props = [...header.matchAll(/property (\w+) (\w+)/g)];
    if (props.some(([, type]) => type !== "float")) {
        throw new Error(`${plyPath}: only float PLY properties are supported`);
    }
    const offset = Object.fromEntries(props.map(([, , name], i) => [name, i * 4]));
    const stride = props.length * 4;
    const rows = Buffer.alloc(count * 32);
    const SH_C0 = 0.28209479177387814;
    const byte = (v) => Math.max(0, Math.min(255, Math.round(v)));
    for (let i = 0; i < count; i++) {
        const base = end + i * stride;
        const f = (name) => buf.readFloatLE(base + offset[name]);
        const w = i * 32;
        ["x", "y", "z"].forEach((n, k) => rows.writeFloatLE(f(n), w + k * 4));
        ["scale_0", "scale_1", "scale_2"].forEach((n, k) =>
            rows.writeFloatLE(Math.exp(f(n)), w + 12 + k * 4)
        );
        ["f_dc_0", "f_dc_1", "f_dc_2"].forEach((n, k) => {
            rows[w + 24 + k] = byte((0.5 + SH_C0 * f(n)) * 255);
        });
        rows[w + 27] = byte(255 / (1 + Math.exp(-f("opacity"))));
        const q = ["rot_0", "rot_1", "rot_2", "rot_3"].map(f);
        const norm = Math.hypot(...q) || 1;
        q.forEach((v, k) => {
            rows[w + 28 + k] = byte((v / norm) * 128 + 128);
        });
    }
    writeFileSync(splatPath, rows);
};

const sample = manifest.samples.find((s) => s.id === SAMPLE_ID);
const sog = await download(sample);
const file = (name) => join(out, name);

splatTransform("-g", "cpu", sog, "-d", String(SPLAT_COUNT), file("vams-e2e-splat.ply"));
splatTransform(file("vams-e2e-splat.ply"), "--spz-version", "3", file("vams-e2e-splat-v3.spz"));
splatTransform(file("vams-e2e-splat.ply"), "--spz-version", "4", file("vams-e2e-splat-v4.spz"));
plyToSplat(file("vams-e2e-splat.ply"), file("vams-e2e-splat.splat"));
writeFileSync(file("vams-e2e-splat.sog"), readFileSync(sog));

const derived = sample.derived.map((d) => file(d.file));
for (const path of derived) console.log(`${path}  ${readFileSync(path).length} bytes`);

const database = arg("database");
const asset = arg("asset");
if (database && asset) {
    const profile = arg("profile");
    for (const path of derived) {
        execFileSync(
            "vamscli",
            [
                ...(profile ? ["--profile", profile] : []),
                "file",
                "upload",
                path,
                "-d",
                database,
                "-a",
                asset,
            ],
            { stdio: "inherit" }
        );
    }
}
