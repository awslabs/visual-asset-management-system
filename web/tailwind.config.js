/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: ["selector", ".awsui-dark-mode"],
    content: ["./src/features/orchestration/**/*.{ts,tsx}"],
    corePlugins: { preflight: false },
    theme: {
        extend: {
            // Semantic colors bound to the app's VAMS theme variables (src/styles/theme.css).
            // These track light/dark automatically via the .awsui-dark-mode selector, so the
            // orchestration module matches the rest of the Cloudscape app in both modes with a
            // single token (no `dark:` variant needed).
            colors: {
                // Page background + cards bind to the Cloudscape-matched orchestration surfaces so
                // the new pages share the app's exact dark/light content colors (not the bluer navy).
                surface: "var(--vams-orch-page-bg)",
                "surface-secondary": "var(--vams-bg-secondary)",
                "surface-container": "var(--vams-orch-container-bg)",
                "surface-hover": "var(--vams-bg-hover)",
                "surface-selected": "var(--vams-bg-selected)",
                "surface-input": "var(--vams-bg-input)",
                "text-primary": "var(--vams-text-primary)",
                "text-secondary": "var(--vams-text-secondary)",
                "text-heading": "var(--vams-text-heading)",
                "text-disabled": "var(--vams-text-disabled)",
                "border-default": "var(--vams-border-default)",
                "border-input": "var(--vams-border-input)",
                "border-selected": "var(--vams-border-selected)",
                "vams-success": "var(--vams-color-success)",
                "vams-error": "var(--vams-color-error)",
                "vams-warning": "var(--vams-color-warning)",
                "vams-info": "var(--vams-color-info)",
            },
        },
    },
    plugins: [],
};
