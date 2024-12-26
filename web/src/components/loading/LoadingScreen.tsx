import Spinner from "@cloudscape-design/components/spinner";
import React from "react";

const LoadingScreen = () => {
    return (
        <div
            aria-live="polite"
            aria-label="Loading page content."
            style={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                height: "100%",
                flexFlow: "column"
            }}
        >
            <Spinner size="large" />
            <p>
                Loading, Please Wait...
            </p>
        </div>
    );
};

export default LoadingScreen;