/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * VAMS-authored helper for the robotics viewer build, copied into the Lichtblick clone by
 * web/customInstalls/robotics/roboticsViewerInstall.js. This file imports nothing, so the VAMS web
 * unit tests load it directly.
 *
 * The app is rendered with no extension loader (entrypoint.tsx), so a Lichtblick extension (.foxe)
 * can never be installed. Upstream still accepts a dropped .foxe file and reports the attempt as
 * "An error occurred during extension installation: No extension loader found for namespace
 * local". This helper takes a .foxe file out of the app's hands before its own handlers see it and
 * reports that extensions are not available instead. It changes the message only: with or without
 * it, nothing is installed.
 */

/** Shown in Settings > Extensions in place of the extension list and the marketplace. */
export const EXTENSIONS_UNAVAILABLE =
    "Extensions are not available in the VAMS Robotics Data Viewer.";

/** Shown when a .foxe file is dropped onto the viewer or chosen in the app's file input. */
export const EXTENSION_FILE_REFUSED =
    "Extensions are not available in the VAMS Robotics Data Viewer, so .foxe files cannot be installed.";

/** The test upstream's file handling (useHandleFiles) applies before installing a file. */
export function isExtensionFileName(name: string): boolean {
    return name.endsWith(".foxe");
}

/**
 * Listens on `target` in the capture phase, which runs before the app's drop listener on the
 * document and before React's change handler on the app root. A drop, or a change of a file input,
 * that carries a .foxe file is cancelled there and `notify` is called with EXTENSION_FILE_REFUSED;
 * every other drop and change is left to the app. The whole drop is refused, so a layout file
 * dropped together with a .foxe file is not imported either. Returns the function that removes the
 * listeners.
 *
 * The app's drop listener is what closes its "Drop a file here" overlay, and it never sees a
 * refused drop, so a dragleave (which the app answers by closing the overlay) is sent to the
 * document afterwards.
 *
 * Only the files of the drop itself are checked. The app reads a dropped folder's files itself, so
 * a .foxe file inside a folder still reaches its installer, which installs nothing and shows its
 * own message.
 */
export function refuseExtensionFiles(
    target: Window,
    notify: (message: string) => void
): () => void {
    const refuse = (event: Event, files: ArrayLike<File> | null | undefined): boolean => {
        if (!files || !Array.from(files).some((file) => isExtensionFileName(file.name))) {
            return false;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        notify(EXTENSION_FILE_REFUSED);
        return true;
    };
    const onDrop = (event: DragEvent): void => {
        if (refuse(event, event.dataTransfer?.files)) {
            target.document.dispatchEvent(new Event("dragleave"));
        }
    };
    const onChange = (event: Event): void => {
        const input = event.target;
        if (
            input instanceof HTMLInputElement &&
            input.type === "file" &&
            refuse(event, input.files)
        ) {
            // Choosing the same file again fires a change event only once the value is cleared.
            input.value = "";
        }
    };
    target.addEventListener("drop", onDrop, { capture: true });
    target.addEventListener("change", onChange, { capture: true });
    return () => {
        target.removeEventListener("drop", onDrop, { capture: true });
        target.removeEventListener("change", onChange, { capture: true });
    };
}
