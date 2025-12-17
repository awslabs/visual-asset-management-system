/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Retry configuration for upload operations
 */
export const RETRY_CONFIG = {
    MAX_RETRIES: 3,
    RATE_LIMIT_BACKOFF_MS: 60000, // 1 minute for 429 errors
    STANDARD_BACKOFF_MS: 5000, // 5 seconds for other retryable errors
    RETRYABLE_STATUS_CODES: [400, 404, 429],
};

/**
 * Extract error message from various error formats
 */
export function extractErrorMessage(error: any): string {
    // Check for error.message (standard Error object)
    if (error.message) {
        // If message is a JSON string, try to parse it
        if (typeof error.message === "string" && error.message.includes("{")) {
            try {
                const parsed = JSON.parse(error.message);
                if (parsed.message) {
                    return parsed.message;
                }
            } catch (e) {
                // Not JSON, return as-is
            }
        }
        return error.message;
    }

    // Check for error.response.data.message (API response format)
    if (error.response?.data?.message) {
        return error.response.data.message;
    }

    // Check for direct message property
    if (error.data?.message) {
        return error.data.message;
    }

    // Check for error string
    if (typeof error === "string") {
        return error;
    }

    return "Unknown error occurred";
}

/**
 * Extract status code from error
 */
export function extractStatusCode(error: any): number | null {
    // Check various error formats
    if (error.status) return error.status;
    if (error.response?.status) return error.response.status;
    if (error.statusCode) return error.statusCode;

    // Try to extract from message
    const message = extractErrorMessage(error);
    if (message.includes("429") || message.toLowerCase().includes("rate limit")) {
        return 429;
    }
    if (message.includes("400")) return 400;
    if (message.includes("404")) return 404;
    if (message.includes("503")) return 503;

    return null;
}

/**
 * Check if error is retryable
 */
export function isRetryableError(error: any): boolean {
    const statusCode = extractStatusCode(error);
    if (statusCode === null) return false;

    return RETRY_CONFIG.RETRYABLE_STATUS_CODES.includes(statusCode);
}

/**
 * Check if error is a rate limit error (429)
 */
export function isRateLimitError(error: any): boolean {
    const statusCode = extractStatusCode(error);
    return statusCode === 429;
}

/**
 * Check if error is a 503 error
 */
export function is503Error(error: any): boolean {
    const statusCode = extractStatusCode(error);
    return statusCode === 503;
}

/**
 * Get backoff time for retry based on error type
 */
export function getBackoffTime(error: any, retryCount: number): number {
    if (isRateLimitError(error)) {
        // For rate limit errors, use 1 minute backoff
        return RETRY_CONFIG.RATE_LIMIT_BACKOFF_MS;
    }

    // For other retryable errors, use exponential backoff
    return RETRY_CONFIG.STANDARD_BACKOFF_MS * Math.pow(2, retryCount - 1);
}

/**
 * Sleep for specified milliseconds
 */
export function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Retry an async operation with backoff
 */
export async function retryWithBackoff<T>(
    operation: () => Promise<T>,
    operationName: string,
    onRetry?: (retryCount: number, error: any, backoffMs: number) => void
): Promise<T> {
    let lastError: any;

    for (let attempt = 1; attempt <= RETRY_CONFIG.MAX_RETRIES + 1; attempt++) {
        try {
            return await operation();
        } catch (error: any) {
            lastError = error;
            const statusCode = extractStatusCode(error);
            const errorMessage = extractErrorMessage(error);

            console.error(
                `${operationName} attempt ${attempt} failed:`,
                `Status: ${statusCode}, Message: ${errorMessage}`
            );

            // Check if we should retry
            if (attempt <= RETRY_CONFIG.MAX_RETRIES && isRetryableError(error)) {
                const backoffMs = getBackoffTime(error, attempt);
                const backoffSeconds = Math.round(backoffMs / 1000);

                console.log(
                    `${operationName} will retry in ${backoffSeconds} seconds (attempt ${attempt}/${RETRY_CONFIG.MAX_RETRIES})`
                );

                // Call retry callback if provided
                if (onRetry) {
                    onRetry(attempt, error, backoffMs);
                }

                // Wait before retrying
                await sleep(backoffMs);
            } else {
                // No more retries or non-retryable error
                if (attempt > RETRY_CONFIG.MAX_RETRIES) {
                    console.error(
                        `${operationName} failed after ${RETRY_CONFIG.MAX_RETRIES} retries`
                    );
                } else {
                    console.error(`${operationName} failed with non-retryable error`);
                }
                throw error;
            }
        }
    }

    // Should never reach here, but TypeScript needs it
    throw lastError;
}

/**
 * Format retry message for UI display
 */
export function formatRetryMessage(
    operationName: string,
    retryCount: number,
    error: any,
    backoffMs: number
): { message: string; isRateLimit: boolean } {
    const statusCode = extractStatusCode(error);
    const errorMessage = extractErrorMessage(error);
    const backoffSeconds = Math.round(backoffMs / 1000);

    if (isRateLimitError(error)) {
        return {
            message: `Upload processing is taking longer due to normal throttle controls. Your upload of this size requires pacing. Waiting ${backoffSeconds} seconds before continuing...`,
            isRateLimit: true,
        };
    }

    return {
        message: `${operationName} encountered an error (${statusCode}): ${errorMessage}. Retrying in ${backoffSeconds} seconds (attempt ${retryCount}/${RETRY_CONFIG.MAX_RETRIES})...`,
        isRateLimit: false,
    };
}
