"use client";

// Sentiment trend — single-series line chart (score -100..100 over trading
// days). Dependency-free inline SVG. Spec: 2px round-join line, hairline solid
// gridlines, stronger zero baseline, crosshair that snaps to the nearest date
// with a single tooltip (value leads, line-key in the series color), ≥8px
// ring-wrapped marker on the active point, keyboard equivalent (arrows/Enter),
// and a table view so no value is gated behind hover. Single series → no
// legend; the section title names it.

import { useCallback, useMemo, useRef, useState } from "react";
import styles from "../page.module.css";
import type { TrendPoint } from "../lib/types";

interface Props {
  data: TrendPoint[];                 // oldest first
  selected: string | null;
  onSelect: (date: string) => void;
}

const VB_W = 720;
const VB_H = 220;
const PAD = { left: 40, right: 14, top: 12, bottom: 26 };
const Y_TICKS = [-100, -50, 0, 50, 100];

export default function TrendChart({ data, selected, onSelect }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const plotW = VB_W - PAD.left - PAD.right;
  const plotH = VB_H - PAD.top - PAD.bottom;
  const x = useCallback(
    (i: number) => PAD.left + (data.length > 1 ? (i / (data.length - 1)) * plotW : plotW / 2),
    [data.length, plotW],
  );
  const y = useCallback(
    (score: number) => PAD.top + ((100 - score) / 200) * plotH,
    [plotH],
  );

  const path = useMemo(
    () => data.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.score).toFixed(1)}`).join(""),
    [data, x, y],
  );

  const selIdx = useMemo(
    () => (selected ? data.findIndex((p) => p.run_date === selected) : -1),
    [data, selected],
  );

  // Roughly five x-axis date labels, always including first and last.
  const xTicks = useMemo(() => {
    if (data.length < 2) return [];
    const step = Math.max(1, Math.round((data.length - 1) / 4));
    const idx = new Set<number>([0, data.length - 1]);
    for (let i = step; i < data.length - 1; i += step) idx.add(i);
    return [...idx].sort((a, b) => a - b);
  }, [data.length]);

  const nearestIndex = useCallback((clientX: number) => {
    const svg = svgRef.current;
    if (!svg || data.length === 0) return null;
    const r = svg.getBoundingClientRect();
    const vx = ((clientX - r.left) / r.width) * VB_W;
    const frac = (vx - PAD.left) / plotW;
    return Math.max(0, Math.min(data.length - 1, Math.round(frac * (data.length - 1))));
  }, [data.length, plotW]);

  const onKey = useCallback((e: React.KeyboardEvent) => {
    if (data.length === 0) return;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const cur = hover ?? (selIdx >= 0 ? selIdx : data.length - 1);
      const next = Math.max(0, Math.min(data.length - 1, cur + (e.key === "ArrowRight" ? 1 : -1)));
      setHover(next);
    } else if (e.key === "Enter" && hover !== null) {
      onSelect(data[hover].run_date);
    } else if (e.key === "Escape") {
      setHover(null);
    }
  }, [data, hover, selIdx, onSelect]);

  if (data.length < 2) {
    return <div className={styles.chartEmpty}>Not enough history yet for a trend.</div>;
  }

  const active = hover !== null ? data[hover] : null;
  const activeX = hover !== null ? x(hover) : 0;
  const tipLeftPct = (activeX / VB_W) * 100;
  const tipFlip = tipLeftPct > 62;

  return (
    <div className={styles.chartWrap}>
      <div
        className={styles.chartBox}
        role="application"
        aria-label="Sentiment trend chart. Use left and right arrow keys to inspect days; Enter opens that day's briefing."
        tabIndex={0}
        onKeyDown={onKey}
      >
        <svg
          ref={svgRef}
          viewBox={`0 0 ${VB_W} ${VB_H}`}
          className={styles.chartSvg}
          onPointerMove={(e) => setHover(nearestIndex(e.clientX))}
          onPointerLeave={() => setHover(null)}
          onClick={(e) => {
            const i = nearestIndex(e.clientX);
            if (i !== null) onSelect(data[i].run_date);
          }}
        >
          {/* gridlines + y ticks (clean numbers; zero baseline is stronger) */}
          {Y_TICKS.map((t) => (
            <g key={t}>
              <line
                x1={PAD.left} x2={VB_W - PAD.right} y1={y(t)} y2={y(t)}
                className={t === 0 ? styles.zeroLine : styles.gridLine}
              />
              <text x={PAD.left - 6} y={y(t) + 3} className={styles.axisText} textAnchor="end">
                {t > 0 ? `+${t}` : t}
              </text>
            </g>
          ))}
          {/* x date labels */}
          {xTicks.map((i) => (
            <text key={i} x={x(i)} y={VB_H - 8} className={styles.axisText} textAnchor="middle">
              {data[i].run_date.slice(5)}
            </text>
          ))}

          {/* crosshair */}
          {hover !== null && (
            <line x1={activeX} x2={activeX} y1={PAD.top} y2={PAD.top + plotH}
                  className={styles.crosshair} />
          )}

          {/* the series */}
          <path d={path} className={styles.seriesLine} />

          {/* selected day marker (ring-wrapped so it survives crossing the line) */}
          {selIdx >= 0 && (
            <circle cx={x(selIdx)} cy={y(data[selIdx].score)} r={4.5}
                    className={styles.markerSelected} />
          )}
          {hover !== null && hover !== selIdx && (
            <circle cx={activeX} cy={y(data[hover].score)} r={4.5}
                    className={styles.markerHover} />
          )}
        </svg>

        {active && (
          <div
            className={styles.tooltip}
            style={{
              left: `${tipLeftPct}%`,
              transform: tipFlip ? "translate(calc(-100% - 10px), 0)" : "translate(10px, 0)",
            }}
          >
            <span className={styles.tooltipKey} aria-hidden />
            <strong>
              {(active.score / 100).toLocaleString("en-US",
                { minimumFractionDigits: 2, signDisplay: "always" })}
            </strong>
            <span className={styles.tooltipDate}>{active.run_date}</span>
          </div>
        )}
      </div>

      {/* table view — every charted value reachable without hovering */}
      <details className={styles.tableView}>
        <summary>View as table</summary>
        <div className={styles.tableScroll}>
          <table className={styles.table}>
            <thead>
              <tr><th>Date</th><th>Score</th></tr>
            </thead>
            <tbody>
              {[...data].reverse().map((p) => (
                <tr key={p.run_date}>
                  <td>{p.run_date}</td>
                  <td>{(p.score / 100).toLocaleString("en-US",
                    { minimumFractionDigits: 2, signDisplay: "always" })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
