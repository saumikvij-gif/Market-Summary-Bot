-- ============================================================================
-- Market Summary — Supabase schema (the contract between the Python jobs and
-- the website tab). Run this in the Supabase SQL editor when the project is
-- created. The Python pipeline UPSERTs one batch of rows per session date;
-- the Next.js tab (client components, anon key) only ever SELECTs.
--
-- Keys: everything is keyed by run_date (the trading-session date, DATE).
-- Re-running a date overwrites it — same convention as the history CSVs.
-- ============================================================================

-- One row per trading day: the briefing prose + the day's tone score.
create table if not exists summaries (
  run_date    date primary key,
  sentiment   text,                -- e.g. "Bullish" / "Slightly Bearish"
  score       integer,             -- composite score scaled to -100..100
  summary_md  text,                -- full briefing markdown (prose + dashboard)
  created_at  timestamptz not null default now()
);

-- One row per (day, instrument): the market snapshot tables.
create table if not exists quotes (
  run_date    date not null,
  section     text not null,       -- indices | stocks | sectors | commodities | fx | rates
  name        text not null,       -- display name, e.g. "S&P 500"
  price       double precision,
  change      double precision,
  pct_change  double precision,
  primary key (run_date, section, name)
);

-- One row per (day, sector basket): the Sector Watch table.
create table if not exists sector_watch (
  run_date      date not null,
  sector        text not null,
  move_pct      double precision,
  rel_strength  double precision,  -- delta vs benchmark, in %
  benchmark     text,              -- "Nasdaq" | "S&P"
  breadth_pct   integer,           -- 0..100
  news_score    double precision,  -- -1..1
  score         double precision,  -- blended, -1..1
  label         text,              -- "Bullish" etc.
  primary key (run_date, sector)
);

-- One row per (day, basket): the Positioning & Regime read (under evaluation).
create table if not exists positioning (
  run_date         date not null,
  basket           text not null,
  otm_put_call     double precision,
  short_pct_float  double precision,  -- fraction, e.g. 0.071 = 7.1%
  r1_pct           double precision,  -- the day's move
  price_state      text,              -- bouncing | stabilizing | falling | ...
  today            text,              -- "Bullish" | "Bearish" | "Neutral"
  today_why        text,
  primary key (run_date, basket)
);

-- One row per day: the Bloomberg "The Close" episode summary.
create table if not exists bloomberg (
  run_date    date primary key,
  title       text,                -- episode title
  published   text,                -- episode air date (ISO)
  url         text,                -- YouTube watch link
  mode        text,                -- 'transcript' | 'rundown'
  summary_md  text                 -- Claude's episode summary (markdown)
);

-- Top news of the day (small, ranked list).
create table if not exists top_news (
  run_date  date not null,
  rank      integer not null,
  title     text not null,
  source    text,
  summary   text,
  primary key (run_date, rank)
);

-- Top market gainers of the day (ranked).
create table if not exists gainers (
  run_date    date not null,
  rank        integer not null,
  symbol      text,
  name        text,
  price       double precision,
  pct_change  double precision,
  primary key (run_date, rank)
);

create index if not exists quotes_by_date       on quotes (run_date);
create index if not exists sector_watch_by_date on sector_watch (run_date);
create index if not exists positioning_by_date  on positioning (run_date);

-- ── Row Level Security: the site reads with the PUBLIC anon key, so allow
--    SELECT to everyone; writes happen only through the service-role key the
--    Python job holds (service role bypasses RLS). No insert/update policies
--    for anon on purpose.
alter table summaries    enable row level security;
alter table quotes       enable row level security;
alter table sector_watch enable row level security;
alter table positioning  enable row level security;
alter table bloomberg    enable row level security;
alter table top_news     enable row level security;
alter table gainers      enable row level security;

create policy "public read" on summaries    for select using (true);
create policy "public read" on quotes       for select using (true);
create policy "public read" on sector_watch for select using (true);
create policy "public read" on positioning  for select using (true);
create policy "public read" on bloomberg    for select using (true);
create policy "public read" on top_news     for select using (true);
create policy "public read" on gainers      for select using (true);
