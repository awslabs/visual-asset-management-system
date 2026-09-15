/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Maximum lifetime of user-level (self-service) API keys, in days from the
// key's creation date. User-created keys require an expiration within this
// window, and later edits cannot extend it past creation + this many days --
// after that the user must create a new key (rotation). Mirrors
// USER_API_KEY_MAX_EXPIRATION_DAYS in backend/backend/models/apiKeys.py;
// keep the two in sync.
export const USER_API_KEY_MAX_EXPIRATION_DAYS = 365;

// Keys requested per page of an API key listing. Matches the backend's own
// default page size (API_KEY_LISTING_PAGE_SIZE in
// backend/backend/handlers/auth/apiKeyService.py), so an ordinary deployment
// answers in one page and the table's filter and sort still see every key;
// paging takes over only beyond that.
export const API_KEY_LISTING_PAGE_SIZE = 1000;
