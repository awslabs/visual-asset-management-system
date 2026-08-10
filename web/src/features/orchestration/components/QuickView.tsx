/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Drawer from "./Drawer";

interface QuickViewProps {
    open: boolean;
    onClose: () => void;
    title: string;
    children: React.ReactNode;
}

/**
 * Read-only detail panel. Wider than the base drawer (33.6rem vs 28rem): the values it shows are
 * execution ids, asset ids and file paths, which wrapped by a few characters at the narrower width.
 */
const QuickView: React.FC<QuickViewProps> = ({ open, onClose, title, children }) => {
    return (
        <Drawer
            open={open}
            onOpenChange={(isOpen) => !isOpen && onClose()}
            title={title}
            maxWidthClass="max-w-[33.6rem]"
        >
            {children}
        </Drawer>
    );
};

export default QuickView;
