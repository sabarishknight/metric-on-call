# CLAUDE.md: rules for working in this repo

`README.md` is the spec and `PLAN.md` is the build order. Read both before changing anything.

## Working rules
1. README is the spec. If code and README disagree, stop and ask, or update README in the same commit.
2. Agents never write free-form SQL. All data access goes through parameterised, unit-tested tools in `tools/`.
3. Deterministic tools do the maths; LLMs only route, weigh and write.
4. One definition of each metric. Metric logic lives in `tools/metrics.py` (mirrored by `warehouse/models/marts` and the dbt semantic metrics), never inside agents.
5. Same seed = same data. Never hand-edit generated data. Change `generator/` and rerun `make data`.
6. Every phase ends with: tests green, a `make` target works, README status table updated, one commit/PR.
7. No result numbers in README until `evals/results/` has real output.
8. Local mode must always work with zero cloud credentials.
9. Never commit secrets. `.env.example` only.
10. Held-out incidents (see `generator/incidents.yaml`, `held_out: true`) are never used for prompt tuning. `evals/holdout.py` enforces this in code.

## Layout
- `common/` config, warehouse access, shared schemas
- `generator/` seeded synthetic data + `incidents.yaml` ground truth
- `tools/` deterministic analysis tools + MCP server
- `agents/` LangGraph team (nodes are plain functions; graph is thin wiring)
- `evals/` harness, scorers, results
- `warehouse/` dbt project (DuckDB locally, BigQuery in cloud)
- `infra/`, `pipelines/` Google Cloud
- `app/` Streamlit console

## Commands
`make setup` · `make data` · `make test` · `make dbt` · `make demo` · `make eval-smoke`

## Style
Python 3.11+, type hints, pydantic models at tool boundaries, `ruff` clean. Prefer small pure functions.
