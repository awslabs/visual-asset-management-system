// eslint-disable-next-line @typescript-eslint/no-var-requires
const os = require("os");

module.exports = {
    testEnvironment: "node",
    roots: ["<rootDir>/test"],
    testMatch: ["**/*.test.ts"],
    // Resolve TypeScript sources before any locally-compiled .js build artifacts so
    // tests always exercise the current source (e.g. config.ts, not a stale config.js).
    moduleFileExtensions: ["ts", "tsx", "js", "jsx", "json", "node"],
    // Transpile only. tsconfig.json sets `isolatedModules: true`, so ts-jest compiles each file on its
    // own with `transpileModule` instead of building a type-checking language service per test file
    // (which re-checked the whole lib/ import graph in every worker). Type errors are NOT reported by
    // jest: the CI `infra-typecheck` job runs `tsc --noEmit` once over lib, bin, config and test.
    // Run `npx tsc --noEmit` locally before pushing.
    transform: {
        "^.+\\.tsx?$": "ts-jest",
    },
    // Jest never fires Node's `exit` event (jestjs/jest#10927), so a `cdk.App` built with no
    // `outdir` leaves its temporary assembly directory behind -- an empty one for a config-only
    // app, ~280 MB for a full VAMS synth. aws-cdk-lib's own hook registers the afterAll cleanup
    // in EVERY test file, so a file that has not been converted to test/support/testApp.ts is
    // still swept. test/support/harnessGuards.test.ts is what FAILS on such a file; this only
    // stops it leaking.
    setupFilesAfterEnv: ["aws-cdk-lib/testhelpers/jest-autoclean"],
    // One shared, per-run synth cache: each distinct configuration `synthTemplate()` is asked for is
    // synthesized once per `jest` invocation (each --shard is its own) instead of once per test file.
    // The directory is created here, inherited by every worker, and removed at the end of the run.
    // See test/support/synthCache.ts; VAMS_SYNTH_CACHE=off disables it, =verify checks every hit.
    globalSetup: "<rootDir>/test/support/synthCacheGlobalSetup.ts",
    globalTeardown: "<rootDir>/test/support/synthCacheGlobalTeardown.ts",
    // Worker bound, sized by MEMORY rather than by cores. A full-app synth worker sits at ~2.1 GB, and
    // 44 of these files synthesize the whole app, so Jest's default of one worker per core minus one put
    // 31 such workers (~65 GB) on a 32-core / 63 GB host: one file that takes 66 s alone took 768 s, and
    // three full runs with no source change gave 14, 0 and 1 failures. Four GB per worker, capped at
    // four: the full suite measured 695 s at 2 workers, 577 s at 4 and 822 s at 8, so past four the
    // workers contend and the run gets slower. CI is bounded separately -- the 4-vCPU runner would
    // otherwise run three, and it is where a forced worker exit was first reported.
    maxWorkers: process.env.CI
        ? 2
        : Math.min(4, Math.max(2, Math.floor(os.totalmem() / 4 / 1024 ** 3))),
    // Recycle a worker whose heap exceeds this between test files. Each file gets a fresh module
    // registry, but a worker that has synthesized several full apps keeps heap the reset does not
    // release, and a worker near V8's ceiling spends its time in parallel GC (measured: ~6 busy threads
    // per worker, no disk I/O) — which is the slow-file mechanism above and what leaves a worker
    // mid-collection when Jest asks it to exit. One GB is below a heavy file's post-run heap, so the
    // worker is renewed after every such file; light files never reach it.
    workerIdleMemoryLimit: "1GB",
};
