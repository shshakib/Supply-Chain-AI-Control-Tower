# Private Deployment and Verification

This is a synthetic-data portfolio application, not a public production service. Keep the default
loopback bindings. Selecting a demo persona is not authentication. Do not expose these ports through
a router, tunnel, or public reverse proxy yet.

## Local Acceptance Check

```powershell
docker compose up --detach --build
python scripts/smoke_deployment.py
```

Startup runs migrations and seeds only an empty database before starting the web service. The smoke
check verifies PostgreSQL, seeded documents, the MCP connection, and a complete offline answer with
citations and trace events. It makes no model or embedding API calls. Open http://127.0.0.1:8000.

CI additionally tests actual pgvector retrieval with fixture embeddings, warehouse isolation, and
a real Streamable HTTP MCP tool invocation. No API secret is needed for CI. Unit tests simulate
provider failures, invalid output, timeouts, token limits, and unsupported citations.

## Publish and Run a Verified Image

In GitHub Actions, manually run **CI** on `main` and enable **publish_image**. Configure approval
rules on the GitHub `release` environment first if releases need review. Only after all checks pass
does the job publish the *same image* exercised by the Docker smoke test to GitHub Container Registry
(GHCR). There is no automatic public hosting deployment and Docker Hub is not required.

The image tag is the commit SHA. Record the registry digest and deploy by digest for an immutable
selection; a tag alone can be overwritten. Authenticate with GHCR if the package is private.

```powershell
$env:CONTROL_TOWER_IMAGE = "ghcr.io/shshakib/supply-chain-ai-control-tower@sha256:YOUR_DIGEST"
docker compose pull web db
docker compose up --detach --no-build
python scripts/smoke_deployment.py
```

Keep `.env` and API credentials outside Git and images. The Compose database credentials are only
for the isolated local demo. Before any shared deployment, replace them and use managed secrets.

## Upgrade and Recovery

Back up PostgreSQL before upgrading an existing volume. For this local demo:

```powershell
docker compose exec db pg_dump -U control_tower -d control_tower -Fc -f /tmp/control-tower.dump
docker compose cp db:/tmp/control-tower.dump ./control-tower.dump
```

Store backups securely outside the repository and test restoration into a separate database. Do not
run `docker compose down --volumes` on a database you want to keep. Roll back the app using the
previous image digest only if it supports the current schema; otherwise restore a verified backup
into a separate database. Do not blindly downgrade migrations with user data.

## Paid Evaluation

`control-tower evaluate` uses a real API key and costs money. Each case has the same per-question
limits as chat. Cases check observed tool use, nonempty evidence sources, expected/forbidden terms,
and citations against returned tool references. Errors count as failed cases and a failed run exits
nonzero. These checks do not prove that every claim is entailed by its cited text; human review of
representative answers is still required before release. Evaluations are not run automatically in CI.

## Before Public Hosting

The main local workflow is implemented. The following work remains before public hosting:

- Real identity verification, tenant binding, and authorization at every API boundary.
- HTTPS, secure secret storage, non-demo database credentials, and private database/MCP networking.
- Per-user rate limits, concurrency limits, and provider/account-level spending controls.
- Backup restoration tests, monitoring/alerts, dependency/security checks, and data retention policy.
- Real-model evaluation of the exact deployed models and prompts.

Passing the local pipeline is portfolio readiness, not a claim of production security.
