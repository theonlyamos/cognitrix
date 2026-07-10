import { useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';

// Fenced code block with a hover copy button. Reads the rendered text from the
// DOM so it works regardless of the syntax-highlight token spans inside.
function CodeBlock({ children }: { children?: ReactNode }) {
  const ref = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);
  const copy = () => {
    const text = ref.current?.innerText ?? '';
    void navigator.clipboard?.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };
  return (
    <div className="md-code">
      <button type="button" className="md-copy" onClick={copy}>{copied ? 'copied' : 'copy'}</button>
      <pre ref={ref}>{children}</pre>
    </div>
  );
}

// Route internal links (/tasks/… — multi-step replies carry a run link) through
// the SPA router; open external links in a new tab safely.
const mdComponents: Components = {
  a({ href, children }) {
    if (href && href.startsWith('/')) {
      return <Link to={href} className="text-accent-ink underline underline-offset-2 hover:brightness-110">{children}</Link>;
    }
    return (
      <a href={href} target="_blank" rel="noreferrer nofollow" className="text-accent-ink underline underline-offset-2 hover:brightness-110">
        {children}
      </a>
    );
  },
  pre({ children }) {
    return <CodeBlock>{children}</CodeBlock>;
  },
};

export default function MarkdownMessage({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
      components={mdComponents}
    >
      {content}
    </ReactMarkdown>
  );
}
