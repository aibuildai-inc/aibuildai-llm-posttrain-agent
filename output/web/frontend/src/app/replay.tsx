// The journal frame identity shown by this browser. Every detail query carries
// this revision, so one page frame cannot combine different Run moments.
import { createContext, useContext } from "react";

export interface FrameRevision {
  revision: number;
  historical: boolean;
}

export const FrameContext = createContext<FrameRevision | null>(null);

export function useFrame(): FrameRevision | null {
  return useContext(FrameContext);
}
