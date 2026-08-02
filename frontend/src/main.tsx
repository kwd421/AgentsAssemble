import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import GoogleAccountHandoffPage, {
  consumeGoogleAccountHandoffToken,
} from "./views/GoogleAccountHandoffPage";
import "./index.css";

const googleAccountHandoffToken = consumeGoogleAccountHandoffToken();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {googleAccountHandoffToken ? (
      <GoogleAccountHandoffPage token={googleAccountHandoffToken} />
    ) : (
      <App />
    )}
  </React.StrictMode>
);
