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
};
