/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    EdhRequestError,
    OUTCOME_UNKNOWN_MESSAGE,
    edhRequestError,
    isAlreadyInProgress,
    isOutcomeUnknown,
    mayHaveTakenEffect,
    toOutcomeTuple,
    unconfirmedNotice,
} from "./outcome";

// The bodies the add-on's handlers answer with, as `apiClient` rejects with them.
const UNCONFIRMED =
    "EDH did not confirm the request in time; it may still be in progress. Refresh before trying again.";
const UNAVAILABLE = "EDH is unavailable. Try again later.";
const IN_PROGRESS = "A start of this workstation is already in progress";

const unknown502 = () =>
    new EdhRequestError(UNCONFIRMED, 502, { message: UNCONFIRMED, outcomeUnknown: true });
const plain502 = () => new EdhRequestError(UNAVAILABLE, 502, { message: UNAVAILABLE });
const gateway504 = () =>
    new EdhRequestError("Endpoint request timed out", 504, {
        message: "Endpoint request timed out",
    });
const inProgress409 = () =>
    new EdhRequestError(IN_PROGRESS, 409, { message: IN_PROGRESS, startInProgress: true });

describe("toOutcomeTuple", () => {
    it("returns the unwrapped reply on success", async () => {
        expect(await toOutcomeTuple(async () => ({ state: "STOPPED" }))).toEqual([
            true,
            { state: "STOPPED" },
        ]);
    });

    it("keeps the status and parsed body of a rejection beside its message", async () => {
        const body = { message: UNCONFIRMED, outcomeUnknown: true };
        const [ok, err] = await toOutcomeTuple(() =>
            Promise.reject(Object.assign(new Error(UNCONFIRMED), { status: 502, body }))
        );
        expect(ok).toBe(false);
        expect(err).toBeInstanceOf(EdhRequestError);
        expect(err).toMatchObject({ message: UNCONFIRMED, status: 502, body });
    });

    it("falls back to a generic message for a rejection that carries none", async () => {
        const [, err] = await toOutcomeTuple(() => Promise.reject({}));
        expect((err as EdhRequestError).message).toBe("Request failed");
        expect((err as EdhRequestError).status).toBeUndefined();
    });
});

describe("edhRequestError", () => {
    it("drops the null body apiClient reports for a reply that was not JSON", () => {
        const err = edhRequestError({ message: "Bad Gateway", status: 502, body: null });
        expect(err.status).toBe(502);
        expect(err.body).toBeUndefined();
    });
});

describe("isOutcomeUnknown", () => {
    it("names a flagged 502 and a gateway 504", () => {
        expect(isOutcomeUnknown(unknown502())).toBe(true);
        expect(isOutcomeUnknown(gateway504())).toBe(true);
    });

    it("leaves a plain 502, a 409, other errors and no error alone", () => {
        expect(isOutcomeUnknown(plain502())).toBe(false);
        expect(isOutcomeUnknown(inProgress409())).toBe(false);
        // The flag counts only on the status the handlers answer it with.
        expect(isOutcomeUnknown(new EdhRequestError("x", 500, { outcomeUnknown: true }))).toBe(
            false
        );
        expect(isOutcomeUnknown(new Error("Forbidden"))).toBe(false);
        expect(isOutcomeUnknown(undefined)).toBe(false);
    });

    it("reads the status and body of any rejection, not only an EdhRequestError", () => {
        const rejection = Object.assign(new Error(UNCONFIRMED), {
            status: 502,
            body: { outcomeUnknown: true },
        });
        expect(isOutcomeUnknown(rejection)).toBe(true);
    });
});

describe("isAlreadyInProgress", () => {
    it("names the 409 a start answers while an earlier one may still be under way", () => {
        expect(isAlreadyInProgress(inProgress409())).toBe(true);
    });

    it("leaves a 409 without the flag, even in the same words, and the flag on another status alone", () => {
        const unflagged = new EdhRequestError(IN_PROGRESS, 409, { message: IN_PROGRESS });
        expect(isAlreadyInProgress(unflagged)).toBe(false);
        expect(
            isAlreadyInProgress(new EdhRequestError("Workstation is already running", 409))
        ).toBe(false);
        const otherStatus = new EdhRequestError(IN_PROGRESS, 400, {
            message: IN_PROGRESS,
            startInProgress: true,
        });
        expect(isAlreadyInProgress(otherStatus)).toBe(false);
    });
});

describe("mayHaveTakenEffect", () => {
    it("holds for a success and for an outcome EDH did not confirm, not for a failure", () => {
        expect(mayHaveTakenEffect(null)).toBe(true);
        expect(mayHaveTakenEffect(unknown502())).toBe(true);
        expect(mayHaveTakenEffect(gateway504())).toBe(true);
        expect(mayHaveTakenEffect(plain502())).toBe(false);
        expect(mayHaveTakenEffect(inProgress409())).toBe(false);
    });
});

describe("unconfirmedNotice", () => {
    it("titles an outcome EDH did not confirm '<action> not confirmed', with the backend's message", () => {
        expect(unconfirmedNotice("Start", unknown502())).toEqual({
            title: "Start not confirmed",
            message: UNCONFIRMED,
        });
    });

    it("words a gateway 504 as the handlers word a flagged 502: its body names only the timeout", () => {
        expect(OUTCOME_UNKNOWN_MESSAGE).toBe(UNCONFIRMED);
        expect(unconfirmedNotice("Stop", gateway504())).toEqual({
            title: "Stop not confirmed",
            message: OUTCOME_UNKNOWN_MESSAGE,
        });
    });

    it("titles the in-progress refusal '<action> already in progress'", () => {
        expect(unconfirmedNotice("Start", inProgress409())).toEqual({
            title: "Start already in progress",
            message: IN_PROGRESS,
        });
    });

    it("is null for a failure", () => {
        expect(unconfirmedNotice("Start", plain502())).toBeNull();
        expect(unconfirmedNotice("Start", new Error("Forbidden"))).toBeNull();
    });
});
