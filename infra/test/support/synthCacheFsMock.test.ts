/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A test file that mocks `fs` must never share synth results (`templateSynth.ts`, `fsIsMocked()`).
 *
 * Such a file usually mocks `fs` to serve a substitute for a file the synth reads — the CSP additions
 * file in `albCspSizeGuard.test.ts` — while the configuration, and so the shared cache key, stays the
 * same. In its own file because `jest.mock` applies to the whole file; `synthCache.test.ts` is the
 * unmocked control.
 */

import { fsIsMocked } from "./templateSynth";

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

test("a file that mocks one fs function is detected", () => {
    expect(fsIsMocked()).toBe(true);
});
