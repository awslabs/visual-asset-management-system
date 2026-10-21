/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import DataTable from "../../../features/orchestration/components/DataTable";
import ContextMenu, {
    type ContextMenuItem,
} from "../../../features/orchestration/components/ContextMenu";
import RefreshButton from "../../../features/orchestration/components/RefreshButton";
import SearchInput from "../../../features/orchestration/components/SearchInput";
import { btnPrimary, btnSecondary } from "../../../features/orchestration/components/controlStyles";
import {
    useToast,
    toastErrorMessage,
} from "../../../features/orchestration/components/ToastProvider";
import { useEdhAdminImages, useEdhAdminMutations, useEdhCapacity } from "../api/adminQueries";
import type {
    EdhAdminImage,
    EdhImageKind,
    RegisterSimulationImageRequest,
    RegisterVdiImageRequest,
} from "../api/admin";
import { unconfirmedNotice } from "../api/outcome";
import RegisterImageDialog, { baseOsLabel, type RegisterImageBody } from "./RegisterImageDialog";
import { useTextFilter } from "../components/useTextFilter";
import { useInFlight } from "../components/useInFlight";
import { osFamilyLabel } from "../labels";
import ConfirmActionDialog from "./ConfirmActionDialog";

interface ImagesTabProps {
    can: (method: string, pathTemplate: string) => boolean;
}

const KIND_LABEL: Record<EdhImageKind, string> = { vdi: "VDI", simulation: "Simulation" };
// One chip colour per kind, the pairs the pipelines list uses for its execution types.
const KIND_BADGE: Record<EdhImageKind, string> = {
    vdi: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
    simulation: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200",
};

// The rows shown before the first answer; one instance, so the search memo's dependency holds.
const NO_IMAGES: EdhAdminImage[] = [];

/** The OS column: the family, and for a VDI image the base OS it was registered with. */
const imageOsLabel = (image: Pick<EdhAdminImage, "kind" | "osFamily" | "baseOs">) =>
    image.kind === "vdi"
        ? `${osFamilyLabel(image.osFamily)} (${baseOsLabel(image.baseOs)})`
        : osFamilyLabel("linux");

const searchableText = (image: EdhAdminImage) =>
    [
        image.stackName,
        image.amiId,
        image.imageId,
        KIND_LABEL[image.kind],
        imageOsLabel(image),
        image.baseOs,
        image.architecture,
        image.description,
    ]
        .filter(Boolean)
        .join(" ");

const ImagesTab: React.FC<ImagesTabProps> = ({ can }) => {
    const toast = useToast();
    const { data, isLoading, error, refetch, isFetching } = useEdhAdminImages();
    const { registerVdi, registerSimulation, deleteImage } = useEdhAdminMutations();
    const { claim, release } = useInFlight();
    const [registerKind, setRegisterKind] = useState<EdhImageKind | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<EdhAdminImage | null>(null);
    const rows = data ?? NO_IMAGES;
    // The toolbar owns the search box, so the table's own is off.
    const { searchText, setSearchText, visibleRows } = useTextFilter(rows, searchableText);

    // Templates are the apiRoutes.py path strings verbatim.
    const canRegisterVdi = can("POST", "/addon/edh/admin/images/vdi");
    const canRegisterSimulation = can("POST", "/addon/edh/admin/images/simulation");
    const canDeleteVdi = can("DELETE", "/addon/edh/admin/images/vdi/{imageId}");
    const canDeleteSimulation = can("DELETE", "/addon/edh/admin/images/simulation/{imageId}");
    const canReadCapacity = can("GET", "/addon/edh/admin/capacity");
    // The virtual desktop profiles a VDI image names, read for the register dialog's picker only
    // while that dialog is open.
    const capacity = useEdhCapacity(registerKind === "vdi" && canReadCapacity);

    // Stable across renders: a fresh `cell` function would remount the cell and close an open row
    // menu. The action cell reaches only `setDeleteTarget`, which never changes identity.
    const columns = useMemo<ColumnDef<EdhAdminImage>[]>(() => {
        const rowActions = (image: EdhAdminImage) => {
            const allowed = image.kind === "vdi" ? canDeleteVdi : canDeleteSimulation;
            if (!allowed) return null;
            const items: ContextMenuItem[] = [
                { label: "Delete", danger: true, onSelect: () => setDeleteTarget(image) },
            ];
            return (
                <ContextMenu
                    items={items}
                    trigger={
                        <button
                            aria-label={`Actions for ${image.stackName}`}
                            className="bg-transparent border-0 px-2 py-1 rounded text-lg leading-none text-text-secondary hover:text-gray-700 hover:bg-gray-100 dark:hover:text-gray-300 dark:hover:bg-gray-700 cursor-pointer"
                        >
                            ⋮
                        </button>
                    }
                />
            );
        };
        return [
            {
                accessorKey: "kind",
                header: "Kind",
                cell: ({ row }) => (
                    <span
                        className={`px-2 py-1 text-xs font-semibold rounded ${
                            KIND_BADGE[row.original.kind]
                        }`}
                    >
                        {KIND_LABEL[row.original.kind]}
                    </span>
                ),
            },
            {
                accessorKey: "stackName",
                header: "Stack name",
                cell: ({ row }) => (
                    <span className="font-semibold text-text-primary">
                        {row.original.stackName}
                    </span>
                ),
            },
            {
                accessorKey: "amiId",
                header: "AMI",
                sortingFn: "text",
                cell: ({ row }) => <span className="font-mono text-sm">{row.original.amiId}</span>,
            },
            {
                accessorKey: "osFamily",
                header: "OS",
                cell: ({ row }) => imageOsLabel(row.original),
            },
            {
                accessorKey: "architecture",
                header: "Architecture",
                sortingFn: "text",
                cell: ({ row }) => row.original.architecture || "—",
            },
            {
                accessorKey: "rootSize",
                header: "Root (GB)",
                cell: ({ row }) => row.original.rootSize ?? "—",
            },
            {
                accessorKey: "profileId",
                header: "Profile",
                cell: ({ row }) => row.original.profileId ?? "—",
            },
            {
                accessorKey: "description",
                header: "Description",
                cell: ({ row }) => (
                    <span className="text-sm text-text-secondary">
                        {row.original.description || "—"}
                    </span>
                ),
            },
            // A display column: the table renders the cell as given and offers no sort on it.
            ...(canDeleteVdi || canDeleteSimulation
                ? [
                      {
                          id: "actions",
                          header: "Actions",
                          cell: ({ row }) => rowActions(row.original),
                      } as ColumnDef<EdhAdminImage>,
                  ]
                : []),
        ];
    }, [canDeleteVdi, canDeleteSimulation]);

    const handleRegister = async (body: RegisterImageBody) => {
        if (!registerKind || !claim("register")) return;
        try {
            if (registerKind === "vdi") {
                await registerVdi.mutateAsync(body as RegisterVdiImageRequest);
            } else {
                await registerSimulation.mutateAsync(body as RegisterSimulationImageRequest);
            }
            setRegisterKind(null);
            toast.success("Image registered", { description: body.stackName });
        } catch (err) {
            const unconfirmed = unconfirmedNotice("Registration", err);
            if (unconfirmed) {
                // The image may be registered already, so the form closes rather than invite a resend.
                setRegisterKind(null);
                toast.warning(unconfirmed.title, {
                    description: `${body.stackName}: ${unconfirmed.message}`,
                });
                return;
            }
            toast.error("Registration failed", {
                description: `${body.stackName}: ${toastErrorMessage(err)}`,
            });
        } finally {
            release("register");
        }
    };

    const handleDelete = async () => {
        if (!deleteTarget) return;
        const target = deleteTarget;
        const key = `${target.kind}/${target.imageId}`;
        if (!claim(key)) return;
        try {
            await deleteImage.mutateAsync({ kind: target.kind, imageId: target.imageId });
            setDeleteTarget(null);
            toast.success("Image deleted", { description: target.stackName });
        } catch (err) {
            // Close the confirmation and report through a toast; the dialog has no error slot.
            setDeleteTarget(null);
            const unconfirmed = unconfirmedNotice("Delete", err);
            if (unconfirmed) {
                toast.warning(unconfirmed.title, {
                    description: `${target.stackName}: ${unconfirmed.message}`,
                });
                return;
            }
            toast.error("Delete failed", {
                description: `${target.stackName}: ${toastErrorMessage(err)}`,
            });
        } finally {
            release(key);
        }
    };

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2 flex-wrap">
                    <SearchInput
                        value={searchText}
                        onChange={(e) => setSearchText(e.target.value)}
                        ariaLabel="Search images"
                    />
                    <RefreshButton
                        onClick={() => refetch()}
                        busy={isFetching}
                        ariaLabel="Refresh images"
                    />
                    <span className="text-sm text-text-secondary">{rows.length} images</span>
                </div>
                <div className="flex items-center gap-2">
                    {canRegisterVdi && (
                        <button className={btnPrimary} onClick={() => setRegisterKind("vdi")}>
                            Register VDI image
                        </button>
                    )}
                    {canRegisterSimulation && (
                        <button
                            className={btnSecondary}
                            onClick={() => setRegisterKind("simulation")}
                        >
                            Register simulation image
                        </button>
                    )}
                </div>
            </div>

            {isLoading ? (
                <div className="p-8 text-center text-text-secondary">Loading images...</div>
            ) : error ? (
                <div className="p-4 text-vams-error">
                    Error loading images: {toastErrorMessage(error)}
                </div>
            ) : rows.length === 0 ? (
                <div className="p-8 text-center text-text-secondary">No images are registered.</div>
            ) : (
                <DataTable
                    columns={columns}
                    rows={visibleRows}
                    ariaLabel="Registered EDH images"
                    getRowId={(image) => `${image.kind}:${image.imageId}`}
                    filtering={false}
                />
            )}

            {registerKind && (
                <RegisterImageDialog
                    kind={registerKind}
                    open
                    busy={registerVdi.isPending || registerSimulation.isPending}
                    onOpenChange={(isOpen) => !isOpen && setRegisterKind(null)}
                    onSubmit={handleRegister}
                    profiles={registerKind === "vdi" ? capacity.data?.profiles : undefined}
                />
            )}

            {deleteTarget && (
                <ConfirmActionDialog
                    open
                    title={`Delete ${deleteTarget.stackName}`}
                    confirmLabel="Delete"
                    busy={deleteImage.isPending}
                    onCancel={() => setDeleteTarget(null)}
                    onConfirm={handleDelete}
                    message={
                        <p>
                            Delete the {KIND_LABEL[deleteTarget.kind]} image{" "}
                            <strong>{deleteTarget.stackName}</strong> ({deleteTarget.amiId}) from
                            the EDH catalog? Running sessions launched from it are not affected.
                        </p>
                    }
                />
            )}
        </div>
    );
};

export default ImagesTab;
