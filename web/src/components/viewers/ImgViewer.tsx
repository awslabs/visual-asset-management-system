import React, { useEffect, useState } from "react";
import { API, Storage, Cache } from "aws-amplify";
import { addColumnSortLabels } from "../../common/helpers/labels";

class ImgViewerProps {
    s3key!: string;
}

export default function ImgViewer({ s3key }: ImgViewerProps) {
    const [url, setUrl] = useState("placeholder.jpg");

    useEffect(() => {
        console.log("get key", s3key);
        Storage.get(s3key, {
            download: false,
            expires: 10,
        }).then(setUrl);
    });

    return <img src={url} style={{ width: "100%", height: "auto" }} />;
}
