/* eslint-disable jsx-a11y/alt-text */
import React, { useEffect, useState } from "react";
import { getPresignedKey } from "../../common/auth/s3";

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
            const tmp = await getPresignedKey(assetId, databaseId, assetKey);
            setUrl(tmp);
        };
        fun();
    }, [assetId, assetKey, databaseId, url]);

    const fallback = (error: any) => {
        console.log("handling image load err", error);
        if (err === null) {
            setErr(error);
            getPresignedKey(assetId, databaseId, altAssetKey).then(setUrl);
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
