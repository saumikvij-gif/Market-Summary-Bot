"use client";

// Market Summary tab — a self-contained App Router page.
// Client component per the site architecture: reads Supabase directly
// (or bundled sample data when NEXT_PUBLIC_SUPABASE_* are unset).

import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchAvailableDates, fetchDay, fetchTrend, isSampleMode, pdfUrl } from "./lib/data";
import type { DayBriefing, TrendPoint } from "./lib/types";
import DateNav from "./components/DateNav";
import ToneCard from "./components/ToneCard";
import TrendChart from "./components/TrendChart";
import Markdown from "./components/Markdown";
import SectorWatchTable from "./components/SectorWatchTable";
import PositioningTable from "./components/PositioningTable";
import SnapshotTables from "./components/SnapshotTables";
import GainersNews from "./components/GainersNews";
import styles from "./page.module.css";

export default function MarketSummaryPage() {
  const [dates, setDates] = useState<string[]>([]);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [day, setDay] = useState<DayBriefing | null>(null);
  const [loadingDay, setLoadingDay] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Initial load: available dates + trend series. Honour ?date= if present.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [ds, tr] = await Promise.all([fetchAvailableDates(), fetchTrend()]);
        if (cancelled) return;
        setDates(ds);
        setTrend(tr);
        const param = new URLSearchParams(window.location.search).get("date");
        setSelected(param && ds.includes(param) ? param : ds[0] ?? null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Load the briefing whenever the selected date changes. The previous render
  // is held at reduced opacity while the new one loads (no layout jump).
  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setLoadingDay(true);
    fetchDay(selected)
      .then((d) => { if (!cancelled) { setDay(d); setError(null); } })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoadingDay(false); });
    return () => { cancelled = true; };
  }, [selected]);

  const select = useCallback((date: string) => {
    setSelected(date);
    const u = new URL(window.location.href);
    u.searchParams.set("date", date);
    window.history.replaceState(null, "", u.toString());
  }, []);

  const prevScore = useMemo(() => {
    if (!selected) return null;
    const i = trend.findIndex((p) => p.run_date === selected);
    return i > 0 ? trend[i - 1].score : null;
  }, [trend, selected]);

  const pretty = useMemo(
    () => (selected
      ? new Date(`${selected}T00:00:00`).toLocaleDateString("en-US",
          { year: "numeric", month: "long", day: "numeric" })
      : ""),
    [selected],
  );

  return (
    <div className={styles.root}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Daily Market Summary</h1>
          <p className={styles.subtitle}>
            {pretty || "…"}
            {isSampleMode && <span className={styles.badge}>sample data</span>}
          </p>
        </div>
        <div className={styles.headerRight}>
          <DateNav dates={dates} selected={selected} onSelect={select} />
          {selected && (
            <a className={styles.pdfLink} href={pdfUrl(selected)}
               target="_blank" rel="noopener noreferrer">
              PDF ↓
            </a>
          )}
        </div>
      </header>

      {error && <div className={styles.error}>Could not load data: {error}</div>}

      {!error && (
        <div className={loadingDay ? styles.dimmed : undefined}>
          <div className={styles.toneRow}>
            <ToneCard summary={day?.summary ?? null} prevScore={prevScore} />
            <section className={styles.card}>
              <h2 className={styles.cardTitle}>Sentiment trend</h2>
              <TrendChart data={trend} selected={selected} onSelect={select} />
            </section>
          </div>

          {day?.sectorWatch?.length ? (
            <section className={styles.card}>
              <h2 className={styles.cardTitle}>Sector Watch (AI stack)</h2>
              <SectorWatchTable rows={day.sectorWatch} />
            </section>
          ) : null}

          {day?.positioning?.length ? (
            <section className={styles.card}>
              <h2 className={styles.cardTitle}>
                Positioning &amp; Regime{" "}
                <span className={styles.tag}>under evaluation</span>
              </h2>
              <PositioningTable rows={day.positioning} />
            </section>
          ) : null}

          {day?.quotes?.length ? (
            <section className={styles.card}>
              <h2 className={styles.cardTitle}>Market snapshot</h2>
              <SnapshotTables quotes={day.quotes} />
            </section>
          ) : null}

          {day?.bloomberg?.summary_md ? (
            <section className={styles.card}>
              <h2 className={styles.cardTitle}>
                Bloomberg News Summary{" "}
                <span className={styles.tag}>
                  {day.bloomberg.mode === "transcript"
                    ? "full-transcript summary"
                    : "episode rundown"}
                </span>
              </h2>
              <p className={styles.caption}>
                {day.bloomberg.title ?? "The Close"}
                {day.bloomberg.published ? ` · aired ${day.bloomberg.published}` : ""}
                {day.bloomberg.url && (
                  <>
                    {" · "}
                    <a href={day.bloomberg.url} target="_blank" rel="noopener noreferrer">
                      watch on YouTube
                    </a>
                  </>
                )}
              </p>
              <Markdown source={day.bloomberg.summary_md} />
            </section>
          ) : null}

          <GainersNews gainers={day?.gainers ?? []} news={day?.news ?? []} />

          {day?.summary?.summary_md ? (
            <section className={styles.card}>
              <h2 className={styles.cardTitle}>Analyst briefing</h2>
              <Markdown source={day.summary.summary_md} />
            </section>
          ) : null}

          {!day?.summary && !loadingDay && selected && (
            <div className={styles.empty}>No briefing stored for {selected}.</div>
          )}
        </div>
      )}
    </div>
  );
}
