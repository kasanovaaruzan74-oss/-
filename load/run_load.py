import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import secrets
import socket
from statistics import mean
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep

import psutil
import requests


APP_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ((5, 60), (20, 120), (50, 180), (100, 180))


@dataclass(frozen=True)
class ScenarioResult:
    users: int
    duration_seconds: int
    requests: int
    failures: int
    failure_percent: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    requests_per_second: float
    mean_cpu_percent: float
    peak_cpu_percent: float
    mean_rss_mib: float
    peak_rss_mib: float


def ephemeral_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_server(process, host, log_path):
    deadline = monotonic() + 20
    while monotonic() < deadline and process.poll() is None:
        try:
            if requests.get(f"{host}/health", timeout=1).ok:
                return
        except requests.RequestException:
            pass
        sleep(0.2)
    raise RuntimeError(f"FastAPI did not start. See {log_path}.")


def read_token(host, authenticated):
    if not authenticated:
        return None
    credentials = {
        "email": "locust@example.invalid",
        "password": f"Locust9!{secrets.token_urlsafe(18)}",
    }
    response = requests.post(
        f"{host}/auth/register", json={**credentials, "full_name": "Locust Test Reader"}, timeout=10
    )
    response.raise_for_status()
    response = requests.post(f"{host}/auth/login", json=credentials, timeout=10)
    response.raise_for_status()
    return response.json()["access_token"]


def check_read_routes(host, token):
    if token:
        response = requests.get(f"{host}/documents", timeout=10)
        if response.status_code not in {401, 403}:
            raise RuntimeError("The load app did not enforce authentication.")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    for path in ("/documents", "/documents/lookups"):
        response = requests.get(f"{host}{path}", headers=headers, timeout=10)
        response.raise_for_status()


def measure_scenario(users, seconds, host, server, env, output, app_root):
    stem = output / f"users-{users:03}"
    launch_command = [
        sys.executable, "-m", "locust", "-f", str(Path(__file__).with_name("locustfile.py")),
        "--headless", "--host", host, "--users", str(users), "--spawn-rate", str(users),
        "--run-time", f"{seconds}s", "--only-summary", "--csv", str(stem),
    ]
    watched = psutil.Process(server.pid)
    started_at = monotonic()
    watched.cpu_percent()
    samples = []
    with stem.with_suffix(".log").open("w", encoding="utf-8") as log, subprocess.Popen(
        launch_command, cwd=app_root, env=env, stdout=log, stderr=subprocess.STDOUT
    ) as load_test:
        try:
            while load_test.poll() is None:
                sleep(1)
                if server.poll() is not None:
                    raise RuntimeError("FastAPI stopped during the load test.")
                samples.append({
                    "second": round(monotonic() - started_at, 2),
                    "cpu_percent": watched.cpu_percent(),
                    "rss_mib": round(watched.memory_info().rss / 1024**2, 3),
                })
        finally:
            if load_test.poll() is None:
                load_test.terminate()
        # Locust uses code 1 when one or more requests fail. Those failures belong in the report.
        if load_test.returncode not in {0, 1}:
            raise RuntimeError(f"Locust exited with {load_test.returncode}. See {stem.with_suffix('.log')}.")

    with output.joinpath(f"{stem.name}_resources.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("second", "cpu_percent", "rss_mib"))
        writer.writeheader()
        writer.writerows(samples)
    with output.joinpath(f"{stem.name}_stats.csv").open(encoding="utf-8", newline="") as stream:
        aggregate = next(row for row in csv.DictReader(stream) if row["Name"] == "Aggregated")
    request_count = int(aggregate["Request Count"])
    failures = int(aggregate["Failure Count"])
    if request_count == 0 or not samples:
        raise RuntimeError(f"Locust did not generate measurements. See {stem.with_suffix('.log')}.")
    return ScenarioResult(
        users=users,
        duration_seconds=seconds,
        requests=request_count,
        failures=failures,
        failure_percent=100 * failures / request_count,
        mean_ms=float(aggregate["Average Response Time"]),
        p50_ms=float(aggregate["50%"]),
        p95_ms=float(aggregate["95%"]),
        requests_per_second=float(aggregate["Requests/s"]),
        mean_cpu_percent=mean(sample["cpu_percent"] for sample in samples),
        peak_cpu_percent=max(sample["cpu_percent"] for sample in samples),
        mean_rss_mib=mean(sample["rss_mib"] for sample in samples),
        peak_rss_mib=max(sample["rss_mib"] for sample in samples),
    )


def main():
    parser = argparse.ArgumentParser(description="Run the DeanFlow lab load test on a local disposable database.")
    parser.add_argument("--output", type=Path, required=True, help="Directory for Locust CSV and process metrics.")
    parser.add_argument("--app-root", type=Path, default=APP_ROOT, help="Directory that contains the app package.")
    parser.add_argument("--authenticated", action="store_true", help="Enable the lab 8 access controls.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--users", type=int, choices=(5, 20, 50, 100), help="Repeat one measured scenario.")
    selection.add_argument("--smoke", action="store_true", help="Run a single short check before the full benchmark.")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    scenarios = ((5, 5),) if args.smoke else tuple(
        scenario for scenario in SCENARIOS if args.users is None or scenario[0] == args.users
    )
    metadata = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "visible_process_cpus": len(psutil.Process().cpu_affinity()) if sys.platform == "linux" else os.cpu_count(),
        "packages": {name: version(name) for name in ("fastapi", "sqlmodel", "uvicorn", "locust", "psutil")},
        "server_workers": 1,
        "authenticated": args.authenticated,
        "scenarios": [],
    }
    with TemporaryDirectory(prefix="deanflow-lab08-") as temp:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite:///{Path(temp, 'load.sqlite').as_posix()}"
        for name in ("DEANFLOW_LOAD_TOKEN", "DEANFLOW_ADMIN_EMAIL", "DEANFLOW_ADMIN_PASSWORD"):
            env.pop(name, None)
        env["AUTH_REQUIRED"] = str(args.authenticated).lower()
        port = ephemeral_port()
        host = f"http://127.0.0.1:{port}"
        server_log = Path(temp) / "uvicorn.log"
        subprocess.run([sys.executable, "-m", "app.database.seed"], cwd=args.app_root.resolve(), env=env, check=True)
        with server_log.open("w", encoding="utf-8") as log, subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
             "--workers", "1", "--no-access-log"],
            cwd=args.app_root.resolve(),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        ) as server:
            try:
                wait_for_server(server, host, server_log)
                token = read_token(host, args.authenticated)
                check_read_routes(host, token)
                if token:
                    env["DEANFLOW_LOAD_TOKEN"] = token
                for users, seconds in scenarios:
                    print(f"Locust: {users} users for {seconds} seconds", flush=True)
                    result = measure_scenario(users, seconds, host, server, env, output, args.app_root.resolve())
                    metadata["scenarios"].append(asdict(result))
                    output.joinpath("summary.json").write_text(
                        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                    )
                    print(f"  {result.requests} requests; {result.failures} failures; p95 {result.p95_ms:g} ms", flush=True)
            except Exception:
                print(server_log.read_text(encoding="utf-8"), file=sys.stderr)
                raise
            finally:
                if server.poll() is None:
                    server.terminate()
                    server.wait(timeout=10)


if __name__ == "__main__":
    main()
