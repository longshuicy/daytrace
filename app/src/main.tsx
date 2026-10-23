import { getCurrentWindow } from "@tauri-apps/api/window";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import DayView from "./components/DayView";
import Pill from "./components/Pill";
import "./theme.css";

async function start() {
  if (import.meta.env.DEV && !("__TAURI_INTERNALS__" in window)) {
    await (await import("./devshim")).installDevShim();
  }
  // Both windows load this page; the window label picks the view.
  const label = getCurrentWindow().label;
  document.documentElement.dataset.window = label;
  createRoot(document.getElementById("root")!).render(
    <StrictMode>{label === "pill" ? <Pill /> : <DayView />}</StrictMode>,
  );
}

start();
