module.exports = {
    testEnvironment: "node",
    roots: ["<rootDir>/test"],
    testMatch: ["**/*.test.ts"],
    // Resolve TypeScript sources before any locally-compiled .js build artifacts so
    // tests always exercise the current source (e.g. config.ts, not a stale config.js).
    moduleFileExtensions: ["ts", "tsx", "js", "jsx", "json", "node"],
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
};
