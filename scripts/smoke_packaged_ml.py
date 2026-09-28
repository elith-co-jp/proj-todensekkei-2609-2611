"""Exercise training and inference through the frozen Windows executable."""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _image() -> bytes:
    image = Image.new("RGB", (128, 128), "white")
    ImageDraw.Draw(image).rectangle((40, 40, 88, 88), fill="black")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _wait_for_server(base_url: str, process: subprocess.Popen, deadline: float) -> None:
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Packaged exe exited before startup: {process.returncode}")
        try:
            response = requests.get(f"{base_url}/api/health", timeout=2)
            if response.ok and response.json().get("desktop") is True:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise TimeoutError("Packaged exe did not start within 300 seconds")


def _request(method: str, url: str, **kwargs) -> dict:
    response = requests.request(method, url, timeout=60, **kwargs)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise RuntimeError(f"{method} {url} failed: {response.status_code} {response.text[:2000]}") from exc
    return response.json()


def main() -> None:
    executable = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path("dist/TodenYOLO.exe").resolve()
    if not executable.is_file():
        raise FileNotFoundError(executable)

    with tempfile.TemporaryDirectory(prefix="annotator-ml-smoke-") as temporary:
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update(
            SEQANNO_DATA_DIR=str(Path(temporary) / "data"),
            SEQANNO_PORT=str(port),
            SEQANNO_NO_BROWSER="1",
            SEQANNO_NO_DIALOG="1",
            SEQANNO_DESKTOP_IDLE_TIMEOUT_SECONDS="900",
            OMP_NUM_THREADS="2",
        )
        process = subprocess.Popen(
            [str(executable)], cwd=temporary, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            _wait_for_server(base_url, process, time.monotonic() + 300)
            status = _request("GET", f"{base_url}/api/ml/status")
            if status["ultralytics"] is not True or status["active_model"] is not None:
                raise AssertionError(f"Unexpected initial ML status: {status}")

            project = _request(
                "POST",
                f"{base_url}/api/projects",
                files={"files": ("smoke.png", _image(), "image/png")},
            )
            project_id = project["project_ids"][0]
            _request(
                "PUT",
                f"{base_url}/api/projects/{project_id}/annotations",
                json={
                    "symbols": [
                        {
                            "ref": "SMOKE-1",
                            "class_key": "relay_coil",
                            "cx": 0.5,
                            "cy": 0.5,
                            "w": 0.375,
                            "h": 0.375,
                            "terminals": [],
                        }
                    ],
                    "connections": [],
                },
            )

            run = _request(
                "POST",
                f"{base_url}/api/ml/training/run",
                json={"project_ids": [project_id], "only_done": False, "epochs": 1, "imgsz": 320},
            )
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                run = _request("GET", f"{base_url}/api/ml/training/runs/{run['id']}")
                if run["status"] != "running":
                    break
                time.sleep(3)
            if run["status"] != "success":
                raise AssertionError(f"Frozen training failed: {run['status']}\n{run.get('log_tail')}")
            if not run["base_model"].endswith("models\\yolov8n.pt"):
                raise AssertionError(f"Training did not use the bundled model: {run['base_model']}")

            result = _request(
                "POST",
                f"{base_url}/api/ml/inference/run",
                json={"project_ids": [project_id], "conf": 0.25},
            )
            if len(result["results"]) != 1 or result["model"]["id"] != run["result_model_id"]:
                raise AssertionError(f"Inference did not use the trained model: {result}")
            print("Frozen EXE training and inference succeeded")
        except Exception:
            error_log = executable.parent / "annotator_error.log"
            if error_log.is_file():
                print(error_log.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
            raise
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], check=False)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()
