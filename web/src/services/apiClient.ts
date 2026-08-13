/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { getDualAuthorizationHeader } from "../utils/authTokenUtils";
import { ensureValidSession, logoutExpired } from "../utils/sessionManager";

export class ApiError extends Error {
    status: number;
    body: any;
    constructor(message: string, status: number, body?: any) {
        super(message);
        this.name = "ApiError";
        this.status = status;
        this.body = body;
    }
}

interface ApiClientOptions {
    queryStringParameters?: Record<string, string>;
    body?: any;
    headers?: Record<string, string>;
}

type HttpMethod = "GET" | "POST" | "PUT" | "DELETE";

/**
 * Render a structured error `message` into a readable string. Handlers usually return a plain
 * string, but validation failures may return an object of string lists (e.g.
 * {"triggerTemplateErrors": ["...", "..."]}) or a bare list. Flatten those into newline-joined
 * lines so the UI shows the actual errors instead of "[object Object]".
 */
function flattenErrorMessage(message: any): string {
    if (typeof message === "string") return message;
    if (Array.isArray(message)) return message.map((item) => String(item)).join("\n");
    if (message && typeof message === "object") {
        const lines: string[] = [];
        for (const [key, value] of Object.entries(message)) {
            if (Array.isArray(value)) {
                value.forEach((item) => lines.push(String(item)));
            } else {
                lines.push(`${key}: ${String(value)}`);
            }
        }
        if (lines.length) return lines.join("\n");
    }
    try {
        return JSON.stringify(message);
    } catch {
        return String(message);
    }
}

/**
 * Parse error response body and extract the most useful error message.
 * API errors typically return {"message": "..."} in the response body.
 */
async function parseErrorResponse(response: Response): Promise<ApiError> {
    let body: any = null;
    let errorMessage = `HTTP ${response.status}: ${response.statusText}`;

    try {
        body = await response.json();
        if (body?.message) {
            errorMessage = flattenErrorMessage(body.message);
        } else if (typeof body === "string") {
            errorMessage = body;
        }
    } catch {
        try {
            const text = await response.text();
            if (text) {
                errorMessage = text;
            }
        } catch {
            // Could not read body at all — use default HTTP status message
        }
    }

    return new ApiError(errorMessage, response.status, body);
}

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Parse a Retry-After header (delta-seconds or HTTP-date) into milliseconds, clamped to a
 * sane range. Defaults to 2s when absent/unparseable so a single 429 retries promptly.
 */
function parseRetryAfterMs(headerValue: string | null): number {
    const DEFAULT_MS = 2000;
    const MAX_MS = 30000;
    if (!headerValue) return DEFAULT_MS;
    const seconds = Number(headerValue);
    if (Number.isFinite(seconds)) {
        return Math.min(Math.max(seconds * 1000, 0), MAX_MS);
    }
    const dateMs = Date.parse(headerValue);
    if (Number.isFinite(dateMs)) {
        return Math.min(Math.max(dateMs - Date.now(), 0), MAX_MS);
    }
    return DEFAULT_MS;
}

class ApiClient {
    private getBaseUrl(): string {
        return localStorage.getItem("api_path") || "/";
    }

    private async getAuthHeaders(): Promise<Record<string, string>> {
        const header = await getDualAuthorizationHeader();
        return { Authorization: header, "Content-Type": "application/json" };
    }

    private buildUrl(path: string, queryParams?: Record<string, string>): string {
        const base = this.getBaseUrl();
        const fullBase = base.startsWith("http") ? base : window.location.origin + base;
        // Resolve every path relative to the stage-inclusive base (e.g. ".../api/").
        // A leading "/" would make new URL() treat the path as origin-absolute and drop
        // the base's stage segment, so strip it and always resolve relative to fullBase.
        const relativePath = path.replace(/^\/+/, "");
        const url = new URL(relativePath, fullBase.endsWith("/") ? fullBase : fullBase + "/");
        if (queryParams) {
            Object.entries(queryParams).forEach(([key, value]) => {
                if (value !== null && value !== undefined) {
                    url.searchParams.append(key, String(value));
                }
            });
        }
        return url.toString();
    }

    /**
     * Single request path for all verbs. On an auth-looking failure (a 401/403, or a
     * pre-request token fetch that threw) it asks the auth layer whether a valid token
     * can still be produced: dead session -> forced logout; alive session -> the failure
     * was a genuine permission denial (surface it) or a transient token issue (retry once
     * after a successful refresh). `retried` guards against loops / double-sends.
     */
    private async request(
        method: HttpMethod,
        path: string,
        options?: ApiClientOptions,
        retried = false
    ): Promise<any> {
        let headers: Record<string, string>;
        try {
            headers = { ...(await this.getAuthHeaders()), ...options?.headers };
        } catch (tokenError) {
            const alive = await ensureValidSession();
            if (!alive) {
                logoutExpired();
                throw tokenError;
            }
            if (!retried) {
                return this.request(method, path, options, true);
            }
            throw tokenError;
        }

        const url = this.buildUrl(path, options?.queryStringParameters);
        const init: RequestInit = { method, headers };
        if (method !== "GET" && options?.body !== undefined) {
            init.body = JSON.stringify(options.body);
        }

        const response = await fetch(url, init);
        if (!response.ok) {
            // 429 = WAF/API-Gateway rate limiting. This is NOT an auth failure — never touch
            // the session. Retry once after the server's Retry-After (or a short default),
            // then surface a clear, retryable error so the UI can tell the user to slow down.
            if (response.status === 429) {
                if (!retried) {
                    const retryAfterMs = parseRetryAfterMs(response.headers.get("Retry-After"));
                    await delay(retryAfterMs);
                    return this.request(method, path, options, true);
                }
                throw await parseErrorResponse(response);
            }
            if (response.status === 401 || response.status === 403) {
                const alive = await ensureValidSession();
                if (!alive) {
                    logoutExpired();
                }
                // Alive: a real Casbin permission denial — fall through and surface it.
            }
            throw await parseErrorResponse(response);
        }
        return response.json();
    }

    async get(path: string, options?: ApiClientOptions): Promise<any> {
        return this.request("GET", path, options);
    }

    async post(path: string, options?: ApiClientOptions): Promise<any> {
        return this.request("POST", path, options);
    }

    async put(path: string, options?: ApiClientOptions): Promise<any> {
        return this.request("PUT", path, options);
    }

    async del(path: string, options?: ApiClientOptions): Promise<any> {
        return this.request("DELETE", path, options);
    }
}

export const apiClient = new ApiClient();
