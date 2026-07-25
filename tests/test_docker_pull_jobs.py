from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "src" / "pallas_plugin_protocol"
_PKG = "pallas_plugin_protocol_docker_pull_test"


def load_module(qualified: str, filename: str):
    path = _ROOT / filename
    spec = importlib.util.spec_from_file_location(qualified, path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = mod
    spec.loader.exec_module(mod)
    return mod


if _PKG not in sys.modules:
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_ROOT)]
    sys.modules[_PKG] = pkg

docker_pull_jobs = load_module(f"{_PKG}.docker_pull_jobs", "docker_pull_jobs.py")


def test_estimate_pull_percent_advances() -> None:
    assert docker_pull_jobs.estimate_pull_percent("Pulling fs layer", 5) >= 15
    assert docker_pull_jobs.estimate_pull_percent("Downloading [=>] 10MB", 15) >= 25
    # 单层 Download complete 不应直接跳到 70%
    assert docker_pull_jobs.estimate_pull_percent("Download complete", 40) < 70
    assert docker_pull_jobs.estimate_pull_percent("Download complete", 40) >= 40
    assert docker_pull_jobs.estimate_pull_percent("abc: Pull complete", 40) >= 48
    assert docker_pull_jobs.estimate_pull_percent("Status: Image is up to date", 80) >= 90
    # docker 层内百分比应推动总进度
    assert docker_pull_jobs.estimate_pull_percent("Downloading 50%", 15) >= 40
    assert docker_pull_jobs.estimate_pull_percent("Downloading [========>]  72.5%", 20) >= 60


def test_find_running_job_same_image() -> None:
    coord = docker_pull_jobs.DockerPullCoordinator()
    job = docker_pull_jobs.DockerPullJobState(
        job_id="abc",
        protocol="snowluma",
        image="motricseven7/snowluma:latest",
        status="running",
    )
    coord._jobs["abc"] = job
    assert coord.find_running_job(image="motricseven7/snowluma:latest", protocol="snowluma") == "abc"
    assert coord.find_running_job(image="other:latest", protocol="snowluma") is None
    job.status = "completed"
    assert coord.find_running_job(image="motricseven7/snowluma:latest", protocol="snowluma") is None


def test_docker_pull_job_to_dict_omits_lines() -> None:
    job = docker_pull_jobs.DockerPullJobState(
        job_id="abc",
        protocol="snowluma",
        image="motricseven7/snowluma:latest",
    )
    job.lines.append("hello")
    data = job.to_dict()
    assert "lines" not in data
    assert data["job_id"] == "abc"
    assert data["protocol"] == "snowluma"
