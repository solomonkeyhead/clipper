import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** An Ask answer as Markdown, links opening in a new tab. Its own file so the
 *  Markdown libraries load only when the first answer shows (AskPanel). */
export default function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]}
      components={{ a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">{children}</a> }}>
      {children}
    </ReactMarkdown>
  );
}
