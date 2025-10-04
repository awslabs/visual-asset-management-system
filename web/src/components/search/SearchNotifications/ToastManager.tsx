/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { Flashbar, FlashbarProps } from "@cloudscape-design/components";
import { ToastNotification } from "../types";

interface ToastManagerProps {
    toasts: ToastNotification[];
    onDismiss: (id: string) => void;
}

const ToastManager: React.FC<ToastManagerProps> = ({ toasts, onDismiss }) => {
    // Convert our toast format to CloudScape Flashbar format
    const flashbarItems: FlashbarProps.MessageDefinition[] = toasts.map((toast) => ({
        id: toast.id,
        type: toast.type,
        header: toast.title,
        content: toast.message,
        dismissible: toast.dismissible,
        onDismiss: () => onDismiss(toast.id),
    }));

    if (flashbarItems.length === 0) {
        return null;
    }

    return (
        <div
            style={{
                position: "fixed",
                top: "20px",
                right: "20px",
                zIndex: 1000,
                maxWidth: "400px",
            }}
        >
            <Flashbar items={flashbarItems} />
        </div>
    );
};

export default ToastManager;
