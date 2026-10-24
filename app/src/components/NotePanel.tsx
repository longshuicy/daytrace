import { openUrl } from "@tauri-apps/plugin-opener";
import { useEffect, useState, type ReactNode } from "react";
import { loadNote } from "../api";
import { clock } from "../format";
import type { SavedNote } from "../types";
import "./notepanel.css";

/** The saved copy of a Granola note, read from ~/.daytrace/notes/granola/. ‹ › (or the arrow keys) step through
 * the day's other notes, `ids` in time order. */
export function NotePanel({ id, ids, onOpen, onClose }:
  { id: string; ids: string[]; onOpen: (id: string) => void; onClose: () => void }) {
  const at = ids.indexOf(id);
  const step = (by: number) => at >= 0 && ids[at + by] && onOpen(ids[at + by]);
  const [note, setNote] = useState<SavedNote | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setNote(null);
    setError(null);
    loadNote(id).then(setNote, (e) => setError(String(e)));
  }, [id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft") step(-1);
      else if (e.key === "ArrowRight") step(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div className="note-backdrop" onClick={onClose}>
      <aside className="note-panel" role="dialog" aria-label="Meeting notes" onClick={(e) => e.stopPropagation()}>
        <header className="note-head">
          <div>
            <div className="note-kicker">Meeting notes{note && ` · ${clock(note.meeting_at)}`}</div>
            <h2>{note?.title ?? (error ? "Couldn't open the note" : "Loading…")}</h2>
          </div>
          <div className="note-actions">
            {ids.length > 1 && at >= 0 && (
              <span className="note-step tabular">
                <button aria-label="Previous note" disabled={at === 0} onClick={() => step(-1)}>‹</button>
                {at + 1} of {ids.length}
                <button aria-label="Next note" disabled={at === ids.length - 1} onClick={() => step(1)}>›</button>
              </span>
            )}
            {note?.url && <button className="link" onClick={() => openUrl(note.url!)}>Granola ↗</button>}
            <button className="note-close" aria-label="Close" onClick={onClose}>×</button>
          </div>
        </header>
        <div className="note-body">
          {error && <p className="note-error">{error}</p>}
          {note && (note.markdown ? markdown(note.markdown) : <p className="note-empty">This note is empty.</p>)}
        </div>
      </aside>
    </div>
  );
}

/** The Markdown Granola writes: headings, nested "-" lists, paragraphs, **bold** and [links](url). */
function markdown(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const lines = text.split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const heading = /^(#{1,6})\s+(.*)/.exec(line);
    if (!line.trim()) {
      i++;
    } else if (heading) {
      // "## Summary" / "## My notes" are daytrace's section labels; Granola's own headings sit inside them.
      out.push(heading[1].length === 2
        ? <div key={i} className="note-section">{inline(heading[2])}</div>
        : <h3 key={i}>{inline(heading[2])}</h3>);
      i++;
    } else if (/^\s*[-*]\s+/.test(line)) {
      const start = i;
      const items: { depth: number; text: string }[] = [];
      while (i < lines.length && (/^\s*[-*]\s+/.test(lines[i]) || (lines[i].trim() && /^\s+/.test(lines[i])))) {
        const m = /^(\s*)[-*]\s+(.*)/.exec(lines[i]);
        if (m) items.push({ depth: Math.floor(m[1].length / 2), text: m[2] });
        else items[items.length - 1].text += " " + lines[i].trim();  // a wrapped item
        i++;
      }
      out.push(<ul key={start}>{tree(items, 0, { i: 0 })}</ul>);
    } else {
      const start = i;
      const para: string[] = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*[-*]\s)/.test(lines[i])) para.push(lines[i++].trim());
      out.push(<p key={start}>{inline(para.join(" "))}</p>);
    }
  }
  return out;
}

function tree(items: { depth: number; text: string }[], depth: number, at: { i: number }): ReactNode[] {
  const out: ReactNode[] = [];
  while (at.i < items.length && items[at.i].depth >= depth) {
    const item = items[at.i++];
    const children = at.i < items.length && items[at.i].depth > item.depth ? tree(items, item.depth + 1, at) : [];
    out.push(<li key={at.i}>{inline(item.text)}{children.length > 0 && <ul>{children}</ul>}</li>);
  }
  return out;
}

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|\[([^\]]+)\]\((https?:[^)\s]+)\)/g;
  let last = 0;
  for (let m; (m = re.exec(text));) {
    out.push(unescape(text.slice(last, m.index)));
    const url = m[3];
    out.push(m[1] !== undefined
      ? <strong key={m.index}>{unescape(m[1])}</strong>
      : <button key={m.index} className="link" onClick={() => openUrl(url)}>{unescape(m[2])}</button>);
    last = re.lastIndex;
  }
  out.push(unescape(text.slice(last)));
  return out;
}

/** Markdown backslash escapes, e.g. Granola's "\[Workspace Name\]". */
const unescape = (s: string) => s.replace(/\\([\\`*_{}[\]()#+\-.!>|])/g, "$1");
