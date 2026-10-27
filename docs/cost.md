# Cost

- **Local development / CI:** free. Scripted model, DuckDB, no cloud, no API keys.
- **LLM runs:** free with Ollama on your machine or the Gemini free tier. Each investigation is capped at 40 tool calls (`MAX_STEPS`) and 2 skeptic rounds; the scripted pipeline uses ~7 tool calls per investigation, so an LLM run is roughly 8 model calls.
- **GCP (optional):** designed for the free tier: one small GCS bucket, BigQuery sandbox-sized data (~15k rows/day), one nightly Cloud Run job. Expect cents per month. `make cloud-down` removes everything; set a budget alert before applying.
- Token counts per run are recorded in `evals/results/*.json` (`tokens`) for LLM configs.
