/** @type {import('tailwindcss').Config} */
module.exports = {
    darkMode: ["selector", ".awsui-dark-mode"],
    // The orchestration module plus the thin wrapper pages in src/pages that host it — those
    // wrappers carry their own Tailwind classes for their loading/error panels.
    //
    // The wrapper pages are listed individually rather than as ./src/pages/**. Tailwind's utility
    // CSS is global even though this glob is not: the glob decides which files are SCANNED, and
    // every utility it emits lands in one stylesheet loaded on every page. Scanning a Cloudscape
    // page therefore makes a plain layout class named after a utility (container, grid, flex,
    // hidden, block, fixed) silently take on Tailwind's rule there, with nothing in the
    // component's own styles to explain the result.
    content: [
        "./src/features/orchestration/**/*.{ts,tsx}",
        "./src/pages/PipelinesPage2.tsx",
        "./src/pages/PipelineBuilderPage.tsx",
        "./src/pages/TemplateListPage.tsx",
        "./src/pages/TemplateBuilderPage.tsx",
        "./src/pages/WorkflowsPage2.tsx",
        "./src/pages/WorkflowBuilderPage.tsx",
        "./src/pages/WorkflowTriggersPage.tsx",
        "./src/pages/ExecutionsPage.tsx",
        "./src/pages/ExecutionDetail.tsx",
    ],
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
                // Hover inside a shaded overlay (the record action menus), where the normal hover is
                // too close to the menu background to register.
                "surface-overlay-hover": "var(--vams-bg-overlay-hover)",
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
