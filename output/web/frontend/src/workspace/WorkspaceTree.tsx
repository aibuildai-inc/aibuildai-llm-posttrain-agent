// The run's real workspace tree: a React Aria Tree over lazily loaded
// directory listings. Every row is a filesystem entry, so folder and file
// icons are right here (and nowhere else on the page). A directory loads
// its direct children when it is opened and polls them while the run is
// live; nothing walks the whole workspace. Expanded folders are URL-free
// page state that survives polls; the selected file is the `file` query.
import { useQueries, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight,
  File,
  FileCode,
  FileImage,
  FileText,
  Folder,
  FolderOpen,
  Link2,
  Ban,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Button,
  Collection,
  Tree,
  TreeItem,
  TreeItemContent,
  type Key,
} from "react-aria-components";
import { fetchWorkspaceEntries, type WorkspaceEntry } from "../api/client";
import { useRunId } from "../app/runId";
import { fmtBytes } from "../format";

const POLL_MS = 3000;

export function ancestorsOf(path: string): string[] {
  const parts = path.split("/");
  return parts.slice(0, -1).map((_, i) => parts.slice(0, i + 1).join("/"));
}

function EntryIcon(props: { entry: WorkspaceEntry; open: boolean }): ReactNode {
  const { entry } = props;
  const size = 14;
  if (entry.kind === "directory")
    return props.open ? (
      <FolderOpen size={size} strokeWidth={1.6} aria-hidden />
    ) : (
      <Folder size={size} strokeWidth={1.6} aria-hidden />
    );
  if (entry.kind === "symlink") return <Link2 size={size} strokeWidth={1.6} aria-hidden />;
  if (entry.kind === "other") return <Ban size={size} strokeWidth={1.6} aria-hidden />;
  if (entry.preview_kind === "code")
    return <FileCode size={size} strokeWidth={1.6} aria-hidden />;
  if (entry.preview_kind === "image")
    return <FileImage size={size} strokeWidth={1.6} aria-hidden />;
  if (entry.preview_kind === "text" || entry.preview_kind === "markdown")
    return <FileText size={size} strokeWidth={1.6} aria-hidden />;
  return <File size={size} strokeWidth={1.6} aria-hidden />;
}

function NoteRow(props: { id: string; text: string }): ReactNode {
  return (
    <TreeItem id={props.id} textValue={props.text || "note"} className="ws-row ws-row-note">
      <TreeItemContent>
        <div className="ws-row-inner ws-note">{props.text}</div>
      </TreeItemContent>
    </TreeItem>
  );
}

function kindWord(entry: WorkspaceEntry): string {
  if (entry.kind === "symlink") return "symlink";
  if (entry.kind === "other") return "special";
  return "";
}

export function WorkspaceTree(props: {
  runLive: boolean;
  selectedFile: string | null;
  onSelectFile: (path: string) => void;
  // The path the Inspector asked to reveal, with a counter so the same
  // path can be revealed twice; null until the first request.
  reveal: { path: string; nonce: number } | null;
  refreshNonce: number;
}): ReactNode {
  const runId = useRunId();
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState<Set<Key>>(() => new Set());
  const pendingScroll = useRef<string | null>(null);
  const host = useRef<HTMLDivElement | null>(null);

  // A deep-linked file or a reveal request opens every ancestor directory.
  const { selectedFile, reveal } = props;
  useEffect(() => {
    if (selectedFile === null) return;
    setExpanded((prev) => new Set([...prev, ...ancestorsOf(selectedFile)]));
    pendingScroll.current = selectedFile;
  }, [selectedFile]);
  useEffect(() => {
    if (reveal === null) return;
    setExpanded(
      (prev) => new Set([...prev, ...ancestorsOf(reveal.path), reveal.path]),
    );
    pendingScroll.current = reveal.path;
  }, [reveal]);

  // One query per open directory (the root always): the listings this tree
  // shows are exactly the ones it polls.
  const openDirs = ["", ...[...expanded].map(String)].filter(
    (dir, i, all) => all.indexOf(dir) === i,
  );
  const listings = useQueries({
    queries: openDirs.map((dir) => ({
      queryKey: ["workspace-entries", runId, dir],
      queryFn: () => fetchWorkspaceEntries(runId, dir),
      refetchInterval: props.runLive ? POLL_MS : false,
      retry: false,
    })),
  });
  const byDir = new Map(openDirs.map((dir, i) => [dir, listings[i]]));
  // React Aria caches each rendered item by its entry object; a change in
  // what is open or loaded must invalidate that cache or a folder keeps
  // its first render.
  const dependencies = [expanded, props.selectedFile, ...listings.map((l) => l.data ?? l.status)];
  const { refreshNonce } = props;
  useEffect(() => {
    if (refreshNonce === 0) return;
    void queryClient.invalidateQueries({ queryKey: ["workspace-entries", runId] });
  }, [refreshNonce, queryClient, runId]);

  // Scroll the revealed or deep-linked row into view once it exists.
  useEffect(() => {
    const target = pendingScroll.current;
    if (target === null || host.current === null) return;
    const row = host.current.querySelector<HTMLElement>(
      `[data-workspace-path="${CSS.escape(target)}"]`,
    );
    if (row === null) return;
    row.scrollIntoView({ block: "center" });
    pendingScroll.current = null;
  });

  const renderEntry = (entry: WorkspaceEntry): ReactNode => {
    const isDir = entry.kind === "directory";
    const open = isDir && expanded.has(entry.path);
    const listing = isDir ? byDir.get(entry.path) : undefined;
    const children = listing?.data?.entries ?? [];
    return (
      <TreeItem
        id={entry.path}
        textValue={entry.name}
        hasChildItems={isDir}
        className="ws-row"
        data-workspace-path={entry.path}
        data-selected-file={entry.path === props.selectedFile || undefined}
        onAction={() => {
          if (isDir) {
            setExpanded((prev) => {
              const next = new Set(prev);
              if (next.has(entry.path)) next.delete(entry.path);
              else next.add(entry.path);
              return next;
            });
          } else props.onSelectFile(entry.path); // a symlink or special entry opens its "unavailable" card
        }}
      >
        <TreeItemContent>
          {({ isExpanded, level }) => (
            <div
              className="ws-row-inner"
              style={{ paddingInlineStart: `${(level - 1) * 14 + 6}px` }}
            >
              {isDir ? (
                <Button slot="chevron" className="ws-chevron" aria-label="toggle">
                  <ChevronRight
                    size={13}
                    strokeWidth={1.75}
                    aria-hidden
                    className={isExpanded ? "ws-chevron-open" : undefined}
                  />
                </Button>
              ) : (
                <span className="ws-chevron-space" />
              )}
              <span className="ws-icon">
                <EntryIcon entry={entry} open={open} />
              </span>
              <span className="ws-name">{entry.name}</span>
              {kindWord(entry) !== "" && (
                <span className="ws-kind">{kindWord(entry)}</span>
              )}
              {entry.size !== null && (
                <span className="ws-size num">{fmtBytes(entry.size)}</span>
              )}
            </div>
          )}
        </TreeItemContent>
        {/* A directory always owns at least one row: React Aria expands
            only items with child nodes, so before (and without) a listing
            a note row stands in for the children. */}
        {isDir && (listing === undefined || listing.isPending || (listing.isFetching && children.length === 0)) && (
          <NoteRow id={`${entry.path}//loading`} text={open ? "loading…" : ""} />
        )}
        {isDir && listing !== undefined && listing.isError && (
          <NoteRow id={`${entry.path}//error`} text="directory unavailable" />
        )}
        {isDir && listing?.data !== undefined && !listing.isFetching && children.length === 0 && (
          <NoteRow id={`${entry.path}//empty`} text="empty" />
        )}
        {isDir && listing?.data?.truncated === true && (
          <NoteRow
            id={`${entry.path}//truncated`}
            text={`listing cut at ${children.length} entries`}
          />
        )}
        <Collection items={children} dependencies={dependencies}>
          {renderEntry}
        </Collection>
      </TreeItem>
    );
  };

  const root = byDir.get("");
  if (root === undefined || root.isPending)
    return <p className="quiet ws-empty">loading workspace…</p>;
  if (root.isError)
    return <p className="quiet ws-empty">workspace unavailable</p>;
  if (root.data.entries.length === 0)
    return <p className="quiet ws-empty">the workspace is empty</p>;
  return (
    <div className="ws-tree-host" ref={host}>
      <Tree
        aria-label="Workspace files"
        className="ws-tree"
        selectionMode="none"
        expandedKeys={expanded}
        onExpandedChange={setExpanded}
        items={root.data.entries}
        dependencies={dependencies}
      >
        {renderEntry}
      </Tree>
      {root.data.truncated && (
        <p className="quiet ws-note">
          listing cut at {root.data.entries.length} entries
        </p>
      )}
    </div>
  );
}
