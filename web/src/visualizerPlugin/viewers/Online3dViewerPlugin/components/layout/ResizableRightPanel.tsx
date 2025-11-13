/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useRef, useEffect } from "react";
import { RightPanel } from "./RightPanel";

export const ResizableRightPanel: React.FC = () => {
    const [width, setWidth] = useState(280);
    const isResizing = useRef(false);
    const panelRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const handleMouseMove = (e: MouseEvent) => {
            if (!isResizing.current) return;

            // Calculate new width from right edge (subtract button bar width of 40px)
            const newWidth = window.innerWidth - e.clientX - 40;
            if (newWidth >= 200 && newWidth <= 500) {
                setWidth(newWidth);
                // Trigger window resize event so viewer can adjust
                window.dispatchEvent(new Event("resize"));
            }
        };

        const handleMouseUp = () => {
            isResizing.current = false;
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
            // Trigger final resize event
            window.dispatchEvent(new Event("resize"));
        };

        document.addEventListener("mousemove", handleMouseMove);
        document.addEventListener("mouseup", handleMouseUp);

        return () => {
            document.removeEventListener("mousemove", handleMouseMove);
            document.removeEventListener("mouseup", handleMouseUp);
        };
    }, []);

    const handleMouseDown = (e: React.MouseEvent) => {
        isResizing.current = true;
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";
        e.preventDefault();
    };

    return (
        <>
            <div
                className="resize-handle-vertical"
                onMouseDown={handleMouseDown}
                style={{ flexShrink: 0 }}
            />
            <div
                ref={panelRef}
                style={{ width: `${width + 40}px`, flexShrink: 0, display: "flex" }}
            >
                <RightPanel contentWidth={width} />
            </div>
        </>
    );
};
