// One selected workspace file. The backend's preview_kind picks the
// surface: highlighted source, plain text, Markdown (rendered or raw),
// a rendered PDF (react-pdf with per-page Canvas), a contained image,
// or a metadata card with a download for everything else. Text is the
// bounded initial portion the member serves; the full file is always one
// download away. Nothing here deserializes a file.
import { useQuery } from "@tanstack/react-query";
import { Download, RefreshCw, X } from "lucide-react";
import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { Button, ToggleButton, ToggleButtonGroup } from "react-aria-components";
import {
  fetchWorkspaceStat,
  fetchWorkspaceText,
  workspaceFileUrl,
  type WorkspaceEntry,
} from "../api/client";
import { useRunId } from "../app/runId";
import { MarkdownSurface } from "../components/MarkdownSurface";
import { fmtBytes, fmtClock } from "../format";
import { SourceView } from "./SourceView";

const BINARY_EXTENSIONS = new Set([
  ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff", ".tif",
  ".mp3", ".mp4", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".webm", ".avi", ".mov", ".mkv",
  ".zip", ".gz", ".bz2", ".xz", ".zst", ".tar", ".7z", ".rar",
  ".whl", ".egg", ".jar", ".war",
  ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".o", ".a",
  ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
  ".pkl", ".pickle", ".npy", ".npz", ".pt", ".pth", ".safetensors", ".gguf", ".onnx",
  ".h5", ".hdf5", ".parquet", ".arrow", ".feather",
  ".woff", ".woff2", ".ttf", ".otf", ".eot",
  ".DS_Store",
]);

function extOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

const PdfViewer = lazy(() => import("./PdfViewer").then((m) => ({ default: m.PdfViewer })));

const POLL_MS = 3000;

function DownloadLink(props: { path: string }): ReactNode {
  const runId = useRunId();
  return (
    <a
      className="ws-action"
      href={workspaceFileUrl(runId, "download", props.path)}
      download
    >
      <Download size={13} strokeWidth={1.75} aria-hidden />
      Download
    </a>
  );
}

function MetadataCard(props: { entry: WorkspaceEntry; note: string }): ReactNode {
  const { entry } = props;
  return (
    <dl className="ws-meta">
      <dt>name</dt>
      <dd className="mono">{entry.name}</dd>
      <dt>path</dt>
      <dd className="mono">{entry.path}</dd>
      <dt>size</dt>
      <dd className="num">{entry.size === null ? "—" : fmtBytes(entry.size)}</dd>
      <dt>modified</dt>
      <dd className="num">
        {entry.modified_at_unix === null ? "—" : fmtClock(entry.modified_at_unix)}
      </dd>
      <dt>type</dt>
      <dd className="mono">{entry.mime_type ?? entry.kind}</dd>
      <dt>preview</dt>
      <dd>{props.note}</dd>
    </dl>
  );
}

function TextBody(props: { path: string; runLive: boolean; markdown: boolean }): ReactNode {
  const runId = useRunId();
  const [view, setView] = useState<"rendered" | "raw">("rendered");
  const text = useQuery({
    queryKey: ["workspace-text", runId, props.path],
    queryFn: () => fetchWorkspaceText(runId, props.path),
    refetchInterval: props.runLive ? POLL_MS : false,
    retry: false,
  });
  if (text.isPending) return <p className="quiet ws-empty">reading file…</p>;
  if (text.isError)
    return <p className="quiet ws-empty">file unavailable — it may have been removed or replaced</p>;
  const data = text.data;
  const raw = !props.markdown || view === "raw";
  return (
    <>
      {(props.markdown || data.truncated) && (
        <div className="ws-preview-tools">
          {props.markdown && (
            <ToggleButtonGroup
              selectionMode="single"
              disallowEmptySelection
              selectedKeys={[view]}
              onSelectionChange={(keys) => {
                const [key] = keys;
                if (key === "rendered" || key === "raw") setView(key);
              }}
              className="ws-toggle"
            >
              <ToggleButton id="rendered">Rendered</ToggleButton>
              <ToggleButton id="raw">Raw</ToggleButton>
            </ToggleButtonGroup>
          )}
          {data.truncated && (
            <span className="ws-truncated">
              showing the first {fmtBytes(data.returned_bytes)} of {fmtBytes(data.size)} — download for the whole file
            </span>
          )}
        </div>
      )}
      {raw ? (
        <SourceView text={data.text} language={data.language} />
      ) : (
        <MarkdownSurface markdown={data.text} />
      )}
    </>
  );
}

function DocumentBody(props: { entry: WorkspaceEntry; etag: string }): ReactNode {
  const runId = useRunId();
  // The shown revision is pinned: stat polling may learn that the file
  // changed, but the document's bytes are re-requested only when the
  // user asks. A run that keeps writing a PDF must not reset the
  // browser's viewer every few seconds.
  const [shown, setShown] = useState({ etag: props.etag, attempt: 0 });
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [shown]);
  const newer = props.etag !== shown.etag;
  const url = `${workspaceFileUrl(runId, "raw", props.entry.path)}&rev=${encodeURIComponent(shown.etag)}&try=${shown.attempt}`;
  const retry = (
    <>
      {newer && <span className="ws-newer">newer revision available</span>}
      <Button
        className="ws-action"
        onPress={() => setShown((s) => ({ etag: props.etag, attempt: s.attempt + 1 }))}
      >
        <RefreshCw size={13} strokeWidth={1.75} aria-hidden />
        Reload
      </Button>
    </>
  );
  if (props.entry.preview_kind === "image")
    return (
      <div className="ws-document">
        <div className="ws-preview-tools">{retry}</div>
        {failed ? (
          <p className="quiet ws-empty">image could not be decoded yet — it may still be written</p>
        ) : (
          <img
            className="ws-image"
            src={url}
            alt={props.entry.name}
            onError={() => setFailed(true)}
          />
        )}
      </div>
    );
  return (
    <div className="ws-document">
      <div className="ws-preview-tools">{retry}</div>
      <Suspense fallback={<p className="quiet ws-empty">loading PDF viewer…</p>}>
        <PdfViewer url={url} />
      </Suspense>
    </div>
  );
}

export function FilePreview(props: {
  path: string;
  runLive: boolean;
  onClose: () => void;
}): ReactNode {
  const runId = useRunId();
  const stat = useQuery({
    queryKey: ["workspace-stat", runId, props.path],
    queryFn: () => fetchWorkspaceStat(runId, props.path),
    refetchInterval: props.runLive ? POLL_MS : false,
    retry: false,
  });
  const entry = stat.data?.entry;
  let body: ReactNode;
  if (stat.isPending) body = <p className="quiet ws-empty">reading file…</p>;
  else if (stat.isError || entry === undefined)
    body = <p className="quiet ws-empty">file unavailable — it may have been removed or replaced</p>;
  else if (entry.kind !== "file")
    body = (
      <MetadataCard
        entry={entry}
        note={
          entry.kind === "symlink"
            ? "a symlink is never followed, previewed, or downloaded"
            : entry.kind === "directory"
              ? "a directory; open it in the tree"
              : "a special entry (socket, device, or pipe) is never opened"
        }
      />
    );
  else if (entry.preview_kind === "code" || entry.preview_kind === "text")
    body = <TextBody path={props.path} runLive={props.runLive} markdown={false} />;
  else if (entry.preview_kind === "markdown")
    body = <TextBody path={props.path} runLive={props.runLive} markdown />;
  else if (entry.preview_kind === "pdf" || entry.preview_kind === "image")
    body = <DocumentBody key={entry.path} entry={entry} etag={stat.data.etag} />;
  else if (entry.preview_kind === "download_only" && !BINARY_EXTENSIONS.has(extOf(entry.name)))
    body = <TextBody path={props.path} runLive={props.runLive} markdown={false} />;
  else
    body = (
      <MetadataCard
        entry={entry}
        note={
          props.runLive
            ? "download only; the run is live, so this file may still change"
            : "download only"
        }
      />
    );
  const name = props.path.split("/").at(-1) ?? props.path;
  return (
    <section className="ws-preview" aria-label={`File ${name}`}>
      <header className="ws-preview-header">
        <span className="ws-preview-name mono" title={props.path}>
          {name}
        </span>
        {entry !== undefined && entry.kind === "file" && <DownloadLink path={props.path} />}
        <Button
          className="inspector-close"
          aria-label="Close file preview"
          onPress={props.onClose}
        >
          <X size={15} strokeWidth={1.75} aria-hidden />
        </Button>
      </header>
      <div className="ws-preview-body">{body}</div>
    </section>
  );
}
