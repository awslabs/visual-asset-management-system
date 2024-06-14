import Table from "@cloudscape-design/components/table";
import Select from "@cloudscape-design/components/select";
import Header from "@cloudscape-design/components/header";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";

import { useEffect, useState } from "react";
import { generateUUID } from "../../common/utils/utils";
import { OptionDefinition } from "@cloudscape-design/components/internal/components/option/interfaces";
import { Input } from "@cloudscape-design/components";
import { fetchRoles } from "../../services/APIService";

export interface RoleGroupPermission {
    id: string;
    groupId: string;
    permission: string;
    permissionType: string;
}

export interface RoleGroupPermissionsTableProps {
    permissions: RoleGroupPermission[];
    setPermissions: (permissions: RoleGroupPermission[]) => void;
    fetchGroups?: () => Promise<string[]>;
}

export interface RoleGroup {
    label: string;
    value: string;
}

export default function RoleGroupPermissionsTable({
    permissions,
    setPermissions,
    fetchGroups,
}: RoleGroupPermissionsTableProps) {
    const [selected, setSelected] = useState<RoleGroupPermission[]>([]);
    const [allRoleGroups, setAllRoleGroups] = useState<RoleGroup[]>([]);

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
            groupId: "",
            permission: "GET",
            permissionType: "allow",
        };

        let tmp = permissions;
        if (!permissions) {
            tmp = [];
        }

        setPermissions([...tmp, permission]);
    };

    useEffect(() => {
        const getData = async () => {
            let api_repsonse_message = await fetchRoles();

            if (Array.isArray(api_repsonse_message)) {
                let roleGroups: RoleGroup[] = [];
                roleGroups = api_repsonse_message.map((roleGroup) => {
                    return {
                        label: roleGroup.roleName,
                        value: roleGroup.roleName,
                    };
                });
                setAllRoleGroups(roleGroups);
            }
        };

        getData();
    }, []);

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
                                Add Role Group Permission
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
                                Remove Role Group Permission
                            </Button>
                        </SpaceBetween>
                    }
                ></Header>
            }
            items={permissions}
            columnDefinitions={[
                {
                    id: "groupId",
                    header: "Role Group",
                    minWidth: 136,
                    editConfig: {
                        ariaLabel: "Role Group",
                        editIconAriaLabel: "editable",
                        validation(item, value) {
                            if (value === undefined || value.length === 0) {
                                return "Role Group is required";
                            }
                        },
                        editingCell: (item, { currentValue, setValue }) => {
                            const value = currentValue ?? item.groupId;
                            return (
                                <Select
                                    autoFocus={true}
                                    expandToViewport={true}
                                    selectedOption={
                                        allRoleGroups.find((option) => option.value === value) ??
                                        null
                                    }
                                    onChange={(event) => {
                                        setValue(event.detail.selectedOption.value ?? item.groupId);
                                    }}
                                    options={allRoleGroups}
                                />
                            );
                        },
                    },
                    cell: (item) => {
                        return allRoleGroups.find((option) => option.value === item.groupId)?.label;
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
                    item[column.id as keyof RoleGroupPermission] = newValue as string;

                    const newPermissions = [...permissions.filter((x) => x.id !== item.id), item];

                    setPermissions(newPermissions);
                }
            }}
        />
    );
}
