# Open source trading UI review — 2026-10-17

GitHub stars are a discovery signal, not a quality or trading-performance measure. Counts below came from the GitHub repository API on 2026-10-17; they will change. The linked upstream repositories and documentation are the sources for the feature notes.

| Project | Stars | Relevant UI/research pattern | Fit for AgentTrading |
| --- | ---: | --- | --- |
| [Freqtrade](https://github.com/freqtrade/freqtrade) / [FreqUI docs](https://github.com/freqtrade/freqtrade/blob/develop/docs/freq-ui.md) | 55,486 | Bot status, dashboard performance, trades, dark/light UI, dry-run distinction | Put agent state and validation context ahead of charts; keep account balance distinct from backtest equity. |
| [Hummingbot](https://github.com/hummingbot/hummingbot) | 20,498 | Strategy controllers, reusable executors, multiple running bots | Show which strategy is active and which candidate is still in validation. |
| [FinRL](https://github.com/AI4Finance-Foundation/FinRL) | 16,702 | Agent research framework and reproducible experiments | Useful research reference; its notebook focus is less directly reusable as an operator dashboard. |
| [Jesse](https://github.com/jesse-ai/jesse) | 8,652 | Batch benchmark with metric filtering and sorting, saved backtest sessions, trade charts | Add sortable, side-by-side candidate comparison in the strategy explorer. |
| [OctoBot](https://github.com/Drakkar-Software/OctoBot) | 6,804 | Simple visual entry point for strategy, portfolio, automation, paper trading | Keep navigation task-oriented and identify account mode prominently. |
| [FreqUI source](https://github.com/freqtrade/frequi) | 1,090 | Standalone frontend source for the Freqtrade patterns above | Source to inspect for interaction behavior, not a package dependency. |
| [Hummingbot Dashboard](https://github.com/hummingbot/dashboard) | 369 | Streamlit setup, backtesting, deployment, monitoring | Comparable stack, but [its maintainers say the dashboard is no longer actively maintained](https://github.com/hummingbot/hummingbot-site/blob/main/docs/dashboard/index.md). Use as a pattern reference only. |

## Applied here

- The dashboard presents DEMO/LIVE/HALT, active strategy, walk-forward verdict, sample, fee profile and source timestamp beside performance. This follows the status-first pattern seen in FreqUI and OctoBot.
- Strategy Explorer now labels the market, interval and cost profile, allows ranking by Sharpe, net return, drawdown or trade count, and compares up to four candidates from the same filtered run set. This follows Jesse's batch benchmark interaction.
- The comparison stays explicitly labeled as a single-window research view. Its rank is not an approval or live-trading signal; the walk-forward gate remains authoritative.
- No external project code or assets were copied. No upstream trading engine is installed or connected.

## Limits before a true Bitcoin benchmark

The available historical run summaries use BitMEX **XBTUSD** and include futures costs and, for `+funding` profiles, funding. A BTC spot buy-and-hold series or another exchange's BTCUSDT perpetual series would have different instruments, dates, fees and funding. A valid market benchmark needs a matched symbol, timestamps, capital convention and explicit cost model before its returns can be placed beside the agent's returns. The current UI does not fabricate that baseline.

## Source inspection and next UI changes

[UI/UX change preparation](ui-ux-preparation.md) records pinned source references for FreqUI, Hummingbot Dashboard and Jesse, their license labels, the current screen-to-file map, the proposed change order, and the UI test baseline. It distinguishes Jesse's public session-management source from its bundled frontend and adds FreqUI's public result-comparison component as a direct implementation reference.
