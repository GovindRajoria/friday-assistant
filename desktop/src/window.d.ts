/// <reference types="vite/client" />
import type { FridayApi } from "../electron/api";

declare global {
  interface Window {
    friday: FridayApi;
  }
}

export {};
