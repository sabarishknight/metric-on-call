"""Metric On-Call portal: a local web app (Starlette) that serves the UI and a small JSON API.

    make portal            # local DuckDB data
    make portal-cloud      # reads the BigQuery dataset instead

Binds to 127.0.0.1 only. Nothing runs on a schedule: an investigation starts when someone clicks Run.
"""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from portal import service

STATIC = Path(__file__).parent / "static"
NO_CACHE = {"Cache-Control": "no-store"}


def _json(data, status: int = 200) -> Response:
    # service.clean() already made everything JSON-safe; this keeps responses compact and cache-free
    return Response(json.dumps(data, default=str), status_code=status, media_type="application/json", headers=NO_CACHE)


def _not_ready() -> Response:
    return _json({"error": "data is still loading", "ready": False}, 503)


async def index(request: Request) -> Response:
    return FileResponse(STATIC / "index.html", headers=NO_CACHE)


async def api_meta(request: Request) -> Response:
    return _json(service.meta())


async def api_incidents(request: Request) -> Response:
    return _json(service.incidents())


async def api_truth(request: Request) -> Response:
    t = service.truth_for(int(request.path_params["incident_id"]))
    return _json(t, 200) if t is not None else _json({"error": "unknown incident"}, 404)


async def api_agents(request: Request) -> Response:
    return _json(service.agents())


async def api_dashboard(request: Request) -> Response:
    if not service.meta()["ready"]:
        return _not_ready()
    return _json(service.dashboard())


async def api_system(request: Request) -> Response:
    return _json(service.system())


async def api_published(request: Request) -> Response:
    return _json(service.published())


async def api_runs(request: Request) -> Response:
    return _json(service.history())


async def api_start_run(request: Request) -> Response:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    try:
        run = service.start_run(int(body.get("incident_id")), str(body.get("provider", "scripted")), float(body.get("pace", 0)))
    except (TypeError, ValueError) as e:
        return _json({"error": f"bad request: {e}"}, 400)
    except KeyError as e:
        return _json({"error": str(e)}, 404)
    except RuntimeError as e:
        return _json({"error": str(e)}, 409)
    return _json({"run_id": run.id})


async def api_run(request: Request) -> Response:
    run = service.RUNS.get(request.path_params["run_id"])
    if run is None:
        service.history()  # lazily load runs saved by an earlier session
        run = service.RUNS.get(request.path_params["run_id"])
    if run is None:
        return _json({"error": "unknown run"}, 404)
    return _json(run.snapshot(int(request.query_params.get("since", 0))))


async def api_decision(request: Request) -> Response:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    try:
        run = service.decide(request.path_params["run_id"], str(body.get("action", "")), str(body.get("comment", "")))
    except KeyError:
        return _json({"error": "unknown run"}, 404)
    except (RuntimeError, ValueError) as e:
        return _json({"error": str(e)}, 409)
    return _json(run.snapshot(0))


def create_app(warm: bool = True) -> Starlette:
    app = Starlette(routes=[
        Route("/", index),
        Route("/api/meta", api_meta),
        Route("/api/incidents", api_incidents),
        Route("/api/incidents/{incident_id:int}/truth", api_truth),
        Route("/api/agents", api_agents),
        Route("/api/dashboard", api_dashboard),
        Route("/api/system", api_system),
        Route("/api/published", api_published),
        Route("/api/runs", api_runs, methods=["GET"]),
        Route("/api/runs", api_start_run, methods=["POST"]),
        Route("/api/runs/{run_id}", api_run, methods=["GET"]),
        Route("/api/runs/{run_id}/decision", api_decision, methods=["POST"]),
        Mount("/static", StaticFiles(directory=str(STATIC)), name="static"),
    ])
    if warm:
        service.warm_up()
    return app


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    a = ap.parse_args()
    url = f"http://127.0.0.1:{a.port}"
    print(f"\n  Metric On-Call portal  ->  {url}\n  (Ctrl+C to stop. Nothing runs unless you click Run.)\n")
    if not a.no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
