import React, { useState, useCallback, useEffect, useRef } from "react";
import "./HorizontalResizableSplitter.css";

interface HorizontalResizableSplitterProps {
    topPanel: React.ReactNode;
    bottomPanel: React.ReactNode;
    initialTopHeight?: number;
    minTopHeight?: number;
    maxTopHeight?: number;
    className?: string;
}

export const HorizontalResizableSplitter: React.FC<HorizontalResizableSplitterProps> = ({
    topPanel,
    bottomPanel,
    initialTopHeight,
    minTopHeight,
    maxTopHeight,
    className = "",
}) => {
    // Calculate default heights based on viewport
    const getViewportHeight = () => window.innerHeight;
    const defaultTopHeight = initialTopHeight || Math.max(300, getViewportHeight() - 200);

    const [topHeight, setTopHeight] = useState(defaultTopHeight);
    const [isDragging, setIsDragging] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);
    const splitterRef = useRef<HTMLDivElement>(null);

    // Update heights when viewport changes
    useEffect(() => {
        const handleResize = () => {
            const newViewportHeight = getViewportHeight();
            const newMinHeight = Math.max(200, newViewportHeight - 200);
            const newMaxHeight = newViewportHeight - 220;

            // Adjust current height if it's outside new bounds
            setTopHeight((prevHeight) => {
                if (prevHeight < newMinHeight) return newMinHeight;
                if (prevHeight > newMaxHeight) return newMaxHeight;
                return prevHeight;
            });
        };

        window.addEventListener("resize", handleResize);
        return () => window.removeEventListener("resize", handleResize);
    }, []);

    const handleMouseDown = useCallback((e: React.MouseEvent) => {
        e.preventDefault();
        setIsDragging(true);
    }, []);

    const handleMouseMove = useCallback(
        (e: MouseEvent) => {
            if (!isDragging || !containerRef.current) return;

            const containerRect = containerRef.current.getBoundingClientRect();
            const newTopHeight = e.clientY - containerRect.top;

            // Apply constraints
            const currentMinHeight = Math.max(200, getViewportHeight() - 500);
            const currentMaxHeight = getViewportHeight() - 220;
            const constrainedHeight = Math.max(
                currentMinHeight,
                Math.min(currentMaxHeight, newTopHeight)
            );

            setTopHeight(constrainedHeight);
        },
        [isDragging]
    );

    const handleMouseUp = useCallback(() => {
        setIsDragging(false);
    }, []);

    // Add global mouse event listeners when dragging
    useEffect(() => {
        if (isDragging) {
            document.addEventListener("mousemove", handleMouseMove);
            document.addEventListener("mouseup", handleMouseUp);
            document.body.style.cursor = "row-resize";
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
        <div ref={containerRef} className={`horizontal-resizable-splitter-container ${className}`}>
            <div className="horizontal-resizable-top-panel" style={{ height: topHeight }}>
                {topPanel}
            </div>

            <div
                ref={splitterRef}
                className={`horizontal-resizable-splitter ${isDragging ? "dragging" : ""}`}
                onMouseDown={handleMouseDown}
            >
                <div className="horizontal-splitter-handle" />
            </div>

            <div className="horizontal-resizable-bottom-panel">{bottomPanel}</div>
        </div>
    );
};
