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
    /** Called with a step's id when its row is clicked; when absent every row is inert. */
    onJumpTo?: (stepId: string) => void;
    /** Which steps may be jumped to. Defaults to the steps before the current one. */
    canJumpTo?: (stepId: string) => boolean;
}

/**
 * The wizard progress strip. With `onJumpTo` the reachable steps render as buttons, so a completed
 * step can be reopened without walking Back through the ones between.
 */
const Stepper: React.FC<StepperProps> = ({ steps, current, onJumpTo, canJumpTo }) => {
    const currentIndex = steps.findIndex((step) => step.id === current);

    return (
        <div className="flex items-center gap-2">
            {steps.map((step, index) => {
                const isCurrent = step.id === current;
                const isCompleted = index < currentIndex;
                const jumpable =
                    !!onJumpTo && !isCurrent && (canJumpTo ? canJumpTo(step.id) : isCompleted);
                const badge = (
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
                );
                const label = (
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
                );

                return (
                    <React.Fragment key={step.id}>
                        {jumpable ? (
                            <button
                                type="button"
                                onClick={() => onJumpTo?.(step.id)}
                                aria-label={`Go to step ${step.label}`}
                                className="flex items-center gap-2 rounded-md px-1 py-0.5 hover:bg-surface-hover focus:outline-none focus:ring-2 focus:ring-blue-500"
                            >
                                {badge}
                                {label}
                            </button>
                        ) : (
                            <div
                                className="flex items-center gap-2 px-1 py-0.5"
                                aria-current={isCurrent ? "step" : undefined}
                            >
                                {badge}
                                {label}
                            </div>
                        )}
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
