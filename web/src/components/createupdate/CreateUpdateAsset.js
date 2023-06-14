/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { AssetFormDefinition } from "./form-definitions/AssetFormDefinition";
import AssetEntity from "./entity-types/AssetEntity";
import CreateUpdateElement from "./CreateUpdateElement";
import { actionTypes } from "./form-definitions/types/FormDefinition";

export default function CreateUpdateAsset(props) {
    const {
        open,
        setOpen,
        setReload,
        databaseId,
        assetId,
        actionType = actionTypes.CREATE,
        asset,
        setAsset,
    } = props;

    if (actionType === actionTypes.CREATE) {
        return (
            <CreateUpdateElement
                open={open}
                setOpen={setOpen}
                setReload={setReload}
                formDefinition={AssetFormDefinition}
                formEntity={AssetEntity}
                databaseId={databaseId}
                elementId={assetId}
                actionType={actionType}
            />
        );
    } else if (actionType === actionTypes.UPDATE) {
        return (
            <CreateUpdateElement
                open={open}
                setOpen={setOpen}
                setReload={setReload}
                formDefinition={AssetFormDefinition}
                formEntity={AssetEntity}
                asset={asset}
                setAsset={setAsset}
                databaseId={databaseId}
                elementId={assetId}
                actionType={actionType}
            />
        );
    }
}
