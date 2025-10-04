import React, { useState, useCallback, useEffect, useRef } from "react";
import "./ResizableSplitter.css";

interface ResizableSplitterProps {
    leftPanel: React.ReactNode;
    rightPanel: React.ReactNode;
    initialLeftWidth?: number;
    minLeftWidth?: number;
    maxLeftWidth?: number;
    className?: string;
    onWidthChange?: (width: number) => void; // Callback when width changes
}

export const ResizableSplitter: React.FC<ResizableSplitterProps> = ({
    leftPanel,
    rightPanel,
    initialLeftWidth = 300,
    minLeftWidth = 200,
    maxLeftWidth = 800,
    className = "",
    onWidthChange,
}) => {
    const [leftWidth, setLeftWidth] = useState(initialLeftWidth);
    const [isDragging, setIsDragging] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);
    const splitterRef = useRef<HTMLDivElement>(null);

    const handleMouseDown = useCallback((e: React.MouseEvent) => {
        e.preventDefault();
        setIsDragging(true);
    }, []);

    const handleMouseMove = useCallback(
        (e: MouseEvent) => {
            if (!isDragging || !containerRef.current) return;

            const containerRect = containerRef.current.getBoundingClientRect();
            const newLeftWidth = e.clientX - containerRect.left;

            // Apply constraints
            const constrainedWidth = Math.max(minLeftWidth, Math.min(maxLeftWidth, newLeftWidth));

            setLeftWidth(constrainedWidth);
        },
        [isDragging, minLeftWidth, maxLeftWidth]
    );

    const handleMouseUp = useCallback(() => {
        setIsDragging(false);
        // Notify parent of width change when dragging ends
        if (onWidthChange) {
            onWidthChange(leftWidth);
        }
    }, [leftWidth, onWidthChange]);

    // Add global mouse event listeners when dragging
    useEffect(() => {
        if (isDragging) {
            document.addEventListener("mousemove", handleMouseMove);
            document.addEventListener("mouseup", handleMouseUp);
            document.body.style.cursor = "col-resize";
            document.body.style.userSelect = "none";
        } else {
            document.removeEventListener("mousemove", handleMouseMove);
            document.removeEventListener("mouseup", handleMouseUp);
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
        }

        return () => {
            document.removeEventListener("mousemove", handleMouseMove);
            document.removeEventListener("mouseup", handleMouseUp);
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
        };
    }, [isDragging, handleMouseMove, handleMouseUp]);

    return (
        <div ref={containerRef} className={`resizable-splitter-container ${className}`}>
            <div className="resizable-left-panel" style={{ width: leftWidth }}>
                {leftPanel}
            </div>

            <div
                ref={splitterRef}
                className={`resizable-splitter ${isDragging ? "dragging" : ""}`}
                onMouseDown={handleMouseDown}
            >
                <div className="splitter-handle" />
            </div>

            <div className="resizable-right-panel">{rightPanel}</div>
        </div>
    );
};
