/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { getDualAuthorizationHeader } from "../utils/authTokenUtils";

export class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
        super(message);
        this.name = "ApiError";
        this.status = status;
    }
}

interface ApiClientOptions {
    queryStringParameters?: Record<string, string>;
    body?: any;
    headers?: Record<string, string>;
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
        const url = new URL(path, fullBase);
        if (queryParams) {
            Object.entries(queryParams).forEach(([key, value]) => {
                if (value !== null && value !== undefined) {
                    url.searchParams.append(key, String(value));
                }
            });
        }
        return url.toString();
    }

    async get(path: string, options?: ApiClientOptions): Promise<any> {
        const url = this.buildUrl(path, options?.queryStringParameters);
        const headers = { ...(await this.getAuthHeaders()), ...options?.headers };
        const response = await fetch(url, { method: "GET", headers });
        if (!response.ok) throw new ApiError(`HTTP ${response.status}: ${response.statusText}`, response.status);
        return response.json();
    }

    async post(path: string, options?: ApiClientOptions): Promise<any> {
        const url = this.buildUrl(path, options?.queryStringParameters);
        const headers = { ...(await this.getAuthHeaders()), ...options?.headers };
        const response = await fetch(url, {
            method: "POST",
            headers,
            body: options?.body ? JSON.stringify(options.body) : undefined,
        });
        if (!response.ok) throw new ApiError(`HTTP ${response.status}: ${response.statusText}`, response.status);
        return response.json();
    }

    async put(path: string, options?: ApiClientOptions): Promise<any> {
        const url = this.buildUrl(path, options?.queryStringParameters);
        const headers = { ...(await this.getAuthHeaders()), ...options?.headers };
        const response = await fetch(url, {
            method: "PUT",
            headers,
            body: options?.body ? JSON.stringify(options.body) : undefined,
        });
        if (!response.ok) throw new ApiError(`HTTP ${response.status}: ${response.statusText}`, response.status);
        return response.json();
    }

    async del(path: string, options?: ApiClientOptions): Promise<any> {
        const url = this.buildUrl(path, options?.queryStringParameters);
        const headers = { ...(await this.getAuthHeaders()), ...options?.headers };
        const response = await fetch(url, {
            method: "DELETE",
            headers,
            body: options?.body ? JSON.stringify(options.body) : undefined,
        });
        if (!response.ok) throw new ApiError(`HTTP ${response.status}: ${response.statusText}`, response.status);
        return response.json();
    }
}

export const apiClient = new ApiClient();
