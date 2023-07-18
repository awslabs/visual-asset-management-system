import {createContext} from "react";
import {AssetDetail} from "../pages/AssetUpload";

const AssetDetailContext = createContext<AssetDetail | undefined>(undefined);
