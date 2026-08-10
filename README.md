# SupplyScope

SupplyScope is an original, synthetic supply-chain intelligence application for a technical
portfolio. It combines a manager-style multi-agent workflow, deterministic authorization,
typed database tools, PostgreSQL/pgvector retrieval, persistent conversations, and a focused
operations console.

No private source code or private data is included. The schema, prompts, synthetic scenarios,
tools, and interface were designed independently for this project.

## Architecture

[View the complete file-mapped architecture diagram](ARCHITECTURE.md). It shows the request
flow, deterministic authorization boundary, supervisor and specialist relationships, typed
domain tools, PostgreSQL/pgvector storage, external model calls, and the source files owned by
each segment.

The supervisor calls specialists as tools using the OpenAI Agents SDK. Specialists can call
only their own typed tools. A local `AgentRuntime` carries the authenticated access context,
database session, fixed as-of date, retriever, and tool-event trace. That context is not a
model-editable argument.

## Implemented Capabilities

- OpenAI Agents SDK supervisor with four specialist agents
- Pydantic structured outputs for specialist reports and final answers
- Deterministic organization, warehouse, supplier, and conversation authorization
- Typed SQLAlchemy tools instead of unrestricted model-generated SQL
- PostgreSQL schema with a native `VECTOR(384)` document embedding column
- OpenAI embedding indexing with configurable model and dimensions
- Scoped hybrid retrieval using vector similarity and keyword ranking
- SQLite vector-search fallback for development and tests
- Persistent conversations and message history
- FastAPI chat, persona, conversation, health, and deterministic-demo endpoints
- Responsive operations console with specialist activity and citations
- Six golden LLM evaluation cases
- Deterministic local demo that remains available without an API key

## Quick Start

The project already contains a local virtual environment and seeded SQLite demo database on
this machine. To run the console immediately:

```powershell
cd "C:\Users\SH2\Documents\Codex\2026-08-10\prior-conversation-with-codex-conversation-role\outputs\supplyscope"
.\.venv\Scripts\supplyscope-web.exe --database-url "sqlite:///./supplyscope.db"
```

Open `http://127.0.0.1:8000` and use **Run local scenario**. This deterministic route works
without an API key.

For a fresh checkout:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
supplyscope --database-url "sqlite:///./supplyscope.db" init-db
supplyscope --database-url "sqlite:///./supplyscope.db" seed
supplyscope-web --database-url "sqlite:///./supplyscope.db"
```

## VS Code Tasks And Debugging

Open the `supplyscope` folder itself as the VS Code workspace. The checked-in `.vscode`
configuration selects `.venv`, enables pytest discovery, and provides:

- `Ctrl+Shift+B`: run the web console with SQLite
- **Terminal > Run Task > SupplyScope: Seed SQLite Demo**: create or refresh local demo data
- **Terminal > Run Task > SupplyScope: Run Offline Demo**: run the no-key workflow with a trace
- **Terminal > Run Task > SupplyScope: Ask LLM (SQLite, API Key)**: prompt for a question and
  synthetic persona
- **Terminal > Run Task > SupplyScope: Verify**: run tests, linting, and formatting checks
- **Terminal > Run Task > SupplyScope: Prepare PostgreSQL Demo**: start the pgvector container
  and seed PostgreSQL

The Run and Debug panel includes configurations for the SQLite web server, offline CLI demo,
and an LLM question. Tasks whose names include `API Key` require `OPENAI_API_KEY` in `.env`;
PostgreSQL tasks require Docker Desktop.

## Enable LLM Chat And RAG

Create `.env` from `.env.example` and set:

```dotenv
OPENAI_API_KEY=your_api_key
SUPPLYSCOPE_SUPERVISOR_MODEL=gpt-5.6-terra
SUPPLYSCOPE_SPECIALIST_MODEL=gpt-5.6-luna
SUPPLYSCOPE_EMBEDDING_MODEL=text-embedding-3-small
SUPPLYSCOPE_EMBEDDING_DIMENSIONS=384
```

Model names are configuration, so they can be changed to models available to the API project.
Do not place an API key in the browser or commit `.env`.

Index the synthetic documents:

```powershell
supplyscope --database-url "sqlite:///./supplyscope.db" index-documents
```

Then use the web console or ask from the CLI:

```powershell
supplyscope --database-url "sqlite:///./supplyscope.db" ask `
  "Which delayed shipments could stop production, and what contractual remedies apply?" `
  --trace
```

## PostgreSQL And pgvector

With Docker Desktop running:

```powershell
docker compose up -d db
supplyscope init-db
supplyscope seed
supplyscope index-documents
supplyscope-web
```

The Compose service exposes PostgreSQL on local port `5433`. `init-db` enables pgvector and
creates the schema. In PostgreSQL, semantic ranking uses the pgvector cosine-distance operator.

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

The main correlated scenario includes:

- Toronto has 30 available `MCU-X100` units and 3.8 days of cover.
- Shipment `SS-CRITICAL-001` contains 800 replacement units from Apex Circuits.
- A port labor disruption delays the shipment by nine days.
- An incident report describes the interruption and possible air-freight recovery.
- The supplier agreement permits a 4% credit after a five-day grace period, subject to
  force-majeure review.

## Deterministic Access

| Persona | Role | Warehouse scope |
|---|---|---|
| `ava.admin@supplyscope.demo` | Global administrator | All |
| `noah.east@supplyscope.demo` | Regional operations | Toronto and Chicago |
| `mia.west@supplyscope.demo` | Regional operations | Vancouver and Austin |
| `priya.procurement@supplyscope.demo` | Procurement analyst | All |
| `leo.quality@supplyscope.demo` | Quality analyst | All |
| `sofia.viewer@supplyscope.demo` | Viewer | Toronto |

The access service resolves scope before the agent run. Tools inject organization and warehouse
filters from local context. Document retrieval applies the same warehouse and supplier scope
before keyword or vector ranking. Conversation reads are also restricted to their owning user.

## Agent Responsibilities

| Agent | Available tools |
|---|---|
| Shipment specialist | Delayed inbound shipments, tracking history |
| Inventory specialist | Current low stock, inventory history |
| Supplier-risk specialist | Risk ranking, scorecards, quality incidents |
| Contracts and compliance specialist | Scoped hybrid contract/report retrieval |
| Supervisor | The four specialists only; no direct database access |

The LLM chooses specialists and tool arguments. Authorization, joins, aggregation, risk-score
calculation, date limits, and SQL construction remain deterministic Python code.

## Evaluation

The golden cases cover cross-domain routing, contract retrieval, supplier ranking, inventory
scope, shipment tracking, and regional isolation:

```powershell
supplyscope --database-url "sqlite:///./supplyscope.db" evaluate
```

This command requires an API key. It checks expected specialists, evidence terms, forbidden
internal identifiers, and access-leakage indicators.

## Verification

```powershell
python -m pytest
python -m ruff check src tests
python -m ruff format --check src tests
```

## Project Layout

```text
src/supplyscope/
  access.py             deterministic authorization
  agent_service.py      OpenAI runner boundary
  agents/
    llm.py              supervisor, specialists, and function tools
    runtime.py          trusted local run context and tool trace
    supervisor.py       deterministic offline demonstration
  analytics.py          supplier, shipment, and inventory analytics
  conversations.py      scoped conversation persistence
  embeddings.py         OpenAI embedding provider and indexer
  retrieval.py          scoped hybrid retrieval
  models.py             relational, vector, and conversation schema
  synthetic.py          correlated synthetic-data generator
  tools.py              core typed database tools
  web.py                FastAPI application
  static/               responsive operations console
evals/cases.json         golden LLM evaluation cases
tests/                   authorization, tools, RAG, agents, API, and UI backend tests
.vscode/                 repeatable run, test, Docker, and debugger workflows
```

## Production Hardening

This is a complete portfolio implementation, not a production deployment. A production version
should add Alembic migrations, PostgreSQL row-level security, managed secrets, rate limiting,
background embedding jobs, model-cost monitoring, stronger identity authentication, and CI/CD.
