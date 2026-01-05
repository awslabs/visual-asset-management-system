/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { Input, SpaceBetween } from "@cloudscape-design/components";

interface Matrix4x4InputProps {
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
    disabled?: boolean;
    invalid?: boolean;
    ariaLabel?: string;
    error?: string;
}

type Matrix4x4Values = string[][];

export const Matrix4x4Input: React.FC<Matrix4x4InputProps> = ({
    value,
    onChange,
    placeholder = "Identity Matrix",
    disabled = false,
    invalid = false,
    ariaLabel = "4x4 transformation matrix",
    error,
}) => {
    const [matrixValues, setMatrixValues] = useState<Matrix4x4Values>(() =>
        Array(4)
            .fill(null)
            .map(() => Array(4).fill(""))
    );

    // Parse the string value into matrix components
    useEffect(() => {
        if (value && value.trim() !== "") {
            try {
                const parsed = JSON.parse(value);
                if (Array.isArray(parsed) && parsed.length === 4) {
                    const newMatrix: Matrix4x4Values = [];
                    let isValid = true;

                    for (let i = 0; i < 4; i++) {
                        if (Array.isArray(parsed[i]) && parsed[i].length === 4) {
                            newMatrix[i] = parsed[i].map((val: any) => val.toString());
                        } else {
                            isValid = false;
                            break;
                        }
                    }

                    if (isValid) {
                        setMatrixValues(newMatrix);
                    }
                }
            } catch (error) {
                console.warn("Failed to parse matrix value:", error);
            }
        } else {
            setMatrixValues(
                Array(4)
                    .fill(null)
                    .map(() => Array(4).fill(""))
            );
        }
    }, [value]);

    const handleValueChange = (row: number, col: number, newValue: string) => {
        const updatedMatrix = matrixValues.map((r, i) =>
            r.map((c, j) => (i === row && j === col ? newValue : c))
        );
        setMatrixValues(updatedMatrix);

        // Check if all values are provided and valid
        const allFilled = updatedMatrix.every((row) =>
            row.every(
                (cell) => cell !== "" && !isNaN(parseFloat(cell)) && isFinite(parseFloat(cell))
            )
        );

        if (allFilled) {
            const numericMatrix = updatedMatrix.map((row) =>
                row.map((cell) => parseFloat(cell) || 0)
            );
            const jsonString = JSON.stringify(numericMatrix);
            onChange(jsonString);
        } else {
            onChange("");
        }
    };

    const isValidNumber = (val: string) => {
        return !isNaN(parseFloat(val)) && isFinite(parseFloat(val));
    };

    const setIdentityMatrix = () => {
        const identityMatrix = [
            ["1", "0", "0", "0"],
            ["0", "1", "0", "0"],
            ["0", "0", "1", "0"],
            ["0", "0", "0", "1"],
        ];
        setMatrixValues(identityMatrix);
        onChange(
            JSON.stringify([
                [1, 0, 0, 0],
                [0, 1, 0, 0],
                [0, 0, 1, 0],
                [0, 0, 0, 1],
            ])
        );
    };

    return (
        <SpaceBetween direction="vertical" size="xs">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "14px", fontWeight: "bold" }}>4x4 Matrix</span>
                <button
                    type="button"
                    onClick={setIdentityMatrix}
                    disabled={disabled}
                    style={{
                        padding: "4px 8px",
                        fontSize: "12px",
                        border: "1px solid #ccc",
                        borderRadius: "4px",
                        background: "#f5f5f5",
                        cursor: disabled ? "not-allowed" : "pointer",
                    }}
                >
                    Identity
                </button>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "4px" }}>
                {matrixValues.map((row, rowIndex) =>
                    row.map((cell, colIndex) => (
                        <Input
                            key={`${rowIndex}-${colIndex}`}
                            value={cell}
                            onChange={({ detail }) =>
                                handleValueChange(rowIndex, colIndex, detail.value)
                            }
                            placeholder="0"
                            disabled={disabled}
                            invalid={invalid && cell !== "" && !isValidNumber(cell)}
                            type="number"
                            step="any"
                            ariaLabel={`${ariaLabel} row ${rowIndex + 1} column ${colIndex + 1}`}
                        />
                    ))
                )}
            </div>
            {error && <div style={{ fontSize: "12px", color: "#d13212" }}>{error}</div>}
            <div style={{ fontSize: "11px", color: "#666", textAlign: "center" }}>
                Enter values for a 4x4 transformation matrix. Click "Identity" for identity matrix.
            </div>
        </SpaceBetween>
    );
};

export default Matrix4x4Input;
