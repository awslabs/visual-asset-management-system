/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Drawer from "./Drawer";
import type { ReturnFocusTo } from "./useReturnFocus";

interface QuickViewProps {
    open: boolean;
    onClose: () => void;
    title: string;
    children: React.ReactNode;
    /** Where focus returns when the opener cannot take it back (see `Drawer`). */
    returnFocusTo?: ReturnFocusTo;
}

/**
 * Read-only detail panel. Wider than the base drawer (33.6rem vs 28rem): the values it shows are
 * execution ids, asset ids and file paths, which wrapped by a few characters at the narrower width.
 */
const QuickView: React.FC<QuickViewProps> = ({ open, onClose, title, children, returnFocusTo }) => {
    return (
        <Drawer
            open={open}
            onOpenChange={(isOpen) => !isOpen && onClose()}
            title={title}
            maxWidthClass="max-w-[33.6rem]"
            returnFocusTo={returnFocusTo}
        >
            {children}
        </Drawer>
    );
};

export default QuickView;
