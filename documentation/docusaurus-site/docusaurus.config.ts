import { themes as prismThemes } from "prism-react-renderer";
import type { Config } from "@docusaurus/types";
import type * as Preset from "@docusaurus/preset-classic";

const config: Config = {
    title: "Visual Asset Management System (VAMS)",
    tagline:
        "AWS-native solution for managing, visualizing, and processing 3D assets, point clouds, CAD files, and visual content",
    favicon: "img/favicon.ico",

    url: process.env.DOCS_URL || "https://awslabs.github.io",
    baseUrl: process.env.DOCS_BASE_URL || "/visual-asset-management-system/",

    organizationName: "awslabs",
    projectName: "visual-asset-management-system",

    onBrokenLinks: "warn",
    onBrokenMarkdownLinks: "warn",

    i18n: {
        defaultLocale: "en",
        locales: ["en"],
    },

    markdown: {
        mermaid: true,
        format: "detect",
    },

    themes: ["@docusaurus/theme-mermaid"],

    presets: [
        [
            "classic",
            {
                docs: {
                    sidebarPath: "./sidebars.ts",
                    routeBasePath: "/",
                },
                blog: false,
                theme: {
                    customCss: "./src/css/custom.css",
                },
            } satisfies Preset.Options,
        ],
    ],

    themeConfig: {
        image: "img/logo.png",
        announcementBar: {
            id: "star_us",
            content:
                '⭐ If you like VAMS, give it a star on <a target="_blank" rel="noopener noreferrer" href="https://github.com/awslabs/visual-asset-management-system">GitHub</a>! ⭐',
            backgroundColor: "#232f3e",
            textColor: "#ffffff",
            isCloseable: true,
        },
        navbar: {
            title: "",
            logo: {
                alt: "VAMS",
                src: "img/logo_dark.png",
                srcDark: "img/logo.png",
            },
            items: [
                {
                    type: "docSidebar",
                    sidebarId: "docsSidebar",
                    position: "left",
                    label: "Documentation",
                },
                {
                    href: "https://github.com/awslabs/visual-asset-management-system",
                    label: "GitHub",
                    position: "right",
                },
                {
                    href: "https://aws.amazon.com/solutions/guidance/visual-asset-management-system-on-aws/",
                    label: "AWS Solutions",
                    position: "right",
                },
            ],
        },
        footer: {
            style: "dark",
            links: [
                {
                    title: "Documentation",
                    items: [
                        { label: "Getting Started", to: "/user-guide/getting-started" },
                        { label: "Deployment Guide", to: "/deployment/deploy-the-solution" },
                        { label: "API Reference", to: "/api/overview" },
                        { label: "CLI Reference", to: "/cli/getting-started" },
                    ],
                },
                {
                    title: "Resources",
                    items: [
                        {
                            label: "GitHub Repository",
                            href: "https://github.com/awslabs/visual-asset-management-system",
                        },
                        {
                            label: "AWS Solutions Guidance",
                            href: "https://aws.amazon.com/solutions/guidance/visual-asset-management-system-on-aws/",
                        },
                        {
                            label: "AWS Spatial Blog",
                            href: "https://aws.amazon.com/blogs/spatial/",
                        },
                    ],
                },
                {
                    title: "Blog Posts",
                    items: [
                        {
                            label: "GPU-Accelerated Robotic Simulation with Isaac Lab",
                            href: "https://aws.amazon.com/blogs/spatial/gpu-accelerated-robotic-simulation-training-with-nvidia-isaac-lab-in-vams/",
                        },
                        {
                            label: "Production-Ready 3D Pipelines",
                            href: "https://aws.amazon.com/blogs/spatial/building-production-ready-3d-pipelines-with-aws-visual-asset-management-system-vams-and-4d-pipeline/",
                        },
                    ],
                },
            ],
            copyright: `Copyright ${new Date().getFullYear()} Amazon.com, Inc. or its affiliates. All Rights Reserved.`,
        },
        prism: {
            theme: prismThemes.github,
            darkTheme: prismThemes.dracula,
            additionalLanguages: ["bash", "json", "python", "typescript"],
        },
        mermaid: {
            theme: {
                light: "neutral",
                dark: "dark",
            },
        },
        colorMode: {
            defaultMode: "light",
            disableSwitch: false,
            respectPrefersColorScheme: true,
        },
        docs: {
            sidebar: {
                hideable: true,
                autoCollapseCategories: true,
            },
        },
    } satisfies Preset.ThemeConfig,
};

export default config;
