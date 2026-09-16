// The right surface: the run's real workspace. Tree only until a file is
// selected; then an inner split of tree | preview inside the same outer
// panel. The header carries the manual refresh. Widths inside belong to
// react-resizable-panels and the user.
import { RefreshCw, X } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { Button } from "react-aria-components";
import { Group, Panel, Separator } from "react-resizable-panels";
import { useFrame } from "../app/replay";
import { FilePreview } from "./FilePreview";
import { ancestorsOf, WorkspaceTree } from "./WorkspaceTree";

export function WorkspaceShell(props: {
  runLive: boolean;
  file: string | null;
  onSelectFile: (path: string | null) => void;
  reveal: { path: string; nonce: number } | null;
  onClose: () => void;
}): ReactNode {
  const [refreshNonce, setRefreshNonce] = useState(0);
  // The filesystem is not time-indexed. A historical frame shows current files
  // while the Run is active and final files after it ends.
  const frame = useFrame();
  const shell = useRef<HTMLElement | null>(null);
  // Closing the preview unmounts the button that was focused, so focus
  // moves to the closed file's tree row first — or, when that row is
  // gone, the nearest ancestor directory row, then the tree itself.
  // A keyboard user continues from where they were instead of from BODY.
  const closePreview = (): void => {
    const path = props.file;
    props.onSelectFile(null);
    const root = shell.current;
    if (path === null || root === null) return;
    for (const target of [path, ...ancestorsOf(path).reverse()]) {
      const row = root.querySelector<HTMLElement>(
        `[data-workspace-path="${CSS.escape(target)}"]`,
      );
      if (row !== null) {
        row.focus();
        return;
      }
    }
    root.querySelector<HTMLElement>('[role="treegrid"], [role="tree"]')?.focus();
  };
  const tree = (
    <WorkspaceTree
      runLive={props.runLive}
      selectedFile={props.file}
      onSelectFile={props.onSelectFile}
      reveal={props.reveal}
      refreshNonce={refreshNonce}
    />
  );
  return (
    <aside className="workspace" aria-label="Workspace" ref={shell}>
      <header className="workspace-header">
        <span className="workspace-title">
          {frame?.historical === true ? "Current workspace" : "Workspace"}
        </span>
        {frame?.historical === true && (
          <span className="quiet workspace-sub">
            {props.runLive ? "current active files" : "final files"} — not historical
          </span>
        )}
        <Button
          className="inspector-close"
          aria-label="Refresh workspace"
          onPress={() => setRefreshNonce((n) => n + 1)}
        >
          <RefreshCw size={14} strokeWidth={1.75} aria-hidden />
        </Button>
        <Button
          className="inspector-close"
          aria-label="Close workspace"
          onPress={props.onClose}
        >
          <X size={15} strokeWidth={1.75} aria-hidden />
        </Button>
      </header>
      {/* The tree panel stays mounted whether or not a file is open, so
          opening and closing a preview never resets which folders are
          expanded. */}
      <Group orientation="horizontal" id="workspace-split" className="workspace-split">
        <Panel id="workspace-tree" defaultSize="38" className="workspace-body">
          {tree}
        </Panel>
        {props.file !== null && (
          <>
            <Separator className="inspector-resize" aria-label="Resize file preview" />
            <Panel id="workspace-preview" defaultSize="62">
              <FilePreview
                path={props.file}
                runLive={props.runLive}
                onClose={closePreview}
              />
            </Panel>
          </>
        )}
      </Group>
    </aside>
  );
}
