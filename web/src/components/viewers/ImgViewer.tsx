import React, { useEffect, useState } from "react";
import { API, Storage, Cache } from "aws-amplify";
import { addColumnSortLabels } from "../../common/helpers/labels";

class ImgViewerProps {
    assetKey!: string;
}

export default function ImgViewer({ assetKey }: ImgViewerProps) {
    const [url, setUrl] = useState("placeholder.jpg");

    useEffect(() => {
        console.log("get key", assetKey);
        Storage.get(assetKey, {
            download: false,
            expires: 10,
        }).then(setUrl);
    });

    return <img src={url} style={{ width: "100%", height: "auto" }} />;
}
