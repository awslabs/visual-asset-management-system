/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * JSX typings for the Miris custom elements this viewer renders. The SDK is not a dependency of
 * the web app (it is bundled by `web/customInstalls/miris/` and imported at runtime), so its own
 * typings are not available here. These cover the attributes the viewer sets, following the SDK's
 * `@miris-inc/components/react` declarations; the viewer uses the elements only through
 * `HTMLElement` (attributes and DOM events).
 */
import type { DetailedHTMLProps, HTMLAttributes } from "react";

export type MirisSceneElement = HTMLElement;
export type MirisStreamElement = HTMLElement;
export type MirisCameraElement = HTMLElement;

type MirisHTMLAttributes<T> = DetailedHTMLProps<HTMLAttributes<T>, T>;

interface MirisSceneAttributes extends MirisHTMLAttributes<MirisSceneElement> {
    "camera-fit"?: "none" | "contain" | "cover";
    "viewer-key"?: string;
    exposure?: number | string;
}

interface MirisStreamAttributes extends MirisHTMLAttributes<MirisStreamElement> {
    uuid?: string;
    controls?: boolean;
}

interface MirisCameraAttributes extends MirisHTMLAttributes<MirisCameraElement> {
    controls?: boolean;
}

declare module "react" {
    // The JSX namespace is React's own; augmenting it is how custom elements get JSX types.
    // eslint-disable-next-line @typescript-eslint/no-namespace
    namespace JSX {
        interface IntrinsicElements {
            "miris-scene": MirisSceneAttributes;
            "miris-stream": MirisStreamAttributes;
            "miris-camera": MirisCameraAttributes;
        }
    }
}
