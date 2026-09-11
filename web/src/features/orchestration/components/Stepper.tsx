/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";

interface StepperStep {
    id: string;
    label: string;
}

interface StepperProps {
    steps: StepperStep[];
    current: string;
}

const Stepper: React.FC<StepperProps> = ({ steps, current }) => {
    const currentIndex = steps.findIndex((step) => step.id === current);

    return (
        <div className="flex items-center gap-2">
            {steps.map((step, index) => {
                const isCurrent = step.id === current;
                const isCompleted = index < currentIndex;

                return (
                    <React.Fragment key={step.id}>
                        <div className="flex items-center gap-2">
                            <div
                                className={`flex items-center justify-center w-8 h-8 rounded-full text-sm font-medium ${
                                    isCurrent
                                        ? "bg-blue-600 text-white"
                                        : isCompleted
                                        ? "bg-green-600 text-white"
                                        : "bg-gray-300 dark:bg-gray-700 text-text-secondary"
                                }`}
                            >
                                {isCompleted ? "✓" : index + 1}
                            </div>
                            <span
                                className={`text-sm ${
                                    isCurrent
                                        ? "font-semibold text-text-primary"
                                        : isCompleted
                                        ? "text-text-primary"
                                        : "text-text-secondary"
                                }`}
                            >
                                {step.label}
                            </span>
                        </div>
                        {index < steps.length - 1 && (
                            <div
                                className={`w-8 h-0.5 ${
                                    isCompleted ? "bg-green-600" : "bg-gray-300 dark:bg-gray-700"
                                }`}
                            />
                        )}
                    </React.Fragment>
                );
            })}
        </div>
    );
};

export default Stepper;
