# ADR 0002 — Integrate with the client's ticketing system through an adapter, never write to it directly

**Status:** Accepted · **Date:** Week 1

## Context
Kaveri's ticketing tool stays (hard constraint). Its webhooks are at-least-once and sometimes delayed or duplicated. We don't own its uptime.

## Decision
- Build a **mock ServiceNow-style API** (`src/hao/mock_ticketing`) that behaves like the real thing: string-typed fields, `{"result": ...}` envelopes, numeric state codes, HMAC-signed webhooks, retries, and **configurable failure injection** (`MOCK_FAILURE_RATE`, `MOCK_LATENCY_MS`).
- All platform reads and writes go through a `TicketingAdapter` interface (Phase 2). Swapping in real ServiceNow or Jira means writing one class.
- Webhook consumers must be **idempotent**, keyed on `sys_id` + `sys_updated_on`.

## Consequences
- ✅ We develop against realistic friction from day one instead of discovering it in production.
- ✅ Portable to other clients.
- ⚠️ The mock must be kept honest. When we learn a new quirk of the real system, we add it to the mock.
