const path = require("path");

module.exports = {
    mode: "production",
    entry: "./src/index.js",
    output: {
        path: path.resolve(__dirname, "dist"),
        filename: "babylonjs.bundle.js",
        library: "BABYLON",
        libraryTarget: "umd",
        globalObject: "this",
    },
    resolve: {
        extensions: [".js", ".ts"],
    },
    optimization: {
        minimize: true,
    },
};
