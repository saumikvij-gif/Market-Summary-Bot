"use client";

import styles from "../page.module.css";

interface Props {
  dates: string[];          // newest first
  selected: string | null;
  onSelect: (date: string) => void;
}

/** Date picker: a select of every available briefing date + prev/next arrows. */
export default function DateNav({ dates, selected, onSelect }: Props) {
  const i = selected ? dates.indexOf(selected) : -1;
  const newer = i > 0 ? dates[i - 1] : null;
  const older = i >= 0 && i < dates.length - 1 ? dates[i + 1] : null;

  return (
    <div className={styles.dateNav}>
      <button type="button" className={styles.navBtn} disabled={!older}
              onClick={() => older && onSelect(older)} aria-label="Previous trading day">
        ‹
      </button>
      <select
        className={styles.dateSelect}
        value={selected ?? ""}
        onChange={(e) => onSelect(e.target.value)}
        aria-label="Briefing date"
        disabled={!dates.length}
      >
        {!dates.length && <option value="">Loading…</option>}
        {dates.map((d) => (
          <option key={d} value={d}>{d}</option>
        ))}
      </select>
      <button type="button" className={styles.navBtn} disabled={!newer}
              onClick={() => newer && onSelect(newer)} aria-label="Next trading day">
        ›
      </button>
    </div>
  );
}
