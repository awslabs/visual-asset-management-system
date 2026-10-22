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

// Width change per ArrowLeft / ArrowRight key press, in pixels
const KEYBOARD_STEP = 20;

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

    const clampWidth = useCallback(
        (width: number) => Math.max(minLeftWidth, Math.min(maxLeftWidth, width)),
        [minLeftWidth, maxLeftWidth]
    );

    // Pointer events cover mouse, pen and touch; capturing the pointer keeps the drag on the
    // splitter when the pointer moves faster than the splitter follows.
    const handlePointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.currentTarget.setPointerCapture?.(e.pointerId);
        setIsDragging(true);
    }, []);

    const handlePointerMove = useCallback(
        (e: React.PointerEvent<HTMLDivElement>) => {
            if (!isDragging || !containerRef.current) return;

            const containerRect = containerRef.current.getBoundingClientRect();
            setLeftWidth(clampWidth(e.clientX - containerRect.left));
        },
        [isDragging, clampWidth]
    );

    const handlePointerEnd = useCallback(() => {
        if (!isDragging) return;
        setIsDragging(false);
        // Notify parent of width change when dragging ends
        if (onWidthChange) {
            onWidthChange(leftWidth);
        }
    }, [isDragging, leftWidth, onWidthChange]);

    const handleKeyDown = useCallback(
        (e: React.KeyboardEvent<HTMLDivElement>) => {
            let next: number;
            switch (e.key) {
                case "ArrowLeft":
                    next = leftWidth - KEYBOARD_STEP;
                    break;
                case "ArrowRight":
                    next = leftWidth + KEYBOARD_STEP;
                    break;
                case "Home":
                    next = minLeftWidth;
                    break;
                case "End":
                    next = maxLeftWidth;
                    break;
                default:
                    return;
            }
            e.preventDefault();
            const width = clampWidth(next);
            setLeftWidth(width);
            if (onWidthChange) {
                onWidthChange(width);
            }
        },
        [leftWidth, minLeftWidth, maxLeftWidth, clampWidth, onWidthChange]
    );

    // Keep the resize cursor and suppress text selection page-wide while dragging
    useEffect(() => {
        if (isDragging) {
            document.body.style.cursor = "col-resize";
            document.body.style.userSelect = "none";
        } else {
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
        }

        return () => {
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
        };
    }, [isDragging]);

    return (
        <div ref={containerRef} className={`resizable-splitter-container ${className}`}>
            <div className="resizable-left-panel" style={{ width: leftWidth }}>
                {leftPanel}
            </div>

            <div
                ref={splitterRef}
                className={`resizable-splitter ${isDragging ? "dragging" : ""}`}
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize panels"
                aria-valuenow={leftWidth}
                aria-valuemin={minLeftWidth}
                aria-valuemax={maxLeftWidth}
                tabIndex={0}
                onKeyDown={handleKeyDown}
                onPointerDown={handlePointerDown}
                onPointerMove={handlePointerMove}
                onPointerUp={handlePointerEnd}
                onPointerCancel={handlePointerEnd}
            >
                <div className="splitter-handle" />
            </div>

            <div className="resizable-right-panel">{rightPanel}</div>
        </div>
    );
};
