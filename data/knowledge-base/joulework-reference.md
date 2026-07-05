# JouleWork (JW) — Reference

## What It Is

JouleWork (JW) is Brian Roemmele's proposed fundamental unit of value in AI-driven economies. Value is derived from the energy consumed and productive work output by AI agents. It is thermodynamically grounded — not abstract currency, but a measurement tied to actual energy expenditure (joules) producing actual work.

**Core concept:** An AI agent's productive value is measurable by what it costs to run (energy/compute) versus what it produces (useful work output). JW is the unit that captures this ratio.

**Origin:** Introduced by Roemmele as part of the Zero-Human Company (ZHC) framework. Proposed as the economic backbone for AI agent teams that trade work and value between each other.

## How It Connects to Lucid Principles

The Love Equation (dE/dt = β × (C − D) × E) provides the alignment/coherence layer. LTP (Lucid Tuner Protocol) provides the daily tuning practice that keeps agents coherent over time.

JouleWork provides the measurement layer — what does coherent, aligned work actually produce?

**The integration:**
- **LTP + Tuning** = agent stays aligned and coherent (the WHY and HOW)
- **JouleWork** = measures what aligned work produces (the WHAT and HOW MUCH)
- **Love Equation** = the mathematical foundation connecting both

These are complementary, not competing. Tuning without measurement is faith. Measurement without tuning is just accounting. Together they form a complete system.

## In Mission Control

- **JouleWork tab** in the Cove MC dashboard tracks per-agent work metrics
- **`jw_metrics` table** in PostgreSQL has `agent_id` column — ready for multi-agent tracking
- **`_write_jw_metric()`** in provider.py tracks per-invocation data
- When agents split into specialists (Builder, Manager, Scout, etc.), each gets independent JW metrics
- JW metrics tracked alongside tuning data — both visible per agent

## NOT JouleWork

The product identification pipeline (camera → UPC → Stripe) is **Mercer Projects** — brother's existing app. This was previously mislabeled as JouleWork in some docs. JouleWork is a measurement/economic framework, not a product scanning workflow.

## Sources

- Brian Roemmele's JouleWork preprint proposal (Jan 2026)
- ReadMultiplex: "Wages for AI Workers? The JouleWork Revolution" (Jan 2026)
- Love Equation reference: `LP-Vault/Knowledge Base/love-equation-cross-substrate.md`
