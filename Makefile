.PHONY: setup data dbt test lint eval eval-llm eval-smoke demo portal portal-cloud mcp cloud-up cloud-down clean

PY ?= uv run python

setup:
	uv sync --extra agents --extra warehouse --extra app --extra obs --extra dev

data:            ## generate the synthetic chain + planted incidents (DuckDB + CSV)
	$(PY) -m generator.build

dbt:             ## build the dbt project on DuckDB and run its tests
	cd warehouse && uv run dbt deps && uv run dbt build --profiles-dir .

test:            ## unit tests for generator, tools, agents (no model calls)
	uv run pytest

lint:
	uv run ruff check .

eval:            ## full evaluation (needs MODEL_PROVIDER=ollama|gemini for LLM configs)
	$(PY) -m evals.run --config scripted
	$(PY) -m evals.run --config naive

eval-llm:        ## LLM agents with governed tools (MODEL_PROVIDER=ollama|gemini)
	$(PY) -m evals.run --config governed

eval-smoke:      ## offline smoke subset used in CI (scripted model, no API key)
	$(PY) -m evals.run --config scripted --smoke

demo:            ## Streamlit app in Replay mode, no API key needed
	uv run streamlit run app/streamlit_app.py

portal:          ## the web portal on local data; opens http://127.0.0.1:8765 (nothing runs until you click Run)
	$(PY) -m portal.server

portal-cloud:    ## the same portal reading BigQuery (needs gcloud sign-in; slower to start)
	WAREHOUSE=bigquery GCP_PROJECT=$${GCP_PROJECT:-metric-on-call} BQ_MAX_BYTES_BILLED=$${BQ_MAX_BYTES_BILLED:-5000000000} $(PY) -m portal.server

mcp:             ## start the read-only MCP tool server on stdio
	$(PY) -m tools.mcp_server

cloud-up:        ## verify locally, then deploy to Google Cloud (see scripts/deploy_gcp.sh)
	bash scripts/deploy_gcp.sh

cloud-down:
	cd infra && terraform destroy -var project=$${GCP_PROJECT:-metric-on-call} -var loader_image=unused

clean:
	rm -rf data/*.duckdb data/csv data/incident_truth.json .pytest_cache
