import { type ReactElement, type ReactNode } from "react";

import { escape_html_entities } from "../../lib/escape-html-entities";

function render_rich_segment(segment: string, base_key: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let index = 0;
  while ((match = re.exec(segment)) !== null) {
    const plain_before = segment.slice(last, match.index);
    if (plain_before) {
      nodes.push(
        <span key={`${base_key}-plain-${index}`}>
          {escape_html_entities(plain_before)}
        </span>,
      );
    }
    const token = match[1];
    if (token.startsWith("**") && token.endsWith("**")) {
      nodes.push(
        <strong key={`${base_key}-bold-${index}`}>
          {escape_html_entities(token.slice(2, -2))}
        </strong>,
      );
    } else if (token.startsWith("`") && token.endsWith("`")) {
      nodes.push(
        <code
          key={`${base_key}-code-${index}`}
          className="font-data rounded bg-[var(--bg-overlay)] px-1 text-[12px]"
        >
          {escape_html_entities(token.slice(1, -1))}
        </code>,
      );
    }
    last = match.index + token.length;
    index += 1;
  }
  const tail = segment.slice(last);
  if (tail) {
    nodes.push(
      <span key={`${base_key}-tail`}>{escape_html_entities(tail)}</span>,
    );
  }
  return nodes;
}

function render_inline_with_citations(text: string, block_key: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const citation_re = /\[(\d+)\]/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let index = 0;
  while ((match = citation_re.exec(text)) !== null) {
    const before = text.slice(last, match.index);
    nodes.push(
      <span key={`${block_key}-pre-${index}`}>
        {render_rich_segment(before, `${block_key}-inner-${index}`)}
      </span>,
    );
    const cite_id = match[1];
    nodes.push(
      <sup key={`${block_key}-sup-${cite_id}-${index}`}>
        <a
          href={`#omnibox-source-${cite_id}`}
          className="text-[var(--accent-cyan)] underline-offset-2 hover:underline focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-cyan)]"
          aria-label={`Source ${cite_id}`}
        >
          [{cite_id}]
        </a>
      </sup>,
    );
    last = match.index + match[0].length;
    index += 1;
  }
  nodes.push(
    <span key={`${block_key}-post`}>
      {render_rich_segment(text.slice(last), `${block_key}-tail`)}
    </span>,
  );
  return nodes;
}

export interface OmniboxMarkdownBodyProps {
  text: string;
}

function MarkdownBlock(props: { base_key: string; text: string }): ReactElement {
  const lines = props.text.split("\n");
  const elements: ReactElement[] = [];
  const bullet_lines: string[] = [];

  const flush_bullets = (): void => {
    if (bullet_lines.length === 0) {
      return;
    }
    elements.push(
      <ul
        key={`${props.base_key}-ul-${elements.length}`}
        className="list-disc space-y-1 pl-4"
      >
        {bullet_lines.map((item, bullet_index) => (
          <li key={`${props.base_key}-li-${bullet_index}`}>
            {render_inline_with_citations(
              item,
              `${props.base_key}-lic-${bullet_index}`,
            )}
          </li>
        ))}
      </ul>,
    );
    bullet_lines.length = 0;
  };

  for (let line_index = 0; line_index < lines.length; line_index++) {
    const trimmed = lines[line_index]?.trim() ?? "";
    if (!trimmed) {
      continue;
    }
    if (trimmed.startsWith("- ")) {
      bullet_lines.push(trimmed.slice(2));
      continue;
    }
    flush_bullets();
    if (trimmed.startsWith("## ")) {
      elements.push(
        <h3
          key={`${props.base_key}-h3-${line_index}`}
          className="display text-base font-semibold text-[var(--text-primary)]"
        >
          {render_inline_with_citations(
            trimmed.slice(3),
            `${props.base_key}-h3c-${line_index}`,
          )}
        </h3>,
      );
    } else if (trimmed.startsWith("# ")) {
      elements.push(
        <h2
          key={`${props.base_key}-h2-${line_index}`}
          className="display text-lg font-semibold text-[var(--text-primary)]"
        >
          {render_inline_with_citations(
            trimmed.slice(2),
            `${props.base_key}-h2c-${line_index}`,
          )}
        </h2>,
      );
    } else {
      elements.push(
        <p key={`${props.base_key}-p-${line_index}`}>
          {render_inline_with_citations(
            trimmed,
            `${props.base_key}-pc-${line_index}`,
          )}
        </p>,
      );
    }
  }
  flush_bullets();

  return <div className="space-y-2">{elements}</div>;
}

export function OmniboxMarkdownBody(props: OmniboxMarkdownBodyProps): ReactElement {
  const blocks = props.text.split(/\n\n+/).filter((block) => block.trim().length > 0);
  return (
    <div className="space-y-3 text-sm leading-relaxed text-[var(--text-primary)]">
      {blocks.map((block, block_index) => (
        <MarkdownBlock key={`blk-${block_index}`} base_key={`b-${block_index}`} text={block} />
      ))}
    </div>
  );
}
