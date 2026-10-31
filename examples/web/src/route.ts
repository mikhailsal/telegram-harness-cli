import { useSyncExternalStore } from "react";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
  };
}

function currentPath(): string {
  return window.location.pathname;
}

/** Show the page at `path` without loading the document again. */
export function navigate(path: string): void {
  window.history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

/** Return the path of the shown page; the component renders again when it changes. */
export function usePath(): string {
  return useSyncExternalStore(subscribe, currentPath);
}
