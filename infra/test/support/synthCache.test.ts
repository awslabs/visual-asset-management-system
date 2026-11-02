/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The per-run shared synth cache (`synthCache.ts`): keying, the cross-process lock, and the switches.
 *
 * None of this synthesizes anything: the payload is a counter, so the tests run in seconds and prove
 * the mechanism rather than one template.
 */

import { spawn, spawnSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import {
    canonicalJson,
    firstDifference,
    sharedSynth,
    synthCacheKey,
    synthCacheMode,
    SYNTH_CACHE_DIR_ENV,
    SYNTH_CACHE_MODE_ENV,
} from "./synthCache";
import { fsIsMocked } from "./templateSynth";

// eslint-disable-next-line @typescript-eslint/no-var-requires
const jestConfig = require("../../jest.config.js");

const ident = (s: string) => s;

describe("synth cache keying", () => {
    test("key order does not change the canonical form; values do", () => {
        expect(canonicalJson({ a: 1, b: { c: [1, 2], d: "x" } })).toBe(
            canonicalJson({ b: { d: "x", c: [1, 2] }, a: 1 })
        );
        expect(canonicalJson({ a: 1 })).not.toBe(canonicalJson({ a: 2 }));
        expect(synthCacheKey("x")).toBe(synthCacheKey("x"));
        expect(synthCacheKey("x")).not.toBe(synthCacheKey("y"));
    });

    test("an undefined property is the same as an absent one, as in JSON", () => {
        expect(canonicalJson({ a: 1, b: undefined })).toBe(canonicalJson({ a: 1 }));
    });

    test("anything that is not plain JSON data is refused rather than flattened", () => {
        // JSON.stringify would turn each of these into something another value also produces.
        expect(canonicalJson({ f: () => 1 })).toBeUndefined();
        expect(canonicalJson({ r: /x/ })).toBeUndefined();
        expect(canonicalJson({ d: new Date(0) })).toBeUndefined();
        expect(canonicalJson({ n: NaN })).toBeUndefined();
        // Control: plain data is accepted.
        expect(canonicalJson({ ok: [null, true, "s", 1.5] })).toBe('{"ok":[null,true,"s",1.5]}');
    });
});

describe("shared synth cache", () => {
    const saved = {
        dir: process.env[SYNTH_CACHE_DIR_ENV],
        mode: process.env[SYNTH_CACHE_MODE_ENV],
    };
    let dir: string;

    beforeEach(() => {
        dir = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), "vams-synth-cache-test-"));
        process.env[SYNTH_CACHE_DIR_ENV] = dir;
        delete process.env[SYNTH_CACHE_MODE_ENV];
    });

    afterEach(() => {
        fs.rmSync(dir, { recursive: true, force: true });
        for (const [k, v] of [
            [SYNTH_CACHE_DIR_ENV, saved.dir],
            [SYNTH_CACHE_MODE_ENV, saved.mode],
        ] as const) {
            if (v === undefined) delete process.env[k];
            else process.env[k] = v;
        }
    });

    test("this file does not mock fs, so its synths would share (control for synthCacheFsMock)", () => {
        expect(fsIsMocked()).toBe(false);
    });

    test("jest.config.js creates and removes the per-run directory", () => {
        expect(jestConfig.globalSetup).toMatch(/synthCacheGlobalSetup\.ts$/);
        expect(jestConfig.globalTeardown).toMatch(/synthCacheGlobalTeardown\.ts$/);
    });

    test("a key is produced once and then read back from disk", () => {
        let produced = 0;
        const produce = () => `value-${++produced}`;
        expect(sharedSynth("k1", produce, ident, ident)).toBe("value-1");
        expect(sharedSynth("k1", produce, ident, ident)).toBe("value-1");
        expect(produced).toBe(1);
        // Control: a different key is a different synth.
        expect(sharedSynth("k2", produce, ident, ident)).toBe("value-2");
        expect(fs.readdirSync(dir).sort()).toEqual(["k1.json", "k2.json"]);
    });

    test("a producer that throws caches nothing and releases the lock", () => {
        expect(() =>
            sharedSynth(
                "bad",
                () => {
                    throw new Error("synth failed");
                },
                ident,
                ident
            )
        ).toThrow("synth failed");
        expect(fs.readdirSync(dir)).toEqual([]);
        expect(sharedSynth("bad", () => "ok", ident, ident)).toBe("ok");
    });

    test("a lock left by a process that has exited is taken over", () => {
        const dead = spawnSync(process.execPath, ["-e", ""]);
        expect(dead.pid).toBeGreaterThan(0);
        fs.mkdirSync(path.join(dir, "stale.lock"));
        fs.writeFileSync(path.join(dir, "stale.lock", "owner"), String(dead.pid));
        expect(sharedSynth("stale", () => "taken over", ident, ident)).toBe("taken over");
    });

    test("off: no directory is used and every call produces", () => {
        process.env[SYNTH_CACHE_MODE_ENV] = "off";
        expect(synthCacheMode()).toBe("off");
        let produced = 0;
        sharedSynth("k", () => String(++produced), ident, ident);
        sharedSynth("k", () => String(++produced), ident, ident);
        expect(produced).toBe(2);
        expect(fs.readdirSync(dir)).toEqual([]);
    });

    test("no directory published (jest.config.js bypassed): the cache is off", () => {
        delete process.env[SYNTH_CACHE_DIR_ENV];
        expect(synthCacheMode()).toBe("off");
    });

    test("verify: a hit that differs from a fresh synth fails", () => {
        sharedSynth("v", () => "first", ident, ident);
        process.env[SYNTH_CACHE_MODE_ENV] = "verify";
        expect(sharedSynth("v", () => "first", ident, ident)).toBe("first");
        expect(() => sharedSynth("v", () => "second", ident, ident)).toThrow(
            /differs from the cached/
        );
    });

    test("verify: what the comparable form discards does not fail it; anything else does", () => {
        const noDigits = (s: string) => s.replace(/\d/g, "#");
        sharedSynth("t", () => "made at 10:00", ident, ident, noDigits);
        process.env[SYNTH_CACHE_MODE_ENV] = "verify";
        expect(sharedSynth("t", () => "made at 11:30", ident, ident, noDigits)).toBe(
            "made at 10:00"
        );
        expect(() => sharedSynth("t", () => "made on 11:30", ident, ident, noDigits)).toThrow(
            /differs from the cached one at character 5/
        );
    });

    test("verify names the JSON path of the first difference", () => {
        expect(firstDifference('{"a":{"b":1,"c":2}}', '{"a":{"b":1,"c":3}}')).toBe("/a/c: 2 vs 3");
        expect(firstDifference('{"a":1}', '{"a":1}')).toBe("key order only");
    });

    test("concurrent processes asking for one key produce it once and all read the same value", async () => {
        // Each child sleeps inside produce(), so they overlap; the counter file records every produce.
        const counter = path.join(dir, "produced.log");
        const script = `
            const fs = require("fs");
            const { sharedSynth } = require(${JSON.stringify(path.join(__dirname, "synthCache"))});
            const out = sharedSynth("race", () => {
                fs.appendFileSync(${JSON.stringify(counter)}, process.pid + "\\n");
                Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1500);
                return "from " + process.pid;
            }, (s) => s, (s) => s);
            process.stdout.write(out);
        `;
        const run = () =>
            new Promise<string>((resolve, reject) => {
                const child = spawn(
                    process.execPath,
                    ["-r", "ts-node/register/transpile-only", "-e", script],
                    { cwd: path.join(__dirname, "..", ".."), env: process.env }
                );
                let stdout = "";
                let stderr = "";
                child.stdout.on("data", (d) => (stdout += d));
                child.stderr.on("data", (d) => (stderr += d));
                child.on("close", (code) =>
                    code === 0 ? resolve(stdout) : reject(new Error(`child ${code}: ${stderr}`))
                );
            });
        const outputs = await Promise.all([run(), run(), run()]);
        const producers = fs.readFileSync(counter, "utf8").trim().split("\n");
        expect(producers).toHaveLength(1);
        expect(new Set(outputs)).toEqual(new Set([`from ${producers[0]}`]));
    }, 60000);
});
