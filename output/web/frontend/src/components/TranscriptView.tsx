// The Agent transcript as settled on the rendered prototype:
// one card per turn; a time rail OUTSIDE the cards with one marker per
// card at the turn's start time; inside a card, prose, muted thinking
// folds, and one block per tool call (icon, name, note, input, result).
// One frame per thing: the card frames the turn, the block frames the
// call, and everything inside a block is flat sections split by rules.
// The typed turns come from the API (output/web/view.py reads the
// written transcript back); nothing here parses Markdown.
import {
  BookOpen,
  Brain,
  Pencil,
  Search,
  Terminal,
  TriangleAlert,
  Wrench,
} from "lucide-react";
import type { ReactNode } from "react";
import type {
  FoldBlock,
  TextBlock,
  ToolCallBlock,
  TranscriptResponse,
  TranscriptTurn,
} from "../api/client";
import { MarkdownSurface } from "./MarkdownSurface";

const ICON_SIZE = 14;

function ToolIcon(props: { name: string }): ReactNode {
  const tool = props.name.toLowerCase();
  if (tool === "bash") return <Terminal size={ICON_SIZE} aria-hidden />;
  if (tool === "read") return <BookOpen size={ICON_SIZE} aria-hidden />;
  if (tool === "edit" || tool === "write" || tool === "multiedit")
    return <Pencil size={ICON_SIZE} aria-hidden />;
  if (tool === "grep" || tool === "glob" || tool === "websearch")
    return <Search size={ICON_SIZE} aria-hidden />;
  return <Wrench size={ICON_SIZE} aria-hidden />;
}

function FoldBody(props: { block: FoldBlock }): ReactNode {
  return props.block.code.trim().length === 0 ? (
    <p className="quiet transcript-empty">empty</p>
  ) : (
    <MarkdownSurface markdown={"```" + props.block.lang + "\n" + props.block.code + "\n```"} />
  );
}

function Fold(props: { block: FoldBlock }): ReactNode {
  const { block } = props;
  const thinking = block.tag === "thinking";
  return (
    <details className={`transcript-fold${thinking ? " transcript-fold-muted" : ""}`}>
      <summary>
        {thinking && <Brain size={13} aria-hidden />}
        {block.tag === "WARN" && <TriangleAlert size={13} aria-hidden />}
        <span className="transcript-fold-title">{block.title}</span>
        <span className="transcript-fold-meta num">{block.meta}</span>
      </summary>
      <FoldBody block={block} />
    </details>
  );
}

// The input opens by itself when it is short; a long one (a file body, a
// big JSON) starts folded so the call's result stays within reach.
const OPEN_INPUT_LINES = 8;

function ToolCall(props: { block: ToolCallBlock }): ReactNode {
  const { block } = props;
  const result = block.result;
  const warn = result !== null && result.tag === "WARN";
  const inputLines = block.input_markdown.split("\n").length;
  return (
    <div className={`transcript-tool${warn ? " transcript-tool-warn" : ""}`}>
      <div className="transcript-tool-head">
        <span className="transcript-tool-icon">
          <ToolIcon name={block.name} />
        </span>
        <span className="transcript-tool-name">{block.name}</span>
        {block.note !== null && (
          <span className="transcript-tool-note">{block.note}</span>
        )}
      </div>
      {block.input_markdown.length > 0 && (
        <details className="transcript-tool-input" open={inputLines <= OPEN_INPUT_LINES}>
          <summary>
            <span className="transcript-fold-title">input</span>
            <span className="transcript-fold-meta num">{inputLines} lines</span>
          </summary>
          <MarkdownSurface markdown={block.input_markdown} />
        </details>
      )}
      {result !== null && (
        <details className="transcript-tool-result">
          <summary>
            <span className="transcript-fold-title">
              {warn ? result.title : "result"}
            </span>
            <span className="transcript-fold-meta num">{result.meta}</span>
          </summary>
          <FoldBody block={result} />
        </details>
      )}
    </div>
  );
}

function Text(props: { block: TextBlock }): ReactNode {
  const sub = props.block.source !== "parent";
  return (
    <div className={`transcript-text${sub ? " transcript-text-sub" : ""}`}>
      {sub && <span className="transcript-role">{props.block.source}</span>}
      <MarkdownSurface markdown={props.block.markdown} />
    </div>
  );
}

function fmtDuration(seconds: number): string {
  return seconds < 60
    ? `${seconds}s`
    : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

// The row of the selected query scrolls itself into view when it mounts.
function anchorSelected(node: HTMLDivElement | null): void {
  node?.scrollIntoView({ block: "start" });
}

function Turn(props: { turn: TranscriptTurn }): ReactNode {
  const { turn } = props;
  return (
    <div className="transcript-row" ref={turn.selected ? anchorSelected : undefined}>
      <div className="transcript-rail">
        <span className="transcript-rail-dot" />
        <span className="transcript-rail-ts num">
          {turn.start_ts === null ? "" : turn.start_ts.replace(/^00:/, "")}
        </span>
      </div>
      <section className="transcript-card">
        <header className="transcript-card-head">
          <span className="transcript-card-title">Turn {turn.index}</span>
          {turn.duration_s !== null && (
            <span className="transcript-card-span num">
              {fmtDuration(turn.duration_s)}
            </span>
          )}
          <span className="transcript-card-spacer" />
          {turn.tokens_in !== null && (
            <span className="transcript-chip num">{turn.tokens_in} in</span>
          )}
          {turn.tokens_out !== null && (
            <span className="transcript-chip num">{turn.tokens_out} out</span>
          )}
        </header>
        <div className="transcript-card-body">
          {turn.blocks.map((block, i) =>
            block.kind === "tool_call" ? (
              <ToolCall block={block} key={i} />
            ) : block.kind === "fold" ? (
              <Fold block={block} key={i} />
            ) : block.kind === "text" ? (
              <Text block={block} key={i} />
            ) : (
              <MarkdownSurface markdown={block.markdown} key={i} />
            ),
          )}
        </div>
      </section>
    </div>
  );
}

export function TranscriptView(props: { transcript: TranscriptResponse }): ReactNode {
  const { transcript } = props;
  return (
    <div className="transcript">
      {transcript.header_markdown.length > 0 && (
        <details className="transcript-fold transcript-fold-muted transcript-header">
          <summary>
            <span className="transcript-fold-title">session</span>
          </summary>
          <MarkdownSurface markdown={transcript.header_markdown} />
        </details>
      )}
      {transcript.turns.map((turn) => (
        <Turn turn={turn} key={turn.index} />
      ))}
    </div>
  );
}
