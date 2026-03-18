module.exports = {
    testEnvironment: "jsdom",
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
    transformIgnorePatterns: [
        "/node_modules/(?!(d3-.*|internmap|@cloudscape-design/)).+\\.js$",
        "node_modules/(?!axios)/",
        "node_modules/(?!(react-leaflet|@react-leaflet|d3-*|axios))",
        "!node_modules/",
    ],
    moduleNameMapper: {
        "^axios$": "axios/dist/axios.js",
        "\\.(css|scss)$": "<rootDir>/src/__mocks__/styleMock.js",
        "\\.(png|jpg|jpeg|gif|svg)$": "<rootDir>/src/__mocks__/fileMock.js",
    },
    coverageThreshold: {
        global: { branches: 6, functions: 11, lines: 11, statements: 10 },
    },
    coverageReporters: ["text"],
};
