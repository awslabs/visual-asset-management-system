/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Select, SelectProps } from "@cloudscape-design/components";
import { API } from "aws-amplify";
import { useState, useEffect } from "react";

interface GroupControlListProps {
    selectedGroup: string | null;
    setSelectedGroup: (value: string | null) => void;
    disabled: boolean;
    label: string;
    description: string;
    errorText: () => string | null;
    fetchGroups?: () => Promise<string[]>;
}

function GroupControList({
    selectedGroup,
    setSelectedGroup,
    disabled,
    label,
    description,
    errorText,
    fetchGroups = () => API.get("api", `auth/groups`, {}).then((res) => res.claims),
}: GroupControlListProps) {
    const [loadingGroups, setLoadingGroups] = useState(true);
    const [groupOptions, setGroupOptions] = useState<SelectProps.Option[]>([]);
    const [selectedOption, setSelectedOption] = useState<SelectProps.Option>();
    useEffect(() => {
        if (!loadingGroups) return;
        fetchGroups().then((claims) => {
            const opts: SelectProps.Option[] = claims.map((value: string) => ({
                value,
                label: value,
                description: "Unlabeled group",
            }));
            setGroupOptions(opts);
            const option = opts.find((x) => x.value === selectedGroup);
            setSelectedOption(option);
            // setSelectedGroup(
            //     opts.filter(
            //         (x) =>
            //             selectedGroupList &&
            //             x.value !== undefined &&
            //             selectedGroupList.indexOf(x.value) > -1
            //     )
            // );
            setLoadingGroups(false);
        });
    }, [fetchGroups, loadingGroups, selectedGroup, setSelectedGroup]);

    return (
        <Select
            selectedOption={selectedOption || null}
            disabled={disabled}
            onChange={({ detail }) => {
                setSelectedGroup(detail.selectedOption.value || null);
                setSelectedOption(detail.selectedOption);
            }}
            loadingText={loadingGroups ? "Loading..." : undefined}
            options={groupOptions}
            filteringType="auto"
            expandToViewport={true}
            placeholder="Choose options"
            selectedAriaLabel="Selected"
        />
    );
}

export default GroupControList;
