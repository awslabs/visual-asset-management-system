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

const QuickView: React.FC<QuickViewProps> = ({ open, onClose, title, children }) => {
    return (
        <Drawer open={open} onOpenChange={(isOpen) => !isOpen && onClose()} title={title}>
            {children}
        </Drawer>
    );
};

export default QuickView;
