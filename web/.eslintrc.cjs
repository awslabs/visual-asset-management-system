module.exports = {
    root: true,
    env: { browser: true, es2020: true, jest: true },
    extends: [
        "eslint:recommended",
        "plugin:react/recommended",
        "plugin:react-hooks/recommended",
        "plugin:@typescript-eslint/recommended",
    ],
    parser: "@typescript-eslint/parser",
    parserOptions: {
        ecmaFeatures: { jsx: true },
        ecmaVersion: "latest",
        sourceType: "module",
    },
    plugins: ["react", "@typescript-eslint"],
    settings: { react: { version: "detect" } },
    overrides: [
        {
            // Jest mock files use CommonJS
            files: ["src/__mocks__/**/*.js"],
            env: { commonjs: true },
        },
    ],
    rules: {
        // Off — React 17+ with automatic JSX transform
        "react/react-in-jsx-scope": "off",
        "react/prop-types": "off",

        // Off — codebase uses `any` extensively and pragmatically
        "@typescript-eslint/no-explicit-any": "off",

        // Downgrade to warn — pre-existing patterns throughout codebase
        "@typescript-eslint/no-unused-vars": "warn",
        "@typescript-eslint/no-empty-function": "warn",
        "@typescript-eslint/no-empty-interface": "warn",
        "@typescript-eslint/ban-ts-comment": "warn",
        "@typescript-eslint/ban-types": "warn",
        "@typescript-eslint/no-var-requires": "warn",
        "@typescript-eslint/no-non-null-asserted-optional-chain": "warn",
        "no-prototype-builtins": "warn",
        "no-case-declarations": "warn",
        "no-empty": "warn",
        "no-empty-pattern": "warn",
        "no-useless-escape": "warn",
        "no-constant-condition": "warn",
        "no-control-regex": "warn",
        "prefer-const": "warn",
        "react/no-unescaped-entities": "warn",
        "react/no-children-prop": "warn",
        "react/jsx-key": "warn",
    },
};
