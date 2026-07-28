// Data access for the Market Summary tab.
//
// Two modes, decided once at module load:
//   • LIVE — NEXT_PUBLIC_SUPABASE_URL + NEXT_PUBLIC_SUPABASE_ANON_KEY are set:
//     read the Supabase tables defined in ../../..//schema.sql.
//   • SAMPLE — env vars absent: serve the bundled sample-data.json (built from
//     the bot's real history CSVs) so the tab works before Supabase exists.
//     The JSON is dynamically imported, so live deployments never ship it.

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import type {
  BloombergRow, DayBriefing, GainerRow, NewsRow, PositioningRow, QuoteRow,
  SectorWatchRow, SummaryRow, TrendPoint,
} from "./types";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export const isSampleMode = !url || !anonKey;

let client: SupabaseClient | null = null;
function supabase(): SupabaseClient {
  if (!client) client = createClient(url as string, anonKey as string);
  return client;
}

interface SampleData {
  summaries: SummaryRow[];
  trend: TrendPoint[];
  quotes: QuoteRow[];
  sector_watch: SectorWatchRow[];
  positioning: PositioningRow[];
  bloomberg?: BloombergRow[];
  top_news: NewsRow[];
  gainers: GainerRow[];
}

let samplePromise: Promise<SampleData> | null = null;
function sample(): Promise<SampleData> {
  if (!samplePromise) {
    samplePromise = import("./sample-data.json").then(
      (m) => (m.default ?? m) as unknown as SampleData,
    );
  }
  return samplePromise;
}

/** All dates that have a briefing, newest first (drives the date picker). */
export async function fetchAvailableDates(): Promise<string[]> {
  if (isSampleMode) {
    const s = await sample();
    return s.summaries.map((r) => r.run_date).sort().reverse();
  }
  const { data, error } = await supabase()
    .from("summaries")
    .select("run_date")
    .order("run_date", { ascending: false })
    .limit(400);
  if (error) throw new Error(`summaries dates: ${error.message}`);
  return (data ?? []).map((r: { run_date: string }) => r.run_date);
}

/** Sentiment score history for the trend chart, oldest first. */
export async function fetchTrend(): Promise<TrendPoint[]> {
  if (isSampleMode) {
    const s = await sample();
    return [...s.trend].sort((a, b) => a.run_date.localeCompare(b.run_date));
  }
  const { data, error } = await supabase()
    .from("summaries")
    .select("run_date, score")
    .not("score", "is", null)
    .order("run_date", { ascending: true })
    .limit(400);
  if (error) throw new Error(`trend: ${error.message}`);
  return (data ?? []) as TrendPoint[];
}

/** The full briefing for one date. Missing sections come back as empty arrays. */
export async function fetchDay(date: string): Promise<DayBriefing> {
  if (isSampleMode) {
    const s = await sample();
    const by = <T extends { run_date: string }>(rows: T[]) =>
      rows.filter((r) => r.run_date === date);
    return {
      summary: s.summaries.find((r) => r.run_date === date) ?? null,
      quotes: by(s.quotes),
      sectorWatch: by(s.sector_watch)
        .sort((a, b) => (b.score ?? -9) - (a.score ?? -9)),
      positioning: by(s.positioning)
        .sort((a, b) => (b.otm_put_call ?? -9) - (a.otm_put_call ?? -9)),
      bloomberg: (s.bloomberg ?? []).find((r) => r.run_date === date) ?? null,
      news: by(s.top_news).sort((a, b) => a.rank - b.rank),
      gainers: by(s.gainers).sort((a, b) => a.rank - b.rank),
    };
  }

  const db = supabase();
  const [summary, quotes, sectorWatch, positioning, bloomberg, news, gainers] =
    await Promise.all([
      db.from("summaries").select("*").eq("run_date", date).maybeSingle(),
      db.from("quotes").select("*").eq("run_date", date),
      db.from("sector_watch").select("*").eq("run_date", date)
        .order("score", { ascending: false }),
      db.from("positioning").select("*").eq("run_date", date)
        .order("otm_put_call", { ascending: false }),
      db.from("bloomberg").select("*").eq("run_date", date).maybeSingle(),
      db.from("top_news").select("*").eq("run_date", date)
        .order("rank", { ascending: true }),
      db.from("gainers").select("*").eq("run_date", date)
        .order("rank", { ascending: true }),
    ]);
  const firstError =
    summary.error ?? quotes.error ?? sectorWatch.error ?? positioning.error ??
    bloomberg.error ?? news.error ?? gainers.error;
  if (firstError) throw new Error(`briefing ${date}: ${firstError.message}`);
  return {
    summary: (summary.data as SummaryRow | null) ?? null,
    quotes: (quotes.data ?? []) as QuoteRow[],
    sectorWatch: (sectorWatch.data ?? []) as SectorWatchRow[],
    positioning: (positioning.data ?? []) as PositioningRow[],
    bloomberg: (bloomberg.data as BloombergRow | null) ?? null,
    news: (news.data ?? []) as NewsRow[],
    gainers: (gainers.data ?? []) as GainerRow[],
  };
}

/** Public link to the committed PDF for a date (repo is public). */
export function pdfUrl(date: string): string {
  return `https://raw.githubusercontent.com/saumikvij-gif/Market-Summary-Bot/main/summaries/market_summary_${date}.pdf`;
}
