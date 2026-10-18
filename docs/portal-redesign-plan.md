# AgentTrading portal redesign plan

Updated 2026-10-17. Scope: the existing Streamlit portal in `src/ui/`. This plan builds on the current uncommitted v2 UI work and the [source review](open-source-trading-ui-review.md). Trading decisions, gate calculations, OOS access rules, and order permissions remain governed by the existing application code.

## Product goal

Within ten seconds of opening the portal, an operator should know the account mode, whether an agent is active, the latest validation decision, and the next useful action. A researcher should be able to trace every performance number to its market, sample, cost profile, and source file. Backtest returns and exchange account values must remain visibly distinct.

## Information architecture

| Area | Primary question | Main content |
| --- | --- | --- |
| Home · Dashboard | What is the current state and what should I do next? | Persistent agent status, report context, verdict, next step, four gate metrics, validation curve, recent evidence. |
| Agent · Strategy | What can the agent run, and why? | Active strategy or clear inactive state, candidate pipeline, rules and evidence. |
| Agent · Monitor / History | What is happening on the connected account? | Market data, positions, orders, realized P&L and audit trail with update times. |
| Backtest · Walk-forward / Explore / Trades / Diagnose | Is the research result credible? | Out-of-sample gate, same-context candidate comparison, trade inspection and failure analysis. |
| Settings & Safety | What controls execution risk? | Mode and key status, limits, kill switch and explicit confirmation for critical actions. |

The four existing sidebar groups remain the navigation spine. The page content follows **state → decision → evidence → action**. A concise next-step callout appears above detailed charts. On narrower screens, status and the next step wrap before tables or charts; critical controls retain full labels.

## Screen blueprint

```text
Dashboard
┌ Account mode · HALT · active strategy · key status · verdict ┐
│ Report: market · sample · cost profile · updated time          │
│ Decision and one next step                                      │
├ Gate metrics: trades · Sharpe · drawdown · DSR ────────────────┤
│ Validation equity + drawdown      │ Decision explanation       │
├ Optional analysis: folds · triggers · train vs test ──────────┤
│ Recent validation trades │ Human tasks │ Phase progress        │
└─────────────────────────────────────────────────────────────────┘
```

Every result view should show its market, time range and cost model close to the numbers. Use signed values plus color for profit and loss, tabular numerals for comparison, and separate messages for empty data, stale data and failed requests. The research benchmark compares candidates only within the same market, interval and cost profile. A Bitcoin passive baseline is deferred until matched futures data and a stated cost model exist.

## Task list

| ID | Priority | Status | Task | Acceptance |
| --- | --- | --- | --- | --- |
| P0-01 | P0 | Done | Put a verdict-specific next step above dashboard metrics and give the decision panel enough width. | Dashboard shows an action for pass, fail, insufficient and missing report; local report renders without exception. |
| P0-02 | P0 | To do | Distinguish **historical validation** from **connected account** data on every relevant screen. | Chart, KPI, position and balance labels identify their source and time basis; no backtest P&L looks like live P&L. |
| P0-03 | P0 | To do | Unify empty, stale and request-error states. | Each state says what happened and one recovery step; stale exports cannot appear current. |
| P1-01 | P1 | To do | Add clear dashboard paths to Diagnosis, Walk-forward and Strategy, preserving selected report context. | Operator reaches the named detail screen without losing the report selection. |
| P1-02 | P1 | To do | Make candidate comparison resilient to changed filters and rank metric. | Selected rows remain the intended candidates or clear visibly; sorting uses numeric values; same-context rule is visible. |
| P1-03 | P1 | To do | Standardize table headers, units, signed P&L, timestamps and download behavior. | Trades, runs and account tables use consistent labels and show UTC where applicable. |
| P1-04 | P1 | To do | Make monitor freshness and partial failures explicit. | Each market/account block shows its own last successful update; a failed request cannot imply that all blocks refreshed. |
| P1-05 | P1 | To do | Tighten risk and mode-change presentation. | LIVE, HALT, kill and resume states are prominent, keyboard usable and retain existing confirmations. |
| P2-01 | P2 | To do | Check layouts at 1440, 1024 and 390 px. | No clipped status, controls or chart labels; tables can scroll without hiding critical context. |
| P2-02 | P2 | To do | Review keyboard navigation, focus, contrast and non-color status cues. | All controls are reachable and status is understandable without color. |
| P2-03 | P2 | Blocked on data | Build a matched XBTUSD passive futures benchmark. | Same timestamps, capital convention, fees and funding assumptions are shown beside the strategy result. |
| P2-04 | P2 | To do | Run UI regression and manual browser review. | Existing UI tests pass; empty, stale, DEMO, LIVE and HALT states are inspected at target widths. |

## Delivery order

1. Finish P0 tasks and validate the dashboard using fixture and local report data.
2. Complete navigation, comparison and table consistency tasks (P1-01 to P1-03).
3. Complete monitor and safety states (P1-04 to P1-05), then browser and accessibility review.
4. Build the market benchmark only after the matched data contract is agreed and available.

The current implementation of P0-01 is in `src/ui/dashboard.py`, `src/ui/views/overview.py`, and `src/ui/theme.py`. This plan does not mark the rest of the portal complete; the table is the implementation backlog.
