/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from 'react';
import {
    ExpandableSection,
    FormField,
    Select,
} from '@cloudscape-design/components';

interface DisplayOptionsPanelProps {
    cardSize: 'small' | 'medium' | 'large';
    onCardSizeChange: (size: 'small' | 'medium' | 'large') => void;
    disabled?: boolean;
}

const DisplayOptionsPanel: React.FC<DisplayOptionsPanelProps> = ({
    cardSize,
    onCardSizeChange,
    disabled = false,
}) => {
    const cardSizeOptions = [
        { label: 'Small', value: 'small' },
        { label: 'Medium', value: 'medium' },
        { label: 'Large', value: 'large' },
    ];

    return (
        <ExpandableSection
            headerText="Display Options"
            variant="container"
            defaultExpanded={false}
        >
            {/* Grid Card Size Selector */}
            <FormField
                label="Grid Card Size"
                description="Size of cards in grid view"
            >
                <Select
                    selectedOption={
                        cardSizeOptions.find((opt) => opt.value === cardSize) ||
                        cardSizeOptions[1]
                    }
                    onChange={({ detail }) =>
                        onCardSizeChange(detail.selectedOption.value as 'small' | 'medium' | 'large')
                    }
                    options={cardSizeOptions}
                    disabled={disabled}
                />
            </FormField>
        </ExpandableSection>
    );
};

export default DisplayOptionsPanel;
