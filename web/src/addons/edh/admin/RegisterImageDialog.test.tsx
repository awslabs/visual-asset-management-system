/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RegisterImageDialog, {
    BASE_OS_OPTIONS,
    LAUNCH_TENANCY_OPTIONS,
    validateRegisterImage,
} from "./RegisterImageDialog";
import type { EdhDesktopProfile, EdhImageKind } from "../api/admin";

const renderDialog = (
    kind: EdhImageKind,
    onSubmit: jest.Mock = jest.fn().mockResolvedValue(undefined),
    profiles?: EdhDesktopProfile[]
) => {
    render(
        <RegisterImageDialog
            kind={kind}
            open
            busy={false}
            onOpenChange={() => undefined}
            onSubmit={onSubmit}
            profiles={profiles}
        />
    );
    return onSubmit;
};

/** The message the field names through aria-describedby, or null when it names none. */
const describedError = (field: HTMLElement) => {
    const ids = (field.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean);
    const messages = ids
        .map((id) => document.getElementById(id))
        .filter(
            (node): node is HTMLElement => !!node && node.classList.contains("text-vams-error")
        );
    return messages.length === 0 ? null : messages.map((node) => node.textContent).join(" ");
};

describe("RegisterImageDialog", () => {
    it("submits the VDI fields as a JSON body and carries no thumbnail", async () => {
        const onSubmit = renderDialog("vdi");
        expect(screen.queryByLabelText("User data template ID")).not.toBeInTheDocument();
        const user = userEvent.setup();
        await user.type(screen.getByLabelText("AMI ID"), "ami-0123456789abcdef0");
        await user.type(screen.getByLabelText("Stack name"), "linux-cad");
        await user.selectOptions(screen.getByLabelText("Base OS"), "amazonlinux2023");
        await user.clear(screen.getByLabelText("Root size (GB)"));
        await user.type(screen.getByLabelText("Root size (GB)"), "120");
        await user.type(screen.getByLabelText("Virtual desktop profile ID"), "1");
        await user.click(screen.getByRole("button", { name: "Register" }));

        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
        expect(onSubmit).toHaveBeenCalledWith({
            amiId: "ami-0123456789abcdef0",
            stackName: "linux-cad",
            baseOs: "amazonlinux2023",
            rootSize: 120,
            virtualDesktopProfileId: 1,
        });
        expect(Object.keys(onSubmit.mock.calls[0][0])).not.toContain("thumbnail");
        expect(screen.queryByLabelText(/thumbnail/i)).not.toBeInTheDocument();
        expect(document.querySelector('input[type="file"]')).toBeNull();
    });

    it("places Cancel and Register in the dialog footer, outside the form body", () => {
        renderDialog("vdi");
        const dialog = screen.getByRole("dialog");
        const register = within(dialog).getByRole("button", { name: "Register" });
        const form = dialog.querySelector("form")!;
        expect(form.contains(register)).toBe(false);
        expect(register).toHaveAttribute("type", "submit");
        expect(register).toHaveAttribute("form", form.id);
        expect(form.contains(within(dialog).getByRole("button", { name: "Cancel" }))).toBe(false);
    });

    it("offers the base OS keys EDH accepts, shown by their names", () => {
        renderDialog("vdi");
        const select = screen.getByLabelText("Base OS") as HTMLSelectElement;
        const values = Array.from(select.options).map((option) => option.value);
        expect(values).toEqual(["", ...BASE_OS_OPTIONS.map((option) => option.value)]);
        expect(values).toEqual(expect.arrayContaining(["amazonlinux2023", "windows2022"]));
        expect(within(select).getByRole("option", { name: "Amazon Linux 2023" })).toHaveValue(
            "amazonlinux2023"
        );
        // Free text is no longer accepted.
        expect(select.tagName).toBe("SELECT");
    });

    it("offers the launch tenancies EDH accepts without a host id and sends the chosen one", async () => {
        const onSubmit = renderDialog("vdi");
        const select = screen.getByLabelText("Launch tenancy") as HTMLSelectElement;
        expect(Array.from(select.options).map((option) => option.value)).toEqual(
            LAUNCH_TENANCY_OPTIONS.map((option) => option.value)
        );
        expect(Array.from(select.options).map((option) => option.value)).not.toContain("host");
        const user = userEvent.setup();
        await user.type(screen.getByLabelText("AMI ID"), "ami-0123456789abcdef0");
        await user.type(screen.getByLabelText("Stack name"), "linux-cad");
        await user.selectOptions(screen.getByLabelText("Base OS"), "windows2022");
        await user.type(screen.getByLabelText("Virtual desktop profile ID"), "2");
        await user.selectOptions(select, "dedicated");
        await user.click(screen.getByRole("button", { name: "Register" }));

        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
        expect(onSubmit.mock.calls[0][0]).toMatchObject({
            baseOs: "windows2022",
            virtualDesktopProfileId: 2,
            launchTenancy: "dedicated",
        });
    });

    it("offers the capacity profiles as a picker and sends the chosen id", async () => {
        const onSubmit = renderDialog("vdi", undefined, [
            { id: 1, profile_name: "default" },
            { id: 4, profile_name: "gpu" },
        ]);
        expect(screen.queryByLabelText("Virtual desktop profile ID")).not.toBeInTheDocument();
        const picker = screen.getByLabelText("Virtual desktop profile") as HTMLSelectElement;
        expect(within(picker).getByRole("option", { name: "default (id 1)" })).toHaveValue("1");
        expect(within(picker).getByRole("option", { name: "gpu (id 4)" })).toHaveValue("4");

        const user = userEvent.setup();
        await user.type(screen.getByLabelText("AMI ID"), "ami-0123456789abcdef0");
        await user.type(screen.getByLabelText("Stack name"), "linux-gpu");
        await user.selectOptions(screen.getByLabelText("Base OS"), "ubuntu2204");
        // Nothing chosen yet: the picker, and only the picker, is flagged.
        await user.click(screen.getByRole("button", { name: "Register" }));
        expect(onSubmit).not.toHaveBeenCalled();
        expect(picker).toHaveAttribute("aria-invalid", "true");
        expect(describedError(picker)).toBe("Choose a profile.");
        expect(screen.getByLabelText("AMI ID")).toHaveAttribute("aria-invalid", "false");

        await user.selectOptions(picker, "4");
        await user.click(screen.getByRole("button", { name: "Register" }));
        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
        expect(onSubmit.mock.calls[0][0]).toMatchObject({ virtualDesktopProfileId: 4 });
    });

    it("does not submit a profile id typed before the capacity answer replaced the field with the picker", async () => {
        const onSubmit = jest.fn().mockResolvedValue(undefined);
        const dialog = (profiles?: EdhDesktopProfile[]) => (
            <RegisterImageDialog
                kind="vdi"
                open
                busy={false}
                onOpenChange={() => undefined}
                onSubmit={onSubmit}
                profiles={profiles}
            />
        );
        const { rerender } = render(dialog());
        const user = userEvent.setup();
        await user.type(screen.getByLabelText("AMI ID"), "ami-0123456789abcdef0");
        await user.type(screen.getByLabelText("Stack name"), "linux-gpu");
        await user.selectOptions(screen.getByLabelText("Base OS"), "ubuntu2204");
        await user.type(screen.getByLabelText("Virtual desktop profile ID"), "7");

        rerender(dialog([{ id: 1, profile_name: "default" }]));
        const picker = screen.getByLabelText("Virtual desktop profile") as HTMLSelectElement;
        await user.click(screen.getByRole("button", { name: "Register" }));
        expect(onSubmit).not.toHaveBeenCalled();
        expect(picker).toHaveAttribute("aria-invalid", "true");
        expect(describedError(picker)).toBe("Choose a profile.");
    });

    it("the simulation form carries the target-node fields, a variables format hint and no base OS", async () => {
        const onSubmit = renderDialog("simulation");
        expect(screen.queryByLabelText("Base OS")).not.toBeInTheDocument();
        expect(screen.queryByLabelText("Launch tenancy")).not.toBeInTheDocument();
        const variables = screen.getByLabelText(/User data variables/);
        expect(variables).toHaveAccessibleDescription(/key=value pairs separated by commas/);
        expect(variables).toHaveAttribute("placeholder", "env=prod,team=sim");
        const user = userEvent.setup();
        await user.type(screen.getByLabelText("AMI ID"), "ami-0aaaaaaaaaaaaaaaa");
        await user.type(screen.getByLabelText("Stack name"), "sim-base");
        await user.clear(screen.getByLabelText("Root size (GB)"));
        await user.type(screen.getByLabelText("Root size (GB)"), "50");
        await user.type(screen.getByLabelText("Target node profile ID"), "4");
        await user.type(variables, "FOO=bar");
        expect(screen.getByLabelText("User data template ID")).toHaveValue(1);
        await user.click(screen.getByRole("button", { name: "Register" }));

        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
        expect(onSubmit).toHaveBeenCalledWith({
            amiId: "ami-0aaaaaaaaaaaaaaaa",
            stackName: "sim-base",
            rootSize: 50,
            targetNodeProfileId: 4,
            targetNodeUserDataId: 1,
            userDataVariables: "FOO=bar",
        });
    });

    it("shows no validation message before the first Register attempt", () => {
        renderDialog("vdi");
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        expect(document.querySelectorAll('[aria-invalid="true"]')).toHaveLength(0);
        expect(screen.getByLabelText("AMI ID")).toHaveAttribute("aria-invalid", "false");
    });

    it("flags a bad AMI id under its own field and leaves the filled fields alone", async () => {
        const onSubmit = renderDialog("vdi", jest.fn());
        const user = userEvent.setup();
        await user.type(screen.getByLabelText("AMI ID"), "ami-0abc");
        await user.type(screen.getByLabelText("Stack name"), "x");
        await user.selectOptions(screen.getByLabelText("Base OS"), "amazonlinux2023");
        await user.type(screen.getByLabelText("Virtual desktop profile ID"), "1");
        await user.click(screen.getByRole("button", { name: "Register" }));

        expect(onSubmit).not.toHaveBeenCalled();
        const ami = screen.getByLabelText("AMI ID");
        expect(ami).toHaveAttribute("aria-invalid", "true");
        expect(describedError(ami)).toMatch(
            /^Must look like ami- followed by 8 to 17 hex characters/
        );
        for (const label of [
            "Stack name",
            "Base OS",
            "Root size (GB)",
            "Virtual desktop profile ID",
        ]) {
            expect(screen.getByLabelText(label)).toHaveAttribute("aria-invalid", "false");
            expect(describedError(screen.getByLabelText(label))).toBeNull();
        }
        expect(screen.getByRole("alert")).toHaveTextContent("Correct the highlighted field below.");
    });

    it("names every failing field on an empty submit, in the wording under each", async () => {
        const onSubmit = renderDialog("simulation", jest.fn());
        const user = userEvent.setup();
        await user.clear(screen.getByLabelText("Root size (GB)"));
        await user.type(screen.getByLabelText("Root size (GB)"), "0");
        await user.clear(screen.getByLabelText("User data template ID"));
        await user.click(screen.getByRole("button", { name: "Register" }));

        expect(onSubmit).not.toHaveBeenCalled();
        expect(describedError(screen.getByLabelText("AMI ID"))).toMatch(/Must look like ami-/);
        expect(describedError(screen.getByLabelText("Stack name"))).toBe("Enter a stack name.");
        expect(describedError(screen.getByLabelText("Root size (GB)"))).toBe(
            "Enter a whole number of GB, 1 or greater."
        );
        expect(describedError(screen.getByLabelText("Target node profile ID"))).toBe(
            "Enter the profile id, a whole number 0 or greater."
        );
        expect(describedError(screen.getByLabelText("User data template ID"))).toBe(
            "Enter a whole number, 1 or greater."
        );
        expect(screen.getByRole("alert")).toHaveTextContent(
            "Correct the highlighted fields below."
        );
    });
});

describe("validateRegisterImage", () => {
    const values = {
        amiId: "ami-0123456789abcdef0",
        stackName: "s",
        baseOs: "amazonlinux2023",
        rootSize: "100",
        profileId: "0",
        userDataTemplateId: "1",
    };

    it("accepts a complete VDI and simulation form", () => {
        expect(validateRegisterImage(values, "vdi", false)).toEqual({});
        expect(validateRegisterImage(values, "simulation", false)).toEqual({});
    });

    it("does not require a base OS or a template id where the kind has no such field", () => {
        expect(validateRegisterImage({ ...values, baseOs: "" }, "simulation", false)).toEqual({});
        expect(validateRegisterImage({ ...values, userDataTemplateId: "" }, "vdi", false)).toEqual(
            {}
        );
    });

    it("rejects a fractional root size and a negative profile id", () => {
        expect(validateRegisterImage({ ...values, rootSize: "1.5" }, "vdi", false)).toEqual({
            rootSize: "Enter a whole number of GB, 1 or greater.",
        });
        expect(validateRegisterImage({ ...values, profileId: "-1" }, "vdi", false)).toEqual({
            profileId: "Enter the profile id, a whole number 0 or greater.",
        });
    });

    it("words the profile message for the picker when one is offered", () => {
        expect(validateRegisterImage({ ...values, profileId: "" }, "vdi", true)).toEqual({
            profileId: "Choose a profile.",
        });
    });

    it("with a picker, accepts only a profile the picker offers", () => {
        expect(
            validateRegisterImage({ ...values, profileId: "4" }, "vdi", true, ["1", "4"])
        ).toEqual({});
        // Typed into the number field before the capacity answer replaced it with the picker.
        expect(
            validateRegisterImage({ ...values, profileId: "7" }, "vdi", true, ["1", "4"])
        ).toEqual({
            profileId: "Choose a profile.",
        });
    });
});
