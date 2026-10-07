import { createRoot } from "react-dom/client";
import { Root } from "./Root";
import "./styles.css";

// No StrictMode: its doubled effects would open two connections to the host in development.
createRoot(document.getElementById("root")!).render(<Root />);
