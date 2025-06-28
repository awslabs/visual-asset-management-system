/* eslint-disable jsx-a11y/alt-text */
import React, { useEffect, useState } from "react";
import { downloadAsset } from "../../services/APIService";

class ImgViewerProps {
    assetId!: string;
    databaseId!: string;
    assetKey!: string;
    altAssetKey!: string;
}

export default function ImgViewer({ assetId, databaseId, assetKey, altAssetKey }: ImgViewerProps) {
    const init = "placeholder.jpg";
    const [url, setUrl] = useState(init);
    const [err, setErr] = useState(null);

    useEffect(() => {
        if (url !== init) {
            return;
        }
        const fun = async () => {
            await downloadAsset({
                assetId: assetId,
                databaseId: databaseId,
                key: assetKey,
                versionId: "",
                downloadType: "assetFile",
            }).then((response) => {
                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        // TODO: error handling (response[1] has error message)
                        console.error(response);
                    } else {
                        setUrl(response[1]);
                    }
                }
            });
        };

        fun();
    }, [assetId, assetKey, databaseId, url]);

    const fallback = async (error: any) => {
        console.log("handling image load err", error);
        if (err === null) {
            setErr(error);

            await downloadAsset({
                assetId: assetId,
                databaseId: databaseId,
                key: altAssetKey,
                versionId: "",
                downloadType: "assetFile",
            }).then((response) => {
                if (response !== false && Array.isArray(response)) {
                    if (response[0] === false) {
                        // TODO: error handling (response[1] has error message)
                        console.error(response);
                    } else {
                        setUrl(response[1]);
                    }
                }
            });
        }
    };
    return (
        <img
            src={url}
            style={{ maxWidth: "100%", maxHeight: "100%", height: "100%" }}
            onError={fallback}
        />
    );
}
