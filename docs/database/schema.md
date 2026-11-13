# Database schema

PostgreSQL 14+, managed by Alembic (`services/api/alembic/versions`). Generated from the SQLAlchemy models.

Conventions: timestamps are `timestamptz` (UTC); daily bars use `ts` = 00:00 UTC of the session date plus `session_date`; missing values are NULL (never 0); derived rows carry `calc_version`.

Views: `dividend_history` (cash-dividend corporate actions), `company_announcements` (news of announcement-like kinds joined to instruments). TimescaleDB hypertables on `price_bars`/`quotes` when the extension exists (ADR-0006).

## Reference

### `exchanges`

- `id` INTEGER PK not null
- `code` VARCHAR(16) not null
- `mic` VARCHAR(8)
- `name` VARCHAR(128) not null
- `country` VARCHAR(2) not null
- `currency` VARCHAR(3) not null
- `timezone` VARCHAR(64) not null
- `open_time` TIME
- `close_time` TIME
- Unique: (code)

### `sectors`

- `id` INTEGER PK not null
- `name` VARCHAR(128) not null
- Unique: (name)

### `industries`

- `id` INTEGER PK not null
- `sector_id` INTEGER not null → sectors.id
- `name` VARCHAR(128) not null
- Unique: (sector_id, name)

### `issuers`

- `id` INTEGER PK not null
- `name` VARCHAR(256) not null
- `country` VARCHAR(2)
- `website` VARCHAR(512)
- `created_at` DATETIME not null
- `updated_at` DATETIME not null
- Unique: (name)

### `instruments`

- `id` INTEGER PK not null
- `exchange_id` INTEGER not null → exchanges.id
- `symbol` VARCHAR(32) not null
- `name` VARCHAR(256) not null
- `security_type` VARCHAR(16) not null
- `sector_id` INTEGER → sectors.id
- `industry_id` INTEGER → industries.id
- `issuer_id` INTEGER → issuers.id
- `currency` VARCHAR(3) not null
- `isin` VARCHAR(12)
- `listing_date` DATE
- `trading_status` VARCHAR(16) not null
- `lot_size` INTEGER
- `coupon_rate` FLOAT
- `maturity_date` DATE
- `face_value` FLOAT
- `is_synthetic` BOOLEAN not null
- `primary_provider_id` INTEGER → data_providers.id
- `first_seen_at` DATETIME not null
- `last_seen_at` DATETIME
- `created_at` DATETIME not null
- `updated_at` DATETIME not null
- Unique: (exchange_id, symbol)
- Indexes: ix_instruments_sector(sector_id), ix_instruments_is_synthetic(is_synthetic)

### `instrument_identifiers`: Maps provider-specific symbols to our instrument ids (e.g. MyStocks 'SCOM.KE' -> instruments.id).

- `id` INTEGER PK not null
- `instrument_id` INTEGER not null → instruments.id
- `provider_id` INTEGER not null → data_providers.id
- `provider_symbol` VARCHAR(64) not null
- Unique: (provider_id, provider_symbol)
- Indexes: ix_instrument_identifiers_instrument_id(instrument_id)

### `market_sessions`

- `id` INTEGER PK not null
- `exchange_id` INTEGER not null → exchanges.id
- `session_date` DATE not null
- `status` VARCHAR(16) not null
- `reason` VARCHAR(256)
- `source_provider_id` INTEGER → data_providers.id
- Unique: (exchange_id, session_date)

### `data_providers`

- `id` INTEGER PK not null
- `code` VARCHAR(32) not null
- `name` VARCHAR(128) not null
- `latency_class` VARCHAR(16) not null
- `delay_minutes` INTEGER
- `is_synthetic` BOOLEAN not null
- `enabled` BOOLEAN not null
- `priority` INTEGER not null
- `attribution` TEXT
- `notes` TEXT
- `created_at` DATETIME not null
- `updated_at` DATETIME not null
- Unique: (code)

## Market data

### `quotes`: A provider quote observation. Never overwritten: one row per (instrument, provider, market_ts).

- `instrument_id` INTEGER PK not null → instruments.id
- `provider_id` INTEGER PK not null → data_providers.id
- `market_ts` DATETIME PK not null
- `source_ts` DATETIME
- `ingested_at` DATETIME not null
- `price` FLOAT
- `open` FLOAT
- `high` FLOAT
- `low` FLOAT
- `previous_close` FLOAT
- `volume` BIGINT
- `bid` FLOAT
- `ask` FLOAT
- `currency` VARCHAR(3) not null
- `latency_class` VARCHAR(16) not null
- `delay_minutes` INTEGER
- `provider_stale` BOOLEAN
- `quality_status` VARCHAR(16) not null
- `ingestion_run_id` BIGINT → ingestion_runs.id
- Indexes: ix_quotes_instrument_ts(instrument_id, market_ts)

### `price_bars`: Raw (unadjusted) OHLCV bars exactly as reported. Adjusted series are derived, never stored over these.

- `instrument_id` INTEGER PK not null → instruments.id
- `provider_id` INTEGER PK not null → data_providers.id
- `timeframe` VARCHAR(4) PK not null
- `ts` DATETIME PK not null
- `session_date` DATE
- `open` FLOAT
- `high` FLOAT
- `low` FLOAT
- `close` FLOAT not null
- `volume` BIGINT
- `turnover` FLOAT
- `trades_count` INTEGER
- `ohlc_available` BOOLEAN not null
- `volume_available` BOOLEAN not null
- `quality_status` VARCHAR(16) not null
- `currency` VARCHAR(3) not null
- `ingested_at` DATETIME not null
- `revised_at` DATETIME
- `ingestion_run_id` BIGINT → ingestion_runs.id
- Indexes: ix_price_bars_lookup(instrument_id, timeframe, ts)

### `price_bar_revisions`: Previous values of a bar that a provider later revised. Original observations are never lost.

- `id` BIGINT PK not null
- `instrument_id` INTEGER not null → instruments.id
- `provider_id` INTEGER not null → data_providers.id
- `timeframe` VARCHAR(4) not null
- `ts` DATETIME not null
- `previous` JSONB not null
- `replaced_at` DATETIME not null
- `ingestion_run_id` BIGINT → ingestion_runs.id
- Indexes: ix_pbr_lookup(instrument_id, timeframe, ts)

### `market_indices`

- `id` INTEGER PK not null
- `code` VARCHAR(32) not null
- `name` VARCHAR(128) not null
- `exchange_id` INTEGER not null → exchanges.id
- `is_synthetic` BOOLEAN not null
- Unique: (code)

### `index_history`

- `index_id` INTEGER PK not null → market_indices.id
- `provider_id` INTEGER PK not null → data_providers.id
- `ts` DATETIME PK not null
- `session_date` DATE
- `open` FLOAT
- `high` FLOAT
- `low` FLOAT
- `close` FLOAT not null
- `ingested_at` DATETIME not null

### `ingestion_runs`

- `id` BIGINT PK not null
- `job_name` VARCHAR(64) not null
- `provider_id` INTEGER → data_providers.id
- `started_at` DATETIME not null
- `finished_at` DATETIME
- `status` VARCHAR(16) not null
- `items_requested` INTEGER not null
- `items_succeeded` INTEGER not null
- `items_failed` INTEGER not null
- `rows_written` INTEGER not null
- `error` TEXT
- `details` JSONB
- Indexes: ix_ingestion_runs_job_started(job_name, started_at)

### `data_source_health`

- `provider_id` INTEGER PK not null → data_providers.id
- `status` VARCHAR(16) not null
- `last_success_at` DATETIME
- `last_failure_at` DATETIME
- `last_error` TEXT
- `consecutive_failures` INTEGER not null
- `latest_market_ts` DATETIME
- `rate_limit_limit` INTEGER
- `rate_limit_remaining` INTEGER
- `rate_limit_reset_at` DATETIME
- `updated_at` DATETIME not null

### `data_quality_issues`

- `id` BIGINT PK not null
- `instrument_id` INTEGER → instruments.id
- `provider_id` INTEGER → data_providers.id
- `issue_type` VARCHAR(48) not null
- `severity` VARCHAR(8) not null
- `observed_ts` DATETIME
- `detected_at` DATETIME not null
- `resolved_at` DATETIME
- `details` JSONB
- `ingestion_run_id` BIGINT → ingestion_runs.id
- Indexes: ix_dq_open(resolved_at, detected_at)

## Corporate & fundamentals

### `corporate_actions`: Dividends, splits, bonus issues, rights, suspensions. `dividend_history` is a SQL view over this table.

- `id` INTEGER PK not null
- `instrument_id` INTEGER not null → instruments.id
- `provider_id` INTEGER not null → data_providers.id
- `external_id` VARCHAR(128) not null
- `action_type` VARCHAR(24) not null
- `status` VARCHAR(32)
- `title` VARCHAR(512)
- `announced_at` DATETIME
- `ex_date` DATE
- `record_date` DATE
- `payment_date` DATE
- `effective_date` DATE
- `cash_amount` FLOAT
- `currency` VARCHAR(3)
- `ratio_from` FLOAT
- `ratio_to` FLOAT
- `source_url` VARCHAR(1024)
- `details` JSONB
- `created_at` DATETIME not null
- `updated_at` DATETIME not null
- Unique: (provider_id, external_id)
- Indexes: ix_ca_instrument_ex(instrument_id, ex_date)

### `company_profiles`

- `instrument_id` INTEGER PK not null → instruments.id
- `provider_id` INTEGER not null → data_providers.id
- `description` TEXT
- `website` VARCHAR(512)
- `country` VARCHAR(2)
- `market_cap` FLOAT
- `provider_pe_ratio` FLOAT
- `fetched_at` DATETIME not null

### `financial_statements`: Long-format financial statement line items with publication date (prevents look-ahead in backtests).

- `id` BIGINT PK not null
- `instrument_id` INTEGER not null → instruments.id
- `period_end` DATE not null
- `period_type` VARCHAR(4) not null
- `statement_type` VARCHAR(12) not null
- `line_item` VARCHAR(64) not null
- `value` FLOAT not null
- `currency` VARCHAR(3) not null
- `reported_unit` VARCHAR(16)
- `published_at` DATE not null
- `source` VARCHAR(256) not null
- `source_url` VARCHAR(1024)
- `quality` VARCHAR(16) not null
- `ingested_at` DATETIME not null
- Unique: (instrument_id, period_end, period_type, statement_type, line_item, source)
- Indexes: ix_fs_instrument_pub(instrument_id, published_at)

### `fundamental_metrics`: Computed point-in-time fundamental metric (e.g. pe_ratio as of date X using only info public by X).

- `instrument_id` INTEGER PK not null → instruments.id
- `metric` VARCHAR(48) PK not null
- `as_of` DATE PK not null
- `calc_version` VARCHAR(16) PK not null
- `value` FLOAT
- `period_end` DATE
- `inputs` JSONB
- `computed_at` DATETIME not null
- Indexes: ix_fm_lookup(instrument_id, metric, as_of)

### `news_articles`

- `id` BIGINT PK not null
- `provider_id` INTEGER not null → data_providers.id
- `external_id` VARCHAR(128) not null
- `kind` VARCHAR(24) not null
- `category` VARCHAR(64)
- `title` VARCHAR(1024) not null
- `publisher` VARCHAR(256)
- `url` VARCHAR(2048)
- `summary` TEXT
- `summary_is_generated` BOOLEAN not null
- `published_at` DATETIME
- `ingested_at` DATETIME not null
- `source_reliability` VARCHAR(16) not null
- `is_synthetic` BOOLEAN not null
- Unique: (provider_id, external_id)
- Indexes: ix_news_published(published_at)

### `news_article_instruments`

- `article_id` BIGINT PK not null → news_articles.id
- `instrument_id` INTEGER PK not null → instruments.id
- Indexes: ix_news_article_instruments_instrument_id(instrument_id)

## Analytics

### `technical_indicators`: All indicator values for one bar, computed only from data up to and including that bar.

- `instrument_id` INTEGER PK not null → instruments.id
- `timeframe` VARCHAR(4) PK not null
- `ts` DATETIME PK not null
- `calc_version` VARCHAR(16) PK not null
- `values` JSONB not null
- `computed_at` DATETIME not null

### `trend_analysis`

- `instrument_id` INTEGER PK not null → instruments.id
- `timeframe` VARCHAR(4) PK not null
- `as_of_ts` DATETIME PK not null
- `calc_version` VARCHAR(16) PK not null
- `classification` VARCHAR(24) not null
- `strength` FLOAT
- `evidence` JSONB not null
- `computed_at` DATETIME not null

### `support_resistance_levels`

- `id` BIGINT PK not null
- `instrument_id` INTEGER not null → instruments.id
- `timeframe` VARCHAR(4) not null
- `as_of_ts` DATETIME not null
- `level_type` VARCHAR(12) not null
- `price` FLOAT not null
- `touches` INTEGER not null
- `first_touch_ts` DATETIME not null
- `last_touch_ts` DATETIME not null
- `calc_version` VARCHAR(16) not null
- Indexes: ix_sr_lookup(instrument_id, timeframe, as_of_ts)

### `instrument_snapshots`: Latest per-instrument metrics powering the scanner (volatility, relative strength, liquidity, scores).

- `instrument_id` INTEGER PK not null → instruments.id
- `timeframe` VARCHAR(4) PK not null
- `as_of_ts` DATETIME not null
- `session_date` DATE
- `calc_version` VARCHAR(16) not null
- `bars_available` INTEGER not null
- `last_close` FLOAT
- `prev_close` FLOAT
- `change_pct` FLOAT
- `volume` BIGINT
- `avg_volume_20` FLOAT
- `relative_volume` FLOAT
- `avg_traded_value_20` FLOAT
- `trading_frequency_60` FLOAT
- `rsi_14` FLOAT
- `return_1w` FLOAT
- `return_1m` FLOAT
- `return_3m` FLOAT
- `return_6m` FLOAT
- `return_1y` FLOAT
- `return_ytd` FLOAT
- `volatility_20` FLOAT
- `volatility_60` FLOAT
- `atr_pct_14` FLOAT
- `max_drawdown_1y` FLOAT
- `drawdown_from_high` FLOAT
- `high_52w` FLOAT
- `low_52w` FLOAT
- `relative_strength_3m` FLOAT
- `dividend_yield` FLOAT
- `pe_ratio` FLOAT
- `trend_class` VARCHAR(24)
- `trading_score` FLOAT
- `quality_score` FLOAT
- `risk_score` FLOAT
- `score_details` JSONB
- `computed_at` DATETIME not null

### `opportunity_signals`

- `id` BIGINT PK not null
- `instrument_id` INTEGER not null → instruments.id
- `category` VARCHAR(16) not null
- `signal_type` VARCHAR(48) not null
- `direction` VARCHAR(8) not null
- `timeframe` VARCHAR(4) not null
- `bar_ts` DATETIME not null
- `detected_at` DATETIME not null
- `reference_price` FLOAT not null
- `evidence` JSONB not null
- `invalidation` JSONB not null
- `risk_factors` JSONB not null
- `status` VARCHAR(12) not null
- `status_changed_at` DATETIME
- `expires_at` DATETIME not null
- `is_backfill` BOOLEAN not null
- `calc_version` VARCHAR(16) not null
- Unique: (instrument_id, signal_type, timeframe, bar_ts, calc_version)
- Indexes: ix_signals_type(signal_type, timeframe), ix_signals_status_detected(status, detected_at), ix_opportunity_signals_instrument_id(instrument_id)

### `opportunity_outcomes`: Forward performance of a signal after N bars - the basis for any historical base rates shown in the UI.

- `signal_id` BIGINT PK not null → opportunity_signals.id
- `horizon_bars` INTEGER PK not null
- `evaluated_at` DATETIME not null
- `end_ts` DATETIME not null
- `forward_return` FLOAT not null
- `max_favorable` FLOAT not null
- `max_adverse` FLOAT not null
- `invalidated` BOOLEAN not null

### `analytics_reports`: Versioned validation / calibration outputs (e.g. score rank-IC, signal base rates) per data universe.

- `code` VARCHAR(48) PK not null
- `universe` VARCHAR(16) PK not null
- `calc_version` VARCHAR(16) not null
- `computed_at` DATETIME not null
- `payload` JSONB not null

## Users & research

### `users`

- `id` INTEGER PK not null
- `email` VARCHAR(320) not null
- `password_hash` VARCHAR(256) not null
- `display_name` VARCHAR(128)
- `role` VARCHAR(8) not null
- `is_active` BOOLEAN not null
- `last_login_at` DATETIME
- `failed_logins` INTEGER not null
- `locked_until` DATETIME
- `created_at` DATETIME not null
- `updated_at` DATETIME not null
- Unique: (email)

### `user_sessions`

- `id` BIGINT PK not null
- `user_id` INTEGER not null → users.id
- `token_hash` VARCHAR(64) not null
- `created_at` DATETIME not null
- `last_seen_at` DATETIME not null
- `expires_at` DATETIME not null
- `revoked_at` DATETIME
- `ip` VARCHAR(64)
- `user_agent` VARCHAR(512)
- Unique: (token_hash)
- Indexes: ix_user_sessions_user_id(user_id)

### `audit_log`

- `id` BIGINT PK not null
- `user_id` INTEGER → users.id
- `action` VARCHAR(64) not null
- `entity` VARCHAR(64)
- `entity_id` VARCHAR(64)
- `ip` VARCHAR(64)
- `ts` DATETIME not null
- `details` JSONB
- Indexes: ix_audit_user_ts(user_id, ts)

### `fee_schedules`

- `id` INTEGER PK not null
- `code` VARCHAR(48) not null
- `name` VARCHAR(128) not null
- `components` JSONB not null
- `effective_from` DATE not null
- `source_notes` TEXT not null
- `is_default` BOOLEAN not null
- `created_at` DATETIME not null
- `updated_at` DATETIME not null
- Unique: (code)

### `portfolios`

- `id` INTEGER PK not null
- `user_id` INTEGER not null → users.id
- `name` VARCHAR(128) not null
- `kind` VARCHAR(8) not null
- `base_currency` VARCHAR(3) not null
- `fee_schedule_id` INTEGER → fee_schedules.id
- `dividend_tax_rate` FLOAT not null
- `created_at` DATETIME not null
- `updated_at` DATETIME not null
- Unique: (user_id, name)
- Indexes: ix_portfolios_user_id(user_id)

### `portfolio_transactions`

- `id` BIGINT PK not null
- `portfolio_id` INTEGER not null → portfolios.id
- `instrument_id` INTEGER → instruments.id
- `txn_type` VARCHAR(12) not null
- `trade_date` DATE not null
- `quantity` FLOAT
- `price` FLOAT
- `gross_amount` FLOAT not null
- `fees` FLOAT not null
- `fee_breakdown` JSONB
- `tax` FLOAT not null
- `net_cash_flow` FLOAT not null
- `notes` TEXT
- `created_at` DATETIME not null
- Indexes: ix_ptx_portfolio_date(portfolio_id, trade_date)

### `watchlists`

- `id` INTEGER PK not null
- `user_id` INTEGER not null → users.id
- `name` VARCHAR(128) not null
- `description` TEXT
- `created_at` DATETIME not null
- `updated_at` DATETIME not null
- Unique: (user_id, name)
- Indexes: ix_watchlists_user_id(user_id)

### `watchlist_items`

- `id` INTEGER PK not null
- `watchlist_id` INTEGER not null → watchlists.id
- `instrument_id` INTEGER not null → instruments.id
- `notes` TEXT
- `tags` ARRAY not null
- `target_price` FLOAT
- `stop_price` FLOAT
- `entry_price` FLOAT
- `price_when_added` FLOAT
- `added_at` DATETIME not null
- Unique: (watchlist_id, instrument_id)

### `alert_rules`

- `id` INTEGER PK not null
- `user_id` INTEGER not null → users.id
- `instrument_id` INTEGER → instruments.id
- `rule_type` VARCHAR(24) not null
- `params` JSONB not null
- `cooldown_minutes` INTEGER not null
- `is_active` BOOLEAN not null
- `channels` ARRAY not null
- `last_state` JSONB
- `last_triggered_at` DATETIME
- `created_at` DATETIME not null
- `updated_at` DATETIME not null
- Indexes: ix_alert_rules_user_id(user_id)

### `alert_events`

- `id` BIGINT PK not null
- `rule_id` INTEGER not null → alert_rules.id
- `dedupe_key` VARCHAR(128) not null
- `triggered_at` DATETIME not null
- `message` TEXT not null
- `context` JSONB
- Unique: (rule_id, dedupe_key)
- Indexes: ix_alert_events_rule_id(rule_id)

### `notifications`

- `id` BIGINT PK not null
- `user_id` INTEGER not null → users.id
- `channel` VARCHAR(16) not null
- `title` VARCHAR(256) not null
- `body` TEXT not null
- `link` VARCHAR(512)
- `alert_event_id` BIGINT → alert_events.id
- `created_at` DATETIME not null
- `read_at` DATETIME
- `delivered_at` DATETIME
- Indexes: ix_notifications_user_unread(user_id, read_at, created_at)

### `saved_scans`

- `id` INTEGER PK not null
- `user_id` INTEGER not null → users.id
- `name` VARCHAR(128) not null
- `filters` JSONB not null
- `created_at` DATETIME not null
- `updated_at` DATETIME not null
- Unique: (user_id, name)
- Indexes: ix_saved_scans_user_id(user_id)

### `strategies`

- `id` INTEGER PK not null
- `user_id` INTEGER not null → users.id
- `name` VARCHAR(128) not null
- `description` TEXT
- `definition` JSONB not null
- `version` INTEGER not null
- `created_at` DATETIME not null
- `updated_at` DATETIME not null
- Unique: (user_id, name)
- Indexes: ix_strategies_user_id(user_id)

### `backtest_runs`

- `id` INTEGER PK not null
- `user_id` INTEGER not null → users.id
- `strategy_id` INTEGER → strategies.id
- `strategy_snapshot` JSONB not null
- `params` JSONB not null
- `status` VARCHAR(12) not null
- `calc_version` VARCHAR(16)
- `data_fingerprint` VARCHAR(64)
- `created_at` DATETIME not null
- `started_at` DATETIME
- `finished_at` DATETIME
- `error` TEXT
- `metrics` JSONB
- `equity_curve` JSONB
- `warnings` JSONB
- Indexes: ix_bt_status_created(status, created_at), ix_backtest_runs_user_id(user_id)

### `backtest_trades`

- `id` BIGINT PK not null
- `run_id` INTEGER not null → backtest_runs.id
- `instrument_id` INTEGER not null → instruments.id
- `segment` VARCHAR(16) not null
- `entry_date` DATE not null
- `entry_price` FLOAT not null
- `exit_date` DATE
- `exit_price` FLOAT
- `quantity` FLOAT not null
- `entry_fees` FLOAT not null
- `exit_fees` FLOAT not null
- `net_pnl` FLOAT
- `return_pct` FLOAT
- `bars_held` INTEGER
- `exit_reason` VARCHAR(32)
- Indexes: ix_backtest_trades_run_id(run_id)
