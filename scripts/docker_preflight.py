"""Fail quickly when the Docker CLI exists but its engine is unavailable."""

from __future__ import annotations

import subprocess

DOCKER_PREFLIGHT_TIMEOUT_SECONDS = 45


def main() -> None:
    try:
        result = subprocess.run(
            ["docker", "info"],
            check=False,
            capture_output=True,
            text=True,
            timeout=DOCKER_PREFLIGHT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise SystemExit("docker smoke unavailable: docker CLI was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(
            "docker smoke unavailable: Docker engine did not respond within "
            f"{DOCKER_PREFLIGHT_TIMEOUT_SECONDS} seconds"
        ) from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        message = detail[-1] if detail else f"docker info exited {result.returncode}"
        raise SystemExit(f"docker smoke unavailable: {message}")
    print("docker preflight: engine=ready")


if __name__ == "__main__":
    main()
