"use client";

import styles from "../page.module.css";
import type { SummaryRow } from "../lib/types";

interface Props {
  summary: SummaryRow | null;
  prevScore: number | null;    // previous trading day's score (-100..100)
}

/** Stat tile: the day's market tone. Label · value · delta vs previous day. */
export default function ToneCard({ summary, prevScore }: Props) {
  const score = summary?.score ?? null;
  const delta = score !== null && prevScore !== null ? score - prevScore : null;
  const dir = (v: number) => (v > 0 ? styles.pos : v < 0 ? styles.neg : undefined);

  return (
    <section className={`${styles.card} ${styles.toneCard}`}>
      <div className={styles.statLabel}>Market tone</div>
      <div className={styles.statValue}>
        {score !== null && (
          <span className={`${styles.toneDot} ${dir(score) ?? ""}`} aria-hidden />
        )}
        {summary?.sentiment ?? "—"}
      </div>
      <div className={styles.statSub}>
        {score !== null ? (
          <>
            score{" "}
            <strong className={dir(score)}>
              {(score / 100).toLocaleString("en-US",
                { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "always" })}
            </strong>
            {delta !== null && (
              <>
                {" · vs prev "}
                <span className={dir(delta)}>
                  {(delta / 100).toLocaleString("en-US",
                    { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "always" })}
                </span>
              </>
            )}
          </>
        ) : (
          "no score recorded"
        )}
      </div>
      <p className={styles.statNote}>
        A recap of how the market traded that day — not a forecast.
      </p>
    </section>
  );
}
