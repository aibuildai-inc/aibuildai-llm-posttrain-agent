// The two connection ports every Atlas node carries. Their side follows
// the flow direction of the scope the node sits in, so an edge inside a
// composite card (which stacks DOWN) enters at the top and leaves at the
// bottom, while an edge between Search-scope siblings (which flow RIGHT)
// enters at the left and leaves at the right -- the same direction ELK
// laid that scope out in.
import { Handle, Position } from "@xyflow/react";
import type { ReactNode } from "react";
import type { FlowDirection } from "./graph";

export function Ports(props: { direction: FlowDirection }): ReactNode {
  const down = props.direction === "DOWN";
  return (
    <>
      <Handle
        type="target"
        position={down ? Position.Top : Position.Left}
        className="atlas-port"
      />
      <Handle
        type="source"
        position={down ? Position.Bottom : Position.Right}
        className="atlas-port"
      />
    </>
  );
}
