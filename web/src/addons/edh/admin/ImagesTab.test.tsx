/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The admin Images tab on its own: where focus goes when the Delete confirmation or a register
 * dialog closes, and the line the list shows when its search or filters match no image. The rest of
 * the tab is covered through the page in EdhAdminPage.test.tsx.
 */

import React from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ImagesTab from "./ImagesTab";
import * as adminQueries from "../api/adminQueries";
import { EdhRequestError } from "../api/outcome";

jest.mock("../api/adminQueries", () => ({
    useEdhAdminImages: jest.fn(),
    useEdhAdminMutations: jest.fn(),
    useEdhCapacity: jest.fn(),
}));

const mockToast = { success: jest.fn(), warning: jest.fn(), info: jest.fn(), error: jest.fn() };
jest.mock("../../../features/orchestration/components/ToastProvider", () => ({
    ...jest.requireActual("../../../features/orchestration/components/ToastProvider"),
    useToast: () => mockToast,
}));

const query = (data: unknown) => ({
    data,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
});

const IMAGES = [
    {
        kind: "vdi",
        imageId: "7",
        stackName: "linux-cad",
        amiId: "ami-0123456789abcdef0",
        baseOs: "amazonlinux2023",
        osFamily: "linux",
        architecture: "x86_64",
        rootSize: 100,
        profileId: 1,
        description: "Linux CAD",
    },
    {
        kind: "simulation",
        imageId: "3",
        stackName: "sim-base",
        amiId: "ami-0aaaaaaaaaaaaaaaa",
        baseOs: "",
        osFamily: "linux",
        architecture: null,
        rootSize: 50,
        profileId: 4,
        description: "",
    },
];

let deleteImage: { mutateAsync: jest.Mock; isPending: boolean };
let registerSimulation: { mutateAsync: jest.Mock; isPending: boolean };

beforeEach(() => {
    jest.clearAllMocks();
    deleteImage = { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false };
    registerSimulation = { mutateAsync: jest.fn(), isPending: false };
    (adminQueries.useEdhAdminImages as jest.Mock).mockReturnValue(query(IMAGES));
    (adminQueries.useEdhCapacity as jest.Mock).mockReturnValue(query(undefined));
    (adminQueries.useEdhAdminMutations as jest.Mock).mockReturnValue({
        registerVdi: { mutateAsync: jest.fn(), isPending: false },
        registerSimulation,
        deleteImage,
    });
});

// Hidden ones included: the table is hidden from the accessibility tree while a dialog is open.
const trigger = (name: string) =>
    screen.getByRole("button", { name: `Actions for ${name}`, hidden: true });

const openDelete = async (user: ReturnType<typeof userEvent.setup>, name: string) => {
    trigger(name).focus();
    await user.keyboard("{Enter}");
    await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
    return screen.findByRole("dialog", { name: `Delete ${name}` });
};

const closed = () => waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

/**
 * Holds `mutation`'s next request in flight, reading as pending, until the returned function
 * answers it.
 */
const holdRequest = (mutation: { mutateAsync: jest.Mock; isPending: boolean }) => {
    let answer = () => undefined as void;
    mutation.mutateAsync = jest.fn(() => {
        mutation.isPending = true;
        return new Promise((resolve) => {
            answer = () => {
                mutation.isPending = false;
                resolve({});
            };
        });
    });
    return () => act(async () => answer());
};

/**
 * Presses Enter on the dialog's `label` button and checks the busy window: the button reads
 * `busyLabel` and keeps focus (aria-disabled, not disabled), and a second Enter, Escape and Cancel
 * leave the request and the dialog as they are.
 */
const pressAndHold = async (
    user: ReturnType<typeof userEvent.setup>,
    dialog: HTMLElement,
    label: string,
    busyLabel: string
) => {
    within(dialog).getByRole("button", { name: label }).focus();
    await user.keyboard("{Enter}");
    const busy = within(dialog).getByRole("button", { name: busyLabel });
    expect(busy).toHaveAttribute("aria-disabled", "true");
    expect(busy).not.toBeDisabled();
    expect(busy).toHaveFocus();
    await user.keyboard("{Enter}");
    await user.keyboard("{Escape}");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("dialog")).toBe(dialog);
};

describe("focus when the Delete confirmation closes", () => {
    it("keeps focus on Deleting... and the confirmation open while the delete is in flight", async () => {
        const answer = holdRequest(deleteImage);
        const user = userEvent.setup({ delay: null });
        render(<ImagesTab can={() => true} />);
        const dialog = await openDelete(user, "linux-cad");

        await pressAndHold(user, dialog, "Delete", "Deleting...");
        expect(deleteImage.mutateAsync).toHaveBeenCalledTimes(1);

        await answer();
        await closed();
        await waitFor(() =>
            expect(screen.getByRole("textbox", { name: "Search images" })).toHaveFocus()
        );
    });

    it.each(["Escape", "Cancel"])("returns to the row's actions after %s", async (how) => {
        const user = userEvent.setup();
        render(<ImagesTab can={() => true} />);
        const dialog = await openDelete(user, "sim-base");
        if (how === "Escape") {
            await user.keyboard("{Escape}");
        } else {
            await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
        }
        await closed();
        await waitFor(() => expect(trigger("sim-base")).toHaveFocus());
        expect(deleteImage.mutateAsync).not.toHaveBeenCalled();
    });

    it("goes to the search box once the delete removes the image's row", async () => {
        const user = userEvent.setup();
        render(<ImagesTab can={() => true} />);
        const dialog = await openDelete(user, "linux-cad");
        await user.click(within(dialog).getByRole("button", { name: "Delete" }));
        await closed();
        expect(deleteImage.mutateAsync).toHaveBeenCalledWith({ kind: "vdi", imageId: "7" });
        await waitFor(() =>
            expect(screen.getByRole("textbox", { name: "Search images" })).toHaveFocus()
        );
    });

    it("returns to the row's actions when the delete fails and the row stays", async () => {
        deleteImage.mutateAsync = jest.fn().mockRejectedValue(new Error("EDH refused"));
        const user = userEvent.setup();
        render(<ImagesTab can={() => true} />);
        const dialog = await openDelete(user, "linux-cad");
        await user.click(within(dialog).getByRole("button", { name: "Delete" }));
        await closed();
        await waitFor(() => expect(trigger("linux-cad")).toHaveFocus());
    });

    it("returns to the row's actions when opened again on an image a delete EDH did not confirm left listed", async () => {
        deleteImage.mutateAsync = jest
            .fn()
            .mockRejectedValue(new EdhRequestError("Endpoint request timed out", 504));
        const user = userEvent.setup({ delay: null });
        render(<ImagesTab can={() => true} />);
        const dialog = await openDelete(user, "linux-cad");
        await user.click(within(dialog).getByRole("button", { name: "Delete" }));
        await closed();
        await waitFor(() =>
            expect(screen.getByRole("textbox", { name: "Search images" })).toHaveFocus()
        );

        // The image is still listed; the user opens its Delete again and cancels.
        const again = await openDelete(user, "linux-cad");
        await user.click(within(again).getByRole("button", { name: "Cancel" }));
        await closed();
        await waitFor(() => expect(trigger("linux-cad")).toHaveFocus());
    });
});

describe("a list the search or filters empty", () => {
    it("says what the search matched none of, and shows the rows again once it is cleared", async () => {
        const user = userEvent.setup();
        render(<ImagesTab can={() => true} />);
        const search = screen.getByRole("textbox", { name: "Search images" });

        await user.type(search, "zz-no-such");
        expect(screen.getByRole("status")).toHaveTextContent('No images match "zz-no-such".');
        expect(screen.getByRole("table", { name: "Registered EDH images" })).toBeInTheDocument();
        expect(screen.queryByText("linux-cad")).toBeNull();

        await user.clear(search);
        expect(screen.queryByRole("status")).toBeNull();
        expect(screen.getByText("linux-cad")).toBeInTheDocument();
        expect(screen.getByText("sim-base")).toBeInTheDocument();
    });

    it("names the filters when they leave no image, and the search beside them when it does", async () => {
        const user = userEvent.setup();
        render(<ImagesTab can={() => true} />);

        // The simulation image reports no architecture, so the two filters together leave none.
        await user.selectOptions(screen.getByRole("combobox", { name: "Kind" }), "simulation");
        await user.selectOptions(screen.getByRole("combobox", { name: "Architecture" }), "x86_64");
        expect(screen.getByRole("status")).toHaveTextContent(
            "No images match the selected filters."
        );

        await user.selectOptions(screen.getByRole("combobox", { name: "Architecture" }), "");
        await user.type(screen.getByRole("textbox", { name: "Search images" }), "linux-cad");
        expect(screen.getByRole("status")).toHaveTextContent(
            'No images match "linux-cad" with the selected filters.'
        );

        await user.selectOptions(screen.getByRole("combobox", { name: "Kind" }), "");
        expect(screen.queryByRole("status")).toBeNull();
        expect(screen.getByText("linux-cad")).toBeInTheDocument();
    });
});

describe("focus when a register dialog closes", () => {
    const button = (name: string) => screen.getByRole("button", { name, hidden: true });

    const openRegister = async (user: ReturnType<typeof userEvent.setup>, name: string) => {
        button(name).focus();
        await user.keyboard("{Enter}");
        return screen.findByRole("dialog", { name });
    };

    it.each([
        ["Register VDI image", "Escape"],
        ["Register VDI image", "Cancel"],
        ["Register simulation image", "Escape"],
        ["Register simulation image", "Cancel"],
    ])("returns to %s after %s", async (name, how) => {
        const user = userEvent.setup({ delay: null });
        render(<ImagesTab can={() => true} />);
        const dialog = await openRegister(user, name);
        if (how === "Escape") {
            await user.keyboard("{Escape}");
        } else {
            await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
        }
        await closed();
        await waitFor(() => expect(button(name)).toHaveFocus());
    });

    it("goes to the search box once the image is registered", async () => {
        const user = userEvent.setup({ delay: null });
        render(<ImagesTab can={() => true} />);
        const dialog = await openRegister(user, "Register simulation image");
        const enter = async (label: string, value: string) => {
            await user.click(within(dialog).getByLabelText(label));
            await user.paste(value);
        };
        await enter("AMI ID", "ami-0bbbbbbbbbbbbbbbb");
        await enter("Stack name", "sim-gpu");
        await enter("Target node profile ID", "4");
        await user.click(within(dialog).getByRole("button", { name: "Register" }));

        await closed();
        expect(registerSimulation.mutateAsync).toHaveBeenCalledTimes(1);
        await waitFor(() =>
            expect(
                screen.getByRole("textbox", { name: "Search images", hidden: true })
            ).toHaveFocus()
        );
    });

    it("keeps focus on Registering... and the dialog open while the registration is in flight", async () => {
        const answer = holdRequest(registerSimulation);
        const user = userEvent.setup({ delay: null });
        render(<ImagesTab can={() => true} />);
        const dialog = await openRegister(user, "Register simulation image");
        const enter = async (label: string, value: string) => {
            await user.click(within(dialog).getByLabelText(label));
            await user.paste(value);
        };
        await enter("AMI ID", "ami-0bbbbbbbbbbbbbbbb");
        await enter("Stack name", "sim-gpu");
        await enter("Target node profile ID", "4");

        await pressAndHold(user, dialog, "Register", "Registering...");
        expect(registerSimulation.mutateAsync).toHaveBeenCalledTimes(1);

        await answer();
        await closed();
        await waitFor(() =>
            expect(
                screen.getByRole("textbox", { name: "Search images", hidden: true })
            ).toHaveFocus()
        );
    });
});
