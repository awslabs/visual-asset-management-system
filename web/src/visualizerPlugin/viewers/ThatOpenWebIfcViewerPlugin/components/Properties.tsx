/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { SelectedElement } from "../types";
import styles from "../ThatOpenWebIfcPanel.module.css";

interface PropertiesProps {
    selectedElement: SelectedElement | null;
}

/**
 * Displays IFC attributes and property sets for the currently selected element.
 * Selection is driven by the Highlighter in the main component; this is a pure
 * presentation component.
 */
const Properties: React.FC<PropertiesProps> = ({ selectedElement }) => {
    if (!selectedElement) {
        return (
            <div className={styles.emptyHint}>
                Click an element in the 3D model to inspect its IFC properties.
            </div>
        );
    }

    return (
        <div>
            <div className={styles.propHeader}>{selectedElement.name}</div>
            <div className={styles.propSubHeader}>
                {selectedElement.category} · #{selectedElement.localId}
            </div>

            {selectedElement.propertySets.length === 0 && (
                <div className={styles.hint}>No property sets for this element.</div>
            )}

            {selectedElement.propertySets.map((pset, i) => (
                <div key={i} className={styles.psetCard}>
                    <div className={styles.psetTitle}>{pset.name}</div>
                    {pset.properties.map((p, j) => (
                        <div key={j} className={styles.propRow}>
                            <span className={styles.propKey}>{p.name}</span>
                            <span className={styles.propVal}>{p.value}</span>
                        </div>
                    ))}
                </div>
            ))}
        </div>
    );
};

export default Properties;
