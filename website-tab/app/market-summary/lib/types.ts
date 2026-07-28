// Row shapes — mirror the Supabase tables in schema.sql exactly.

export interface SummaryRow {
  run_date: string;            // "YYYY-MM-DD"
  sentiment: string | null;    // "Bullish" | "Slightly Bearish" | …
  score: number | null;        // composite, scaled -100..100
  summary_md: string;          // full briefing markdown
}

export interface TrendPoint {
  run_date: string;
  score: number;               // -100..100
}

export interface QuoteRow {
  run_date: string;
  section: string;             // indices | stocks | sectors | commodities | fx | rates
  name: string;
  price: number | null;
  change: number | null;
  pct_change: number | null;
}

export interface SectorWatchRow {
  run_date: string;
  sector: string;
  move_pct: number | null;
  rel_strength: number | null; // % delta vs benchmark
  benchmark: string | null;    // "Nasdaq" | "S&P"
  breadth_pct: number | null;  // 0..100
  news_score: number | null;   // -1..1
  score: number | null;        // -1..1
  label: string | null;
}

export interface PositioningRow {
  run_date: string;
  basket: string;
  otm_put_call: number | null;
  short_pct_float: number | null; // fraction (0.071 = 7.1%)
  r1_pct: number | null;          // the day's move %
  price_state: string | null;
  today: string | null;           // "Bullish" | "Bearish" | "Neutral"
  today_why: string | null;
}

export interface BloombergRow {
  run_date: string;
  title: string | null;        // episode title
  published: string | null;    // episode air date
  url: string | null;          // YouTube link
  mode: string | null;         // 'transcript' | 'rundown'
  summary_md: string | null;
}

export interface NewsRow {
  run_date: string;
  rank: number;
  title: string;
  source: string | null;
  summary: string | null;
}

export interface GainerRow {
  run_date: string;
  rank: number;
  symbol: string | null;
  name: string | null;
  price: number | null;
  pct_change: number | null;
}

/** Everything the page needs for one selected day. */
export interface DayBriefing {
  summary: SummaryRow | null;
  quotes: QuoteRow[];
  sectorWatch: SectorWatchRow[];
  positioning: PositioningRow[];
  bloomberg: BloombergRow | null;
  news: NewsRow[];
  gainers: GainerRow[];
}
