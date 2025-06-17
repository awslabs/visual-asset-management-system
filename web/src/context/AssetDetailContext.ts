import { createContext, useReducer } from "react";
import { AssetDetail } from "../pages/AssetUpload/AssetUpload";

export interface AssetDetailAction {
    type: string;
    payload: any;
}
export const assetDetailReducer = (state: AssetDetail, action: AssetDetailAction): AssetDetail => {
    switch (action.type) {
        case "SET_ASSET_DETAIL":
            return action.payload;
        default:
            return state;
    }
};

export type AssetDetailContextType = {
    state: AssetDetail;
    dispatch: any;
};

export const AssetDetailContext = createContext<AssetDetailContextType | undefined>(undefined);
