"use client";

import styles from "../page.module.css";
import type { QuoteRow } from "../lib/types";

const SECTION_LABELS: Record<string, string> = {
  indices: "Major indices",
  stocks: "Key stocks",
  sectors: "Sector ETFs",
  commodities: "Commodities & crypto",
  fx: "FX rates",
  rates: "Interest rates",
};
const ORDER = ["indices", "stocks", "sectors", "commodities", "fx", "rates"];

export default function SnapshotTables({ quotes }: { quotes: QuoteRow[] }) {
  const bySection = new Map<string, QuoteRow[]>();
  for (const q of quotes) {
    const list = bySection.get(q.section) ?? [];
    list.push(q);
    bySection.set(q.section, list);
  }
  const sections = ORDER.filter((s) => bySection.has(s))
    .concat([...bySection.keys()].filter((s) => !ORDER.includes(s)));

  return (
    <div className={styles.snapshotGrid}>
      {sections.map((s) => (
        <div key={s}>
          <h3 className={styles.snapshotHeading}>{SECTION_LABELS[s] ?? s}</h3>
          <div className={styles.tableScroll}>
            <table className={styles.table}>
              <thead>
                <tr><th>Instrument</th><th>Price</th><th>Change</th><th>%</th></tr>
              </thead>
              <tbody>
                {(bySection.get(s) ?? []).map((q) => (
                  <tr key={q.name}>
                    <td>{q.name}</td>
                    <td>{q.price !== null
                      ? q.price.toLocaleString("en-US",
                          { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                      : "n/a"}</td>
                    <td>
                      {q.change !== null ? (
                        <span className={q.change > 0 ? styles.pos : q.change < 0 ? styles.neg : undefined}>
                          {q.change.toLocaleString("en-US",
                            { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "always" })}
                        </span>
                      ) : "n/a"}
                    </td>
                    <td>
                      {q.pct_change !== null ? (
                        <span className={q.pct_change > 0 ? styles.pos : q.pct_change < 0 ? styles.neg : undefined}>
                          {q.pct_change.toLocaleString("en-US",
                            { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "always" })}%
                        </span>
                      ) : "n/a"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}
