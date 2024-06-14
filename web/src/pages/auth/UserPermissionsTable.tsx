import Table from "@cloudscape-design/components/table";
import Select from "@cloudscape-design/components/select";
import Header from "@cloudscape-design/components/header";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";

import { useState } from "react";
import { generateUUID } from "../../common/utils/utils";
import { OptionDefinition } from "@cloudscape-design/components/internal/components/option/interfaces";
import { Input } from "@cloudscape-design/components";

export interface UserPermission {
    id: string;
    userId: string;
    permission: string;
    permissionType: string;
}

export interface UserPermissionsTableProps {
    permissions: UserPermission[];
    setPermissions: (permissions: UserPermission[]) => void;
    fetchUsers?: () => Promise<string[]>;
}

export default function UserPermissionsTable({
    permissions,
    setPermissions,
    fetchUsers,
}: UserPermissionsTableProps) {
    const [selected, setSelected] = useState<UserPermission[]>([]);
    const allPermissions = [
        { label: "View/GET", value: "GET" },
        { label: "Add/PUT", value: "PUT" },
        { label: "Update/POST", value: "POST" },
        { label: "DELETE", value: "DELETE" },
    ];
    const permissionTypes = [
        { label: "Allow", value: "allow" },
        { label: "Deny", value: "deny" },
    ];

    const addPermissions = () => {
        const permission = {
            id: generateUUID(),
            userId: "",
            permission: "GET",
            permissionType: "allow",
        };

        let tmp = permissions;
        if (!permissions) {
            tmp = [];
        }

        setPermissions([...tmp, permission]);
    };

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
                                Add User Permission
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
                                Remove User Permission
                            </Button>
                        </SpaceBetween>
                    }
                ></Header>
            }
            items={permissions}
            columnDefinitions={[
                {
                    id: "userId",
                    header: "User ID",
                    cell: (item: UserPermission) => {
                        return item.userId;
                    },
                    editConfig: {
                        editingCell: (item, { currentValue, setValue }) => {
                            return (
                                <Input
                                    value={currentValue}
                                    onChange={({ detail }) => {
                                        setValue(detail.value);
                                    }}
                                />
                            );
                        },
                    },
                },
                {
                    id: "permission",
                    header: "Permission",
                    minWidth: 136,
                    editConfig: {
                        ariaLabel: "Permission",
                        editIconAriaLabel: "editable",
                        validation(item, value) {
                            if (value === undefined || value.length === 0) {
                                return "Permission is required";
                            }
                        },
                        editingCell: (item, { currentValue, setValue }) => {
                            const value = currentValue ?? item.permission;
                            return (
                                <Select
                                    autoFocus={true}
                                    expandToViewport={true}
                                    selectedOption={
                                        allPermissions.find((option) => option.value === value) ??
                                        null
                                    }
                                    onChange={(event) => {
                                        setValue(
                                            event.detail.selectedOption.value ?? item.permission
                                        );
                                    }}
                                    options={allPermissions}
                                />
                            );
                        },
                    },
                    cell: (item) => {
                        return allPermissions.find((option) => option.value === item.permission)
                            ?.label;
                    },
                },
                {
                    id: "permissionType",
                    header: "Permission Type",
                    minWidth: 136,
                    editConfig: {
                        ariaLabel: "Permission Type",
                        editIconAriaLabel: "editable",
                        validation(item, value) {
                            if (value === undefined || value.length === 0) {
                                return "Permission Type is required";
                            }
                        },
                        editingCell: (item, { currentValue, setValue }) => {
                            const value = currentValue ?? item.permissionType;
                            return (
                                <Select
                                    autoFocus={true}
                                    expandToViewport={true}
                                    selectedOption={
                                        permissionTypes.find((option) => option.value === value) ??
                                        null
                                    }
                                    onChange={(event) => {
                                        setValue(
                                            event.detail.selectedOption.value ?? item.permissionType
                                        );
                                    }}
                                    options={permissionTypes}
                                />
                            );
                        },
                    },
                    cell: (item) => {
                        return permissionTypes.find(
                            (option) => option.value === item.permissionType
                        )?.label;
                    },
                },
            ]}
            submitEdit={(item, column, newValue) => {
                if (column.id) {
                    item[column.id as keyof UserPermission] = newValue as string;

                    const newPermissions = [...permissions.filter((x) => x.id !== item.id), item];

                    setPermissions(newPermissions);
                }
            }}
        />
    );
}
