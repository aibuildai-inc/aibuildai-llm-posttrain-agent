// One lazily created Shiki highlighter for the whole page, built from the
// fine-grained core so only the languages this product actually emits or
// the workspace browser previews (output/web/workspace.py LANGUAGES) are
// bundled. The theme is CSS-variables-only: every token color resolves to
// an --aibuildai-code-* variable that markdown.css maps onto the project
// palette, so no third-party color scheme ships as the product appearance.
import type { HighlighterCore } from "shiki/core";
import { createCssVariablesTheme, createHighlighterCore } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";

export const AIBUILDAI_SHIKI_THEME = "aibuildai";

const theme = createCssVariablesTheme({
  name: AIBUILDAI_SHIKI_THEME,
  variablePrefix: "--aibuildai-code-",
  fontStyle: true,
});

let highlighter: Promise<HighlighterCore> | null = null;

export function loadHighlighter(): Promise<HighlighterCore> {
  highlighter ??= createHighlighterCore({
    themes: [theme],
    langs: [
      import("@shikijs/langs/bash"),
      import("@shikijs/langs/python"),
      import("@shikijs/langs/json"),
      import("@shikijs/langs/yaml"),
      import("@shikijs/langs/markdown"),
      import("@shikijs/langs/javascript"),
      import("@shikijs/langs/typescript"),
      import("@shikijs/langs/tsx"),
      import("@shikijs/langs/diff"),
      import("@shikijs/langs/toml"),
      import("@shikijs/langs/html"),
      import("@shikijs/langs/xml"),
      import("@shikijs/langs/css"),
      import("@shikijs/langs/csv"),
      import("@shikijs/langs/ini"),
    ],
    engine: createJavaScriptRegexEngine({ forgiving: true }),
  });
  return highlighter;
}
