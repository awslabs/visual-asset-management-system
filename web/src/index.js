/**
 * Establishes global css, fetches server-side resource,
 * configures amplify and initialized react app.
 */
import React from "react";
import ReactDOM from "react-dom";
import "./styles/index.scss";
import App from "./App";
import reportWebVitals from "./reportWebVitals";
import ConfigLoader from "./ConfigLoader";

window.LOG_LEVEL = "INFO";

ReactDOM.render(
  <React.StrictMode>
    <ConfigLoader>
      <App />
    </ConfigLoader>
  </React.StrictMode>,
  document.getElementById("root")
);

reportWebVitals();
