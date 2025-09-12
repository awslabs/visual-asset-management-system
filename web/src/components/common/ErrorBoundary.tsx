/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Component, ErrorInfo, ReactNode } from "react";
import { Alert, Button, SpaceBetween } from "@cloudscape-design/components";

interface Props {
    children: ReactNode;
    fallback?: ReactNode;
    onReset?: () => void;
    componentName?: string;
}

interface State {
    hasError: boolean;
    error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
    constructor(props: Props) {
        super(props);
        this.state = {
            hasError: false,
            error: null,
        };
    }

    static getDerivedStateFromError(error: Error): State {
        return {
            hasError: true,
            error,
        };
    }

    componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
        console.error("Error caught by ErrorBoundary:", error, errorInfo);
    }

    handleReset = (): void => {
        this.setState({ hasError: false, error: null });
        if (this.props.onReset) {
            this.props.onReset();
        }
    };

    render(): ReactNode {
        if (this.state.hasError) {
            if (this.props.fallback) {
                return this.props.fallback;
            }

            const componentName = this.props.componentName || "Component";

            return (
                <Alert
                    type="error"
                    header={`${componentName} failed to load`}
                    action={<Button onClick={this.handleReset}>Retry</Button>}
                >
                    <SpaceBetween direction="vertical" size="s">
                        <div>
                            There was an error loading this component. Please try again or contact
                            your administrator if the problem persists.
                        </div>
                        {this.state.error && (
                            <div>
                                <strong>Error details:</strong> {this.state.error.message}
                            </div>
                        )}
                    </SpaceBetween>
                </Alert>
            );
        }

        return this.props.children;
    }
}

export default ErrorBoundary;
