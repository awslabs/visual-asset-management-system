import React, { useEffect, useState } from "react";
import { Storage } from "aws-amplify";

class ImgViewerProps {
    assetKey!: string;
    altAssetKey!: string;
}

export default function ImgViewer({ assetKey, altAssetKey }: ImgViewerProps) {
    const init = "placeholder.jpg";
    const [url, setUrl] = useState(init);
    const [err, setErr] = useState(null);

    useEffect(() => {
        if (url !== init) {
            return;
        }
        const fun = async () => {
            const tmp = await Storage.get(assetKey, {
                download: false,
                expires: 10,
            });
            setUrl(tmp);
        };
        fun();
    }, [url]);

    const fallback = (error: any) => {
        console.log("handling image load err", error);
        if (err === null) {
            setErr(error);
            Storage.get(altAssetKey, { download: false, expires: 10 }).then(setUrl);
        }
    };
    return <img src={url} style={{ width: "100%", height: "auto" }} onError={fallback} />;
}
