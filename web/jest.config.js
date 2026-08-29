module.exports = {
    testEnvironment: "jsdom",
    setupFilesAfterEnv: ["<rootDir>/src/setupTests.ts"],
    // Playwright specs under e2e/ use @playwright/test, not Jest — keep them out of the unit run.
    testPathIgnorePatterns: ["/node_modules/", "<rootDir>/e2e/"],
    collectCoverageFrom: [
        "src/**/*.{js,jsx,ts,tsx}",
        "!<rootDir>/node_modules/",
        "!<rootDir>/path/to/dir/",
    ],
    transform: {
        "node_modules/@cloudscape-design/.+\\.js$":
            "./node_modules/@cloudscape-design/jest-preset/js-transformer",
        "node_modules/@cloudscape-design/.+\\.css":
            "./node_modules/@cloudscape-design/jest-preset/css-transformer",
        "node_modules/(d3-.*|internmap)/.+\\.js$":
            "./node_modules/@cloudscape-design/jest-preset/js-transformer",
        "^.+\\.(js|jsx|ts|tsx)$": "babel-jest",
    },
    // A file matching ANY ignore pattern is excluded from transformation, so a
    // single pattern must exempt every ESM package that needs transforming.
    transformIgnorePatterns: [
        "/node_modules/(?!(@cloudscape-design|d3-[^/]+|internmap|react-leaflet|@react-leaflet|axios)/)",
    ],
    moduleNameMapper: {
        // axios ships ESM by default, which jest cannot load, so it is mapped to a CommonJS build.
        // The target must be a subpath axios's `exports` map actually exposes: jest's resolver honours
        // `exports`, and `dist/axios.js` is NOT listed there (axios 1.x publishes
        // `./dist/browser/axios.cjs` and `./dist/node/axios.cjs`). Pointing at the unexposed path failed
        // resolution even though the file exists on disk, and the symptom is a SUITE-LOAD error —
        // "Could not locate module axios mapped as: axios/dist/axios.js" — so no component importing
        // axios could be unit-tested at all. The browser build is the right one for the jsdom
        // environment. Re-check this mapping when axios is upgraded; the exports map is what governs it.
        "^axios$": "axios/dist/browser/axios.cjs",
        "\\.(css|scss)$": "<rootDir>/src/__mocks__/styleMock.js",
        "\\.(png|jpg|jpeg|gif|svg)$": "<rootDir>/src/__mocks__/fileMock.js",
        // Monaco: the real `monaco-editor` package is a UMD/AMD bundle (calls `define`) and its
        // `?worker` imports are Vite-only — both break under jest. Stub them; component tests mock
        // @monaco-editor/react separately, so the editor never actually renders in unit tests.
        "\\?worker$": "<rootDir>/src/__mocks__/emptyModule.js",
        "^monaco-editor$": "<rootDir>/src/__mocks__/emptyModule.js",
    },
    // Thresholds reflect the current sparse test coverage (~2% of a large
    // codebase). Raise them as coverage grows.
    coverageThreshold: {
        global: { branches: 0.5, functions: 1, lines: 1, statements: 1 },
    },
    coverageReporters: ["text"],
};
