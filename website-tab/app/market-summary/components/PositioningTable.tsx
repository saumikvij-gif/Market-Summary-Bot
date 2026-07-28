"use client";

import styles from "../page.module.css";
import type { PositioningRow } from "../lib/types";

export default function PositioningTable({ rows }: { rows: PositioningRow[] }) {
  const sorted = [...rows].sort((a, b) => (b.otm_put_call ?? -9) - (a.otm_put_call ?? -9));
  return (
    <>
      <p className={styles.caption}>
        Was the day bullish or bearish, read through positioning: out-of-the-money
        put/call volume (hedging vs chasing) and short %-of-float (squeeze fuel)
        explain the character of the move. Logged daily; drives no scores.
      </p>
      <div className={styles.tableScroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Basket</th><th>OTM P/C</th><th>Short flt</th>
              <th>Day</th><th>Today&apos;s read</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.basket}>
                <td>{r.basket}</td>
                <td>{r.otm_put_call !== null ? r.otm_put_call.toFixed(2) : "n/a"}</td>
                <td>{r.short_pct_float !== null
                  ? `${(r.short_pct_float * 100).toFixed(1)}%` : "n/a"}</td>
                <td>
                  {r.r1_pct !== null ? (
                    <span className={r.r1_pct > 0 ? styles.pos : r.r1_pct < 0 ? styles.neg : undefined}>
                      {r.r1_pct.toLocaleString("en-US",
                        { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "always" })}%
                    </span>
                  ) : "n/a"}
                </td>
                <td>
                  <strong className={
                    r.today === "Bullish" ? styles.pos : r.today === "Bearish" ? styles.neg : undefined
                  }>
                    {r.today ?? "—"}
                  </strong>
                  {r.today_why && <span className={styles.muted}> — {r.today_why}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
