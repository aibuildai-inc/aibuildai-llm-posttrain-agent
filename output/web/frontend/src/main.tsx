import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./app/App";
import "./styles/global.css";
import "./styles/atlas.css";
import "./styles/inspector.css";
import "./styles/motion.css";
import "./styles/markdown.css";
import "./styles/transcript.css";
import "./styles/workspace.css";
import "./styles/home.css";
import "./styles/controls.css";

const queryClient = new QueryClient();

const root = document.getElementById("root");
if (root === null) throw new Error("missing #root element");
createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        {/* reducedMotion="user": a prefers-reduced-motion user gets
            immediate transforms; opacity-only transitions remain. */}
        <MotionConfig reducedMotion="user">
          <App />
        </MotionConfig>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
