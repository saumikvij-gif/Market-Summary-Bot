"use client";

// Minimal, dependency-free markdown renderer for the briefing prose. Covers
// exactly what the bot emits: ##/### headings, paragraphs, - lists,
// > blockquotes, | pipe tables, **bold**, and [text](url) links. Everything is
// rendered through React elements (auto-escaped) — never innerHTML — and raw
// HTML tags in the source (e.g. <sub>) are stripped, so untrusted headline text
// stays inert. Swap for react-markdown later if richer md ever appears.

import React from "react";
import styles from "../page.module.css";

const LINK_OR_BOLD = /(\*\*[^*]+\*\*|\[[^\]]+\]\([^)\s]+\))/g;

function inline(text: string, key: number): React.ReactNode {
  const clean = text.replace(/<[^>]+>/g, "");           // strip raw html tags
  const parts = clean.split(LINK_OR_BOLD).filter(Boolean);
  return (
    <React.Fragment key={key}>
      {parts.map((p, i) => {
        if (p.startsWith("**") && p.endsWith("**")) {
          return <strong key={i}>{p.slice(2, -2)}</strong>;
        }
        const m = p.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
        if (m) {
          return (
            <a key={i} href={m[2]} target="_blank" rel="noopener noreferrer">
              {m[1]}
            </a>
          );
        }
        return p;
      })}
    </React.Fragment>
  );
}

const isSeparatorRow = (line: string) =>
  /^\|?[\s:-]+\|[\s|:-]*$/.test(line) && line.includes("-");

function cells(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((c) => c.trim());
}

export default function Markdown({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const out: React.ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) { i += 1; continue; }

    if (line.startsWith("### ")) {
      out.push(<h4 key={key++} className={styles.mdH4}>{inline(line.slice(4), 0)}</h4>);
      i += 1;
    } else if (line.startsWith("## ")) {
      out.push(<h3 key={key++} className={styles.mdH3}>{inline(line.slice(3), 0)}</h3>);
      i += 1;
    } else if (line.startsWith("# ")) {
      out.push(<h3 key={key++} className={styles.mdH3}>{inline(line.slice(2), 0)}</h3>);
      i += 1;
    } else if (line.trimStart().startsWith("> ")) {
      const quote: string[] = [];
      while (i < lines.length && lines[i].trimStart().startsWith("> ")) {
        quote.push(lines[i].trimStart().slice(2));
        i += 1;
      }
      out.push(
        <blockquote key={key++} className={styles.mdQuote}>
          {inline(quote.join(" "), 0)}
        </blockquote>,
      );
    } else if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i += 1;
      }
      out.push(
        <ul key={key++} className={styles.mdList}>
          {items.map((it, j) => <li key={j}>{inline(it, 0)}</li>)}
        </ul>,
      );
    } else if (line.trimStart().startsWith("|")) {
      const rows: string[] = [];
      while (i < lines.length && lines[i].trimStart().startsWith("|")) {
        rows.push(lines[i]);
        i += 1;
      }
      const hasHeader = rows.length > 1 && isSeparatorRow(rows[1]);
      const header = hasHeader ? cells(rows[0]) : null;
      const body = (hasHeader ? rows.slice(2) : rows).filter((r) => !isSeparatorRow(r));
      out.push(
        <div key={key++} className={styles.tableScroll}>
          <table className={styles.table}>
            {header && (
              <thead>
                <tr>{header.map((h, j) => <th key={j}>{inline(h, 0)}</th>)}</tr>
              </thead>
            )}
            <tbody>
              {body.map((r, j) => (
                <tr key={j}>{cells(r).map((c, k) => <td key={k}>{inline(c, 0)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
    } else {
      const para: string[] = [line];
      i += 1;
      while (
        i < lines.length && lines[i].trim() &&
        !/^(#{1,3} |\s*[-*]\s+|\s*>|\s*\|)/.test(lines[i])
      ) {
        para.push(lines[i]);
        i += 1;
      }
      out.push(<p key={key++} className={styles.mdP}>{inline(para.join(" "), 0)}</p>);
    }
  }

  return <div className={styles.markdown}>{out}</div>;
}
