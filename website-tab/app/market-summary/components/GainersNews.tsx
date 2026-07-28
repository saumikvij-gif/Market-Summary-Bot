"use client";

import styles from "../page.module.css";
import type { GainerRow, NewsRow } from "../lib/types";

export default function GainersNews({ gainers, news }: { gainers: GainerRow[]; news: NewsRow[] }) {
  if (!gainers.length && !news.length) return null;
  return (
    <div className={styles.twoCol}>
      {gainers.length > 0 && (
        <section className={styles.card}>
          <h2 className={styles.cardTitle}>Top gainers</h2>
          <div className={styles.tableScroll}>
            <table className={styles.table}>
              <thead>
                <tr><th>Company</th><th>Price</th><th>%</th></tr>
              </thead>
              <tbody>
                {gainers.map((g) => (
                  <tr key={g.rank}>
                    <td>{g.name ?? g.symbol}{g.symbol && g.name ? ` (${g.symbol})` : ""}</td>
                    <td>{g.price !== null
                      ? g.price.toLocaleString("en-US",
                          { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                      : "n/a"}</td>
                    <td>
                      {g.pct_change !== null ? (
                        <span className={styles.pos}>
                          {g.pct_change.toLocaleString("en-US",
                            { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "always" })}%
                        </span>
                      ) : "n/a"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {news.length > 0 && (
        <section className={styles.card}>
          <h2 className={styles.cardTitle}>Top news</h2>
          <ul className={styles.newsList}>
            {news.map((n) => (
              <li key={n.rank} className={styles.newsItem}>
                <span className={styles.newsTitle}>{n.title}</span>
                {n.source && <span className={styles.muted}> — {n.source}</span>}
                {n.summary && <p className={styles.newsSummary}>{n.summary}</p>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
