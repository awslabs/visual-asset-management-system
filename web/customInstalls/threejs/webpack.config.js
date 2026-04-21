const path = require("path");
const CopyPlugin = require("copy-webpack-plugin");
const fs = require("fs");

// Check if OCCT is installed
const occtInstalled = fs.existsSync(path.resolve(__dirname, "node_modules/occt-import-js"));

const plugins = [];

// If OCCT is installed, copy WASM files
if (occtInstalled) {
    console.log("OCCT detected - will copy WASM files to bundle");
    plugins.push(
        new CopyPlugin({
            patterns: [
                {
                    from: "node_modules/occt-import-js/dist/*.wasm",
                    to: "[name][ext]",
                    noErrorOnMissing: true,
                },
            ],
        })
    );
} else {
    console.log("OCCT not installed - CAD format support will be unavailable");
}

module.exports = {
    mode: "production",
    entry: "./threejsInstall.js",
    output: {
        path: path.resolve(__dirname, "dist"),
        filename: "threejs.min.js",
        library: {
            name: "THREEBundle",
            type: "umd",
            export: "default",
        },
        globalObject: "this",
    },
    resolve: {
        extensions: [".js", ".json"],
        fallback: {
            // OCCT requires these Node.js modules, but they're not needed in browser
            fs: false,
            path: false,
            crypto: false,
        },
    },
    plugins: plugins,
    optimization: {
        minimize: true,
    },
    performance: {
        hints: false,
        maxEntrypointSize: 512000,
        maxAssetSize: 512000,
    },
};
