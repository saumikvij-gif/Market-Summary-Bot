"use client";

import styles from "../page.module.css";
import type { SectorWatchRow } from "../lib/types";

function Signed({ v, dp = 2, suffix = "%" }: { v: number | null; dp?: number; suffix?: string }) {
  if (v === null || v === undefined) return <span>n/a</span>;
  const cls = v > 0 ? styles.pos : v < 0 ? styles.neg : undefined;
  return (
    <span className={cls}>
      {v.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp, signDisplay: "always" })}
      {suffix}
    </span>
  );
}

export default function SectorWatchTable({ rows }: { rows: SectorWatchRow[] }) {
  const sorted = [...rows].sort((a, b) => (b.score ?? -9) - (a.score ?? -9));
  return (
    <>
      <p className={styles.caption}>
        Score describes the day — driven by the session move, with breadth and news
        for texture. Relative strength (vs Nasdaq for tech baskets, S&amp;P for the
        rest) is context only. Sorted strongest → weakest.
      </p>
      <div className={styles.tableScroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Sector</th><th>Move</th><th>Rel. str.</th>
              <th>Breadth</th><th>News</th><th>Overall</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.sector}>
                <td>{r.sector}</td>
                <td><Signed v={r.move_pct} /></td>
                <td>
                  <Signed v={r.rel_strength} />
                  {r.benchmark && <span className={styles.muted}> vs {r.benchmark}</span>}
                </td>
                <td>{r.breadth_pct !== null ? `${r.breadth_pct}%` : "n/a"}</td>
                <td>{r.news_score !== null
                  ? r.news_score.toLocaleString("en-US", { minimumFractionDigits: 2, signDisplay: "always" })
                  : "n/a"}</td>
                <td>
                  <strong className={
                    (r.score ?? 0) > 0.005 ? styles.pos : (r.score ?? 0) < -0.005 ? styles.neg : undefined
                  }>
                    {r.label ?? "—"}
                    {r.score !== null &&
                      ` (${r.score.toLocaleString("en-US", { minimumFractionDigits: 2, signDisplay: "always" })})`}
                  </strong>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
