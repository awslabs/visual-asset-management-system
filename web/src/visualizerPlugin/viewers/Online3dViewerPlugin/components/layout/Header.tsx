/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

interface HeaderProps {
    fileName?: string;
}

export const Header: React.FC<HeaderProps> = ({ fileName }) => {
    return (
        <div className="ov-header">
            <div className="ov-title">
                <div className="ov-title-left"></div>
                <div className="ov-title-right" id="ov-header-buttons">
                    {/* Header buttons will be added here */}
                </div>
            </div>
        </div>
    );
};
