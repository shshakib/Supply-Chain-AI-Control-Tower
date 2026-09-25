# Technical Reference

This page keeps the implementation details behind the [README](../README.md).
For the full architecture and file map, see [ARCHITECTURE.md](../ARCHITECTURE.md).

## VS Code Tasks And Debugging

Open the `Supply-Chain-AI-Control-Tower` folder itself as the VS Code workspace. The checked-in
`.vscode` configuration selects `.venv`, enables pytest discovery, and provides:

- `Ctrl+Shift+B`: run the SQLite web console and external-risk MCP service together
- **Terminal > Run Task > Supply Chain AI Control Tower: Run Full Local Stack**: start the web
  console and synthetic MCP risk feed together
- **Terminal > Run Task > Supply Chain AI Control Tower: Run External Risk MCP**: run only the
  Streamable HTTP MCP service
- **Terminal > Run Task > Supply Chain AI Control Tower: Seed SQLite Demo**: create or refresh local demo data
- **Terminal > Run Task > Supply Chain AI Control Tower: Run Offline Demo**: run the no-key workflow with a trace
- **Terminal > Run Task > Supply Chain AI Control Tower: Ask LLM (SQLite, API Key)**: prompt for a question and
  synthetic persona
- **Terminal > Run Task > Supply Chain AI Control Tower: Verify**: run tests, linting, and formatting checks
- **Terminal > Run Task > Supply Chain AI Control Tower: Prepare PostgreSQL Demo**: start the pgvector container
  and seed PostgreSQL

The Run and Debug panel includes a compound configuration for the web server and MCP service,
plus individual configurations for the offline CLI demo and an LLM question. Tasks whose names
include `API Key` require `OPENAI_API_KEY` in `.env`; PostgreSQL and Docker MCP tasks require
Docker Desktop.

## MCP In One Concrete Flow

For the question "What may be worsening shipment `SS-CRITICAL-001`?":

1. The shipment specialist reads the authorized shipment and tracking history from PostgreSQL.
2. It learns that BlueArc Logistics recorded an exception at the Port of Vancouver.
3. It calls `search_disruption_events` or `get_carrier_advisories` through MCP.
4. The separate risk-feed service returns `external-risk:EXT-2026-001`, a synthetic Vancouver
   terminal disruption.
5. The specialist labels the event as correlated external evidence, not confirmed causation.
6. The supervisor LLM combines that evidence with inventory and contract findings into the final
   answer.

MCP is the interoperability boundary, not another reasoning layer. The OpenAI Agents SDK client
discovers and calls tools exposed by the independent MCP server over Streamable HTTP. If that
service is offline, the agents continue with local SQL and RAG evidence and disclose the missing
external signal.

The implementation follows the official [OpenAI Agents SDK MCP integration guide](https://openai.github.io/openai-agents-python/mcp/)
and uses the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).

## Live Execution Observability

Both `/api/chat/stream` and `/api/demo/stream` return Server-Sent Events. The browser renders
those events in three coordinated views:

- **Map** lights up the deterministic access boundary, the single supervisor, specialists,
  PostgreSQL, RAG retrieval, MCP, and the answer. Paired arrows between the supervisor and
  specialists distinguish downward delegation from upward evidence return. A shared execution-stage
  frame groups the specialists with their evidence sources, and a centered connector shows that tool
  flow without implying a direct source-to-answer path. A single left-side route from the supervisor
  lights only when it has enough evidence.
- **Timeline** preserves the exact event order with source labels and measured durations.
- **Evidence** shows the durable tool records and citations returned with the answer.

The supervisor is the parent orchestration span, so it remains active while delegated specialists
run and while it reviews their reports. A review emits `more_evidence` when more specialist work
is requested. Otherwise it stays pending until the final structured answer passes application
validation. Only then does the trace emit `evidence_sufficient` and complete the supervisor and
answer spans. This indicates a valid response, not independently verified factual completeness;
the answer can still disclose missing evidence in its caveats. Refusals, malformed or empty output,
and run errors do not produce a success event. The Agents SDK hard limit of 14 supervisor turns, plus the 6-turn
limit on each specialist invocation, prevents an infinite agent loop. Its map label changes from
planning to coordinating, reviewing evidence, and finalizing so the open span is not mistaken for
one continuous model call. Review events in the timeline select the same supervisor map node rather
than implying a second reviewer agent. Directional connectors show which stage is active or
complete, and the light/dark theme preference is stored locally in the browser.

## Retrieval Resilience

Document vectors carry their embedding model and dimensions. Semantic search only uses compatible
vectors within the user's access scope. If the embedding service fails, returns invalid vectors,
or no compatible index is available, scoped keyword search still works. The retrieval tool includes
a limitation (even with zero results), and the trace records keyword-only retrieval. Database and
authorization errors are not silently hidden by this fallback.

After upgrading an existing database, run `control-tower migrate`, then
`control-tower index-documents` with your usual `--database-url` before the subcommand.
The new migration preserves old vectors but leaves their unknown provenance unset. Indexing
automatically rebuilds unknown or mismatched vectors, so `--force` is not required after changing
models. Reindexing calls the embedding API and incurs normal API usage. Until then, keyword search
remains available and the UI counts only embeddings matching the configured model and dimensions.

Selecting a specialist map node opens a safe input/output exchange: the delegated supervisor task,
the specialist's structured summary, cited evidence claims, limitations, and duration. Tool nodes
show redacted arguments, result counts, source, and parent stage. The evidence view groups stable
references into operational records, external MCP intelligence, and retrieved document passages.
The trace deliberately excludes hidden model reasoning, system prompts, credentials, internal
UUIDs, and raw unrestricted model output. The same event schema covers started, completed, failed,
skipped, and informational states, so unavailable services and unused specialists are visible
instead of silently omitted.

## Database Migrations

Alembic owns the persistent database schema. Apply all pending revisions with:

```powershell
control-tower migrate
```

`init-db` remains as a backwards-compatible alias. When a model changes, generate a revision with
`alembic revision --autogenerate -m "describe the schema change"`, review the generated SQL, and
apply it with `control-tower migrate`. Seeding inserts demo rows; it does not define tables.

Databases created before migration support was introduced have no Alembic revision marker. Because
the included data is synthetic, reset those once with `docker compose down --volumes` or delete the
old local SQLite `.db` file, then seed again.

## Synthetic Dataset

The fixed demo date is `2026-06-30`. The generator creates:

| Entity | Count |
|---|---:|
| Synthetic users | 6 |
| Warehouses | 4 |
| Suppliers | 12 |
| Products | 30 |
| Purchase orders | 181 |
| Shipments | 181 |
| Inventory snapshots | 3,600 |
| Documents | 14 |
| External MCP risk events | 8 |

The main correlated scenario includes:

- Toronto has 30 available `MCU-X100` units and 3.8 days of cover.
- Shipment `SS-CRITICAL-001` contains 800 replacement units from Apex Circuits.
- A port labor disruption delays the shipment by nine days.
- An incident report describes the interruption and possible air-freight recovery.
- The supplier agreement permits a 4% credit after a five-day grace period, subject to
  force-majeure review.
- The separate synthetic MCP feed reports a critical Vancouver terminal disruption, a BlueArc
  capacity advisory, and an Apex capacity-watch signal with stable `external-risk:` references.

## Deterministic Access

| Persona | Role | Warehouse scope |
|---|---|---|
| `ava.admin@controltower.demo` | Global administrator | All |
| `noah.east@controltower.demo` | Regional operations | Toronto and Chicago |
| `mia.west@controltower.demo` | Regional operations | Vancouver and Austin |
| `priya.procurement@controltower.demo` | Procurement analyst | All |
| `leo.quality@controltower.demo` | Quality analyst | All |
| `sofia.viewer@controltower.demo` | Viewer | Toronto |

The access service resolves scope before the agent run. Tools inject organization and warehouse
filters from local context. Document retrieval applies the same warehouse and supplier scope
before keyword or vector ranking. Conversation reads are also restricted to their owning user.

## Agent Responsibilities

| Agent | Local tools | External MCP tools |
|---|---|---|
| Shipment specialist | Delayed inbound shipments, tracking history | Disruptions, lanes, carriers |
| Inventory specialist | Current low stock, inventory history | None |
| Supplier-risk specialist | Risk ranking, scorecards, quality incidents | Supplier watch signals |
| Contracts and compliance specialist | Scoped hybrid contract/report retrieval | Trade advisories |
| Supervisor | The four specialists only | None |

The LLM chooses specialists and tool arguments. Authorization, joins, aggregation, risk-score
calculation, date limits, and SQL construction remain deterministic Python code.

## Run Limits

Each live question shares one budget across the supervisor and specialists: a 180-second deadline,
24 model calls, 60,000 reported tokens, and 3,000 output tokens per call by default. Model requests
have a 30-second timeout with automatic retries disabled. Online document embeddings use cancellable
requests with a 10-second timeout. Configure these server-side in `.env` (see `.env.example` for
the available settings); they cannot be overridden by a user's question. Existing turn limits
remain in place.

For an estimated LLM-dollar cutoff, set `CONTROL_TOWER_MAX_COST_USD` and both
`CONTROL_TOWER_INPUT_USD_PER_MILLION` / `CONTROL_TOWER_OUTPUT_USD_PER_MILLION` to conservative rates
covering every configured model. No prices are assumed, and invalid/missing rates fail configuration
when the dollar cutoff is enabled. Zero disables only the dollar cutoff. Reported token counts,
elapsed time, and estimated cost appear in the trace and saved response metadata.

Usage-based cutoffs are checked before and after model calls. Parallel calls already in flight can
overshoot a threshold; cancelled requests may still be billed. These are application circuit
breakers, not guaranteed provider billing caps. Estimates exclude embeddings, indexing, hosting,
and discounts. Use provider/account-level spending controls as well. Deadlines cancel asynchronous
work; synchronous database operations also need database-side timeouts in a shared deployment.

Final citations must match references returned by tools in the current run. This verifies provenance,
not that every claim is supported by the cited text. Run limits and validation failures never produce
a successful final answer.
