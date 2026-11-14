/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A per-run, on-disk cache shared by every Jest worker, so each distinct configuration the suite
 * synthesizes is synthesized ONCE per `jest` invocation rather than once per test file.
 *
 * Why this exists. Jest gives every test file a fresh module registry, so the in-memory cache in
 * `templateSynth.ts` only deduplicates synths WITHIN a file. Dozens of files synthesize the same shipped
 * template, and a full-app synth costs 50-76 s on a CI runner, so most of the infra suite's wall time
 * was the same few templates being rebuilt in every file.
 *
 * ## Lifetime
 *
 * `synthCacheGlobalSetup.ts` (Jest `globalSetup`) creates a fresh directory under the OS temp dir for
 * each `jest` run and publishes it to the workers through `VAMS_SYNTH_CACHE_DIR`;
 * `synthCacheGlobalTeardown.ts` removes it. Nothing survives a run, so a source edit can never be
 * answered by a template synthesized from the previous source. Each `--shard` is its own `jest`
 * invocation, so each shard has its own directory and synthesizes only what its own files ask for.
 * With no `VAMS_SYNTH_CACHE_DIR` (a runner that bypasses `jest.config.js`) the cache is simply off.
 *
 * ## Concurrency
 *
 * Lazy, with a cross-process lock: the first worker to need a key creates `<key>.lock` (`mkdir` is
 * atomic), synthesizes, writes `<key>.json.<pid>.tmp` and renames it into place (`rename` is atomic on
 * one filesystem). A worker that finds the lock taken waits for the file. A lock whose holder process
 * has died is taken over, and a holder whose synth throws releases the lock without writing anything,
 * so every waiter then synthesizes for itself and sees the same error.
 *
 * ## Switches
 *
 * - `VAMS_SYNTH_CACHE=off` disables the shared cache (every file synthesizes for itself, as before).
 * - `VAMS_SYNTH_CACHE=verify` synthesizes fresh on every cache hit as well and FAILS the test if the
 *   fresh result differs from the cached one. Run the full suite this way after changing anything a
 *   synth reads that the cache key does not cover (see `templateSynth.ts`).
 */

import * as crypto from "crypto";
import * as fs from "fs";
import * as path from "path";

/** Bump when the on-disk payload shape changes. */
const PAYLOAD_VERSION = 1;

/** Environment variable `synthCacheGlobalSetup.ts` publishes the per-run directory through. */
export const SYNTH_CACHE_DIR_ENV = "VAMS_SYNTH_CACHE_DIR";
/** `off` or `verify`; anything else (or unset) is the normal shared-cache mode. */
export const SYNTH_CACHE_MODE_ENV = "VAMS_SYNTH_CACHE";
/** Prefix of every per-run directory, so a run can sweep directories a killed run left behind. */
export const SYNTH_CACHE_DIR_PREFIX = "vams-synth-cache-";

export type SynthCacheMode = "off" | "shared" | "verify";

export function synthCacheMode(): SynthCacheMode {
    const mode = (process.env[SYNTH_CACHE_MODE_ENV] ?? "").trim().toLowerCase();
    if (mode === "off" || !process.env[SYNTH_CACHE_DIR_ENV]) return "off";
    return mode === "verify" ? "verify" : "shared";
}

/**
 * JSON with object keys sorted at every level, so equal configurations hash equally whatever order a
 * `mutate` added their keys in.
 *
 * Returns `undefined` for anything that is not plain JSON data — a function, a class instance (a CDK
 * `Duration`, a `RegExp`), a symbol, a bigint. JSON.stringify would drop or flatten those, and two
 * configurations that differ only there would share a key; the caller treats `undefined` as "do not
 * share this synth".
 */
export function canonicalJson(value: unknown): string | undefined {
    try {
        return JSON.stringify(canonicalize(value));
    } catch {
        return undefined;
    }
}

function canonicalize(value: unknown): unknown {
    if (value === null || typeof value === "string" || typeof value === "boolean") return value;
    if (typeof value === "number") {
        if (!Number.isFinite(value)) throw new Error("non-finite number");
        return value;
    }
    if (value === undefined) return undefined;
    if (Array.isArray(value)) return value.map((v) => (v === undefined ? null : canonicalize(v)));
    if (typeof value === "object") {
        const proto = Object.getPrototypeOf(value);
        if (proto !== Object.prototype && proto !== null) throw new Error("not a plain object");
        const out: Record<string, unknown> = {};
        for (const key of Object.keys(value as object).sort()) {
            const v = (value as Record<string, unknown>)[key];
            if (v === undefined) continue;
            out[key] = canonicalize(v);
        }
        return out;
    }
    throw new Error(`unsupported ${typeof value}`);
}

/** Cache key for a canonical description of everything a synth's output depends on. */
export function synthCacheKey(canonical: string): string {
    return crypto
        .createHash("sha256")
        .update(`v${PAYLOAD_VERSION}\n${canonical}`)
        .digest("hex")
        .slice(0, 32);
}

const SLEEP_CELL = new Int32Array(new SharedArrayBuffer(4));

function sleepSync(ms: number): void {
    Atomics.wait(SLEEP_CELL, 0, 0, ms);
}

function processIsAlive(pid: number): boolean {
    try {
        process.kill(pid, 0);
        return true;
    } catch (e: any) {
        // EPERM: the process exists but belongs to someone else.
        return e?.code === "EPERM";
    }
}

/** True only when the lock names an owner and that owner has exited. */
function lockHolderIsDead(lockDir: string): boolean {
    let pid: number;
    try {
        pid = Number(fs.readFileSync(path.join(lockDir, "owner"), "utf8"));
    } catch {
        // No owner file yet: the holder is between mkdir and write. Treat it as alive.
        return false;
    }
    return Number.isInteger(pid) && pid > 0 && !processIsAlive(pid);
}

/** Where two texts first differ: a JSON path when both parse, else a character offset. */
export function firstDifference(a: string, b: string): string {
    let x: unknown;
    let y: unknown;
    try {
        x = JSON.parse(a);
        y = JSON.parse(b);
    } catch {
        let i = 0;
        while (i < a.length && a[i] === b[i]) i++;
        return `character ${i}: ${JSON.stringify(a.slice(i, i + 80))} vs ${JSON.stringify(
            b.slice(i, i + 80)
        )}`;
    }
    const walk = (u: unknown, v: unknown, at: string): string | undefined => {
        if (u !== null && v !== null && typeof u === "object" && typeof v === "object") {
            const keys = Array.from(
                new Set([...Object.keys(u as object), ...Object.keys(v as object)])
            );
            for (const k of keys) {
                const found = walk((u as any)[k], (v as any)[k], `${at}/${k}`);
                if (found) return found;
            }
            return undefined;
        }
        if (JSON.stringify(u) === JSON.stringify(v)) return undefined;
        return `${at || "/"}: ${String(JSON.stringify(u)).slice(0, 200)} vs ${String(
            JSON.stringify(v)
        ).slice(0, 200)}`;
    };
    return walk(x, y, "") ?? "key order only";
}

/**
 * Return the payload cached under `key`, producing it with `produce` if no worker has yet.
 *
 * `serialize` must return JSON text and `deserialize` must rebuild an equivalent value from it. The
 * producer's own in-memory value is returned to the producer; every other worker gets a fresh parse.
 * In `verify` mode a hit is checked against a fresh `produce()` and a mismatch throws; `comparable`
 * maps both texts to the form compared, for a payload with values no two productions share.
 */
export function sharedSynth<T>(
    key: string,
    produce: () => T,
    serialize: (value: T) => string,
    deserialize: (text: string) => T,
    comparable: (text: string) => string = (text) => text
): T {
    const mode = synthCacheMode();
    if (mode === "off") return produce();

    const dir = process.env[SYNTH_CACHE_DIR_ENV] as string;
    fs.mkdirSync(dir, { recursive: true });
    const finalFile = path.join(dir, `${key}.json`);
    const lockDir = path.join(dir, `${key}.lock`);

    const hit = (): T => {
        const text = fs.readFileSync(finalFile, "utf8");
        if (mode === "verify") {
            const fresh = comparable(serialize(produce()));
            const cached = comparable(text);
            if (fresh !== cached) {
                throw new Error(
                    `synth cache verify: a fresh synth for key ${key} differs from the cached one ` +
                        `at ${firstDifference(
                            cached,
                            fresh
                        )}. Something the synth reads is not in ` +
                        `the cache key; pass sharedCache: false for this call, or add it to the key.`
                );
            }
        }
        return deserialize(text);
    };

    for (;;) {
        if (fs.existsSync(finalFile)) return hit();
        try {
            fs.mkdirSync(lockDir);
        } catch (e: any) {
            if (e?.code !== "EEXIST") throw e;
            if (lockHolderIsDead(lockDir)) {
                fs.rmSync(lockDir, { recursive: true, force: true });
                continue;
            }
            sleepSync(200);
            continue;
        }
        try {
            fs.writeFileSync(path.join(lockDir, "owner"), String(process.pid));
            // Another holder may have finished between the existence check and the mkdir.
            if (fs.existsSync(finalFile)) return hit();
            const value = produce();
            const tmp = `${finalFile}.${process.pid}.tmp`;
            fs.writeFileSync(tmp, serialize(value));
            fs.renameSync(tmp, finalFile);
            return value;
        } finally {
            fs.rmSync(lockDir, { recursive: true, force: true });
        }
    }
}
