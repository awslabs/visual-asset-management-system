/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: ["selector", ".awsui-dark-mode"],
    content: ["./src/features/orchestration/**/*.{ts,tsx}"],
    corePlugins: { preflight: false },
    theme: { extend: {} },
    plugins: [],
};
