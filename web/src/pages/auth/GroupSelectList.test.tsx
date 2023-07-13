/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { render } from "@testing-library/react";
import { act } from "react-dom/test-utils";
import GroupSelectList from "./GroupSelectList";
import createWrapper from "@cloudscape-design/components/test-utils/dom";

describe("GroupSelectList", () => {
    it("renders", async () => {
        const promise = Promise.resolve(["one", "two", "three"]);
        const setSelecteGroup = jest.fn();
        const { container } = render(
            <GroupSelectList
                selectedGroup={"one"}
                setSelectedGroup={setSelecteGroup}
                disabled={false}
                label="Groups"
                description="a description"
                fetchGroups={jest.fn(() => promise)}
                errorText={() => null}
            />,
        );
        const wrapper = createWrapper(container);
        await act(async () => {
            await promise;
            await setSelecteGroup;
        });

        expect(wrapper.findSelect()).toBeTruthy();
    });

    it("renders with groups", () => {});
});
