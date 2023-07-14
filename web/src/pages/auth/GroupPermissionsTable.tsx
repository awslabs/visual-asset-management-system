import Table from "@cloudscape-design/components/table";
import Select from "@cloudscape-design/components/select";
import Header from "@cloudscape-design/components/header";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";

import { useState } from "react";
import { generateUUID } from "../../common/utils/utils";
import GroupSelectList from "./GroupSelectList";
import { OptionDefinition } from "@cloudscape-design/components/internal/components/option/interfaces";

export interface GroupPermission {
    id: string;
    groupId: string;
    permission: string;
}

export interface GroupPermissionsTableProps {
    permissions: GroupPermission[];
    setPermissions: (permissions: GroupPermission[]) => void;
    fetchGroups?: () => Promise<string[]>;
}

export default function GroupPermissionsTable({
    permissions,
    setPermissions,
    fetchGroups,
}: GroupPermissionsTableProps) {
    const [selected, setSelected] = useState<GroupPermission[]>([]);

    const addPermissions = () => {
        const permission = {
            id: generateUUID(),
            groupId: "",
            permission: "",
        };

        let tmp = permissions;
        if (!permissions) {
            tmp = [];
        }

        setPermissions([...tmp, permission]);
    };

    const options: OptionDefinition[] = [
        {
            value: "Read",
            description: "View assets only",
        },
        {
            value: "Edit",
            description: "View and modify assets",
        },
        {
            value: "Admin",
            description: "View, modify, delete, share",
        },
    ];

    return (
        <Table
            aria-label="Permissions"
            onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
            trackBy="id"
            selectionType="multi"
            selectedItems={selected}
            header={
                <Header
                    actions={
                        <SpaceBetween direction="horizontal" size="xs">
                            <Button onClick={addPermissions} data-testid="add-permission-button">
                                Add Group Permission
                            </Button>
                            <Button
                                disabled={selected.length === 0}
                                onClick={() => {
                                    const newPermissions = permissions.filter((x) => {
                                        return !selected.find((y) => y.id === x.id);
                                    });

                                    setPermissions(newPermissions);
                                }}
                            >
                                Remove Group Permission
                            </Button>
                        </SpaceBetween>
                    }
                >
                    Criteria
                </Header>
            }
            items={permissions}
            columnDefinitions={[
                {
                    id: "groupId",
                    header: "Group",
                    cell: (item: GroupPermission) => {
                        return item.groupId;
                    },
                    editConfig: {
                        editingCell: (item, { currentValue, setValue }) => {
                            return (
                                <GroupSelectList
                                    selectedGroup={currentValue}
                                    setSelectedGroup={(group) => setValue(group)}
                                    label="Group"
                                    description="Select a group"
                                    disabled={false}
                                    errorText={() => null}
                                    fetchGroups={fetchGroups}
                                />
                            );
                        },
                    },
                },
                {
                    id: "permission",
                    header: "Permission",
                    cell: (item: GroupPermission) => {
                        const o = options.find((x) => x.value === item.permission);
                        return (
                            <>
                                {o?.value}: <i>{o?.description}</i>
                            </>
                        );
                    },
                    editConfig: {
                        editingCell: (item, { currentValue, setValue }) => {
                            return (
                                <Select
                                    selectedOption={
                                        options.find(
                                            (x) => x.value === (currentValue ?? item.permission)
                                        ) ?? null
                                    }
                                    options={options}
                                    onChange={({ detail }) => setValue(detail.selectedOption.value)}
                                    expandToViewport={true}
                                />
                            );
                        },
                    },
                },
            ]}
            submitEdit={(item, column, newValue) => {
                if (column.id) {
                    item[column.id as keyof GroupPermission] = newValue as string;

                    const newPermissions = [...permissions.filter((x) => x.id !== item.id), item];

                    setPermissions(newPermissions);
                }
            }}
        />
    );
}
