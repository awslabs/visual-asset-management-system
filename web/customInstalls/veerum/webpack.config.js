/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const path = require("path");
const TerserPlugin = require("terser-webpack-plugin");
const CopyWebpackPlugin = require("copy-webpack-plugin");

module.exports = {
    mode: "production",
    entry: "./node_modules/@veerum/viewer/dist/lib/index.js",
    output: {
        path: path.resolve(__dirname, "dist"),
        filename: "veerum-viewer.bundle.js",
        library: {
            name: "VeerumViewerModule",
            type: "umd",
            export: undefined,
        },
        globalObject: "this",
        clean: true,
    },
    // Externalize ONLY React and ReactDOM to use the host application's versions
    // All other dependencies (three, lodash, etc.) will be bundled
    externals: {
        react: "React",
        "react-dom": "ReactDOM",
        "react-dom/client": {
            commonjs: "react-dom/client",
            commonjs2: "react-dom/client",
            amd: "react-dom/client",
            root: ["ReactDOM", "client"],
        },
        "react/jsx-runtime": {
            commonjs: "react/jsx-runtime",
            commonjs2: "react/jsx-runtime",
            amd: "react/jsx-runtime",
            root: ["React", "jsx-runtime"],
        },
    },
    module: {
        rules: [
            {
                test: /\.js$/,
                exclude: /node_modules\/(?!(@veerum))/,
                use: {
                    loader: "babel-loader",
                    options: {
                        presets: [
                            [
                                "@babel/preset-env",
                                {
                                    targets: "> 0.25%, not dead",
                                    modules: false,
                                },
                            ],
                            "@babel/preset-react",
                        ],
                    },
                },
            },
        ],
    },
    plugins: [
        new CopyWebpackPlugin({
            patterns: [
                {
                    from: "node_modules/@veerum/viewer/dist/lib/assets",
                    to: "assets",
                    noErrorOnMissing: true,
                },
                {
                    from: "node_modules/@veerum/viewer/dist/lib/textures",
                    to: "textures",
                    noErrorOnMissing: true,
                },
            ],
        }),
    ],
    optimization: {
        minimize: true,
        minimizer: [
            new TerserPlugin({
                terserOptions: {
                    compress: {
                        drop_console: false,
                    },
                    format: {
                        comments: false,
                    },
                },
                extractComments: false,
            }),
        ],
    },
    resolve: {
        extensions: [".js", ".jsx", ".json"],
        fallback: {
            path: false,
            fs: false,
        },
    },
    performance: {
        hints: false,
        maxEntrypointSize: 2048000,
        maxAssetSize: 2048000,
    },
    devtool: "source-map",
};
