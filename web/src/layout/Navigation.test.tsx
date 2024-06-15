/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { render } from "@testing-library/react";

import createWrapper, { ElementWrapper } from "@cloudscape-design/components/test-utils/dom";

import { Navigation } from "./Navigation";

function createUser(roles: string[]) {
    return {
        signInUserSession: {
            idToken: {
                payload: {
                    "vams:roles": JSON.stringify(roles),
                },
            },
        },
    };
}
function expectSideNavLinks(wrapper: ElementWrapper<Element>, ...links: string[]) {
    links.forEach((link) => {
        expect(wrapper.findSideNavigation()?.findLinkByHref(link)).toBeTruthy();
    });
}
function expectNoSideNavLinks(wrapper: ElementWrapper<Element>, ...links: string[]) {
    links.forEach((link) => {
        expect(wrapper.findSideNavigation()?.findLinkByHref(link)).toBeFalsy();
    });
}

describe("Navigation", () => {
    it("should render all links for super-admin", async () => {
        const { container } = render(
            <Navigation activeHref={"#/assets"} user={createUser(["super-admin"])} />
        );
        const wrapper = createWrapper(container);
        expectSideNavLinks(
            wrapper,
            "#/assets",
            "#/upload",
            "#/comments",
            "#/pipelines",
            "#/workflows",
            "#/auth/constraints"
        );
    });

    it("should render assets and visualizer links for the assets role", async () => {
        const { container } = render(
            <Navigation activeHref={"#/assets"} user={createUser(["assets"])} />
        );
        const wrapper = createWrapper(container);
        expectSideNavLinks(wrapper, "#/assets", "#/upload", "#/comments");
        expectNoSideNavLinks(wrapper, "#/pipelines", "#/workflows", "#/auth/constraints");
    });

    it("should render pipelines for the pipeline role", async () => {
        const { container } = render(
            <Navigation activeHref={"#/assets"} user={createUser(["pipelines"])} />
        );
        const wrapper = createWrapper(container);
        expectSideNavLinks(wrapper, "#/pipelines");
        expectNoSideNavLinks(
            wrapper,
            "#/assets",
            "#/upload",
            "#/comments",
            "#/workflows",
            "#/auth/constraints"
        );
    });

    it("should render workflows for the workflow role", async () => {
        const { container } = render(
            <Navigation activeHref={"#/assets"} user={createUser(["workflows"])} />
        );
        const wrapper = createWrapper(container);
        expectSideNavLinks(wrapper, "#/workflows");
        expectNoSideNavLinks(
            wrapper,
            "#/assets",
            "#/comments",
            "#/pipelines",
            "#/auth/constraints"
        );
    });

    it("should render combined links for assets, piplines, and workflows roles", async () => {
        const { container } = render(
            <Navigation
                activeHref={"#/assets"}
                user={createUser(["assets", "pipelines", "workflows"])}
            />
        );
        const wrapper = createWrapper(container);
        expectSideNavLinks(wrapper, "#/assets", "#/comments", "#/pipelines", "#/workflows");
        expectNoSideNavLinks(wrapper, "#/auth/constraints");
    });
});
