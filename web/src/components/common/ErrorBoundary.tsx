/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { Component, ErrorInfo, ReactNode } from "react";
import Alert from "@cloudscape-design/components/alert";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { isChunkLoadError, reloadPage } from "../../utils/chunkLoadRecovery";

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
            // A lazy component whose chunk failed to download re-throws the same cached rejection on
            // every render, so resetting this boundary cannot recover it; only a page reload can.
            const chunkError = isChunkLoadError(this.state.error);

            return (
                <Alert
                    type="error"
                    header={
                        chunkError
                            ? `${componentName} could not be downloaded`
                            : `${componentName} failed to load`
                    }
                    action={
                        chunkError ? (
                            <Button onClick={() => reloadPage()}>Reload</Button>
                        ) : (
                            <Button onClick={this.handleReset}>Retry</Button>
                        )
                    }
                >
                    <SpaceBetween direction="vertical" size="s">
                        <div>
                            {chunkError
                                ? "Part of the application could not be downloaded, usually because the network connection was interrupted or the application was updated. Reload the page to try again."
                                : "There was an error loading this component. Please try again or contact your administrator if the problem persists."}
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
