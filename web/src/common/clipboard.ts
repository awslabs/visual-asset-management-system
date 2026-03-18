/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export function copyToClipboard(text) {
    try {
        // Try to use the modern clipboard API.
        // Some browsers only allow this API in response to a user initiated event.
        return navigator.clipboard.writeText(text);
    } catch {
        // Fall back to using a textarea. Making it asynchronous to align with clipboard API
        // https://stackoverflow.com/a/30810322/898577
        return new Promise((resolve, reject) => {
            const activeElement = document.activeElement;
            const textArea = document.createElement("textarea");
            textArea.value = text;
            textArea.style.position = "fixed";
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();

            try {
                document.execCommand("copy");
                resolve();
            } catch {
                reject();
            } finally {
                document.body.removeChild(textArea);
                activeElement.focus();
            }
        });
    }
}
