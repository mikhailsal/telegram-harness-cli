import type { HarnessAdapter } from "../adapters/contract.js";

export function stopped(signal: AbortSignal): boolean {
  return signal.aborted;
}
export function delay(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const finish = (): void => {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timer = setTimeout(finish, ms);
    signal.addEventListener("abort", finish, { once: true });
    if (signal.aborted) finish();
  });
}
export function untilAbort<T>(
  promise: Promise<T>,
  signal: AbortSignal,
): Promise<T | undefined> {
  if (signal.aborted) return Promise.resolve(undefined);
  return new Promise((resolve, reject) => {
    const abort = (): void => {
      signal.removeEventListener("abort", abort);
      resolve(undefined);
    };
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
    promise.then(
      (value) => {
        signal.removeEventListener("abort", abort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", abort);
        reject(
          error instanceof Error
            ? error
            : new Error("Asynchronous operation failed"),
        );
      },
    );
  });
}
function unavailable<T>(): Promise<T> {
  return Promise.reject(new Error("Adapter is disconnected"));
}
export function offlineAdapter(): HarnessAdapter {
  return {
    capabilities: new Set(),
    newSession: () => unavailable(),
    submit: () => unavailable(),
    observe: () => ({
      [Symbol.asyncIterator]: () => ({
        next: () => Promise.resolve({ done: true, value: undefined }),
      }),
    }),
    interrupt: () => unavailable(),
    approve: () => unavailable(),
    reconcile: () => unavailable(),
    attach: () => unavailable(),
    close: () => Promise.resolve(),
  };
}
