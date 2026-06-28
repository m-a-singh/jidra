"""
Spring Boot actuator client for fetching bean metadata.
Supports both direct URL queries and ephemeral Docker container lifecycle (docker-compose or Dockerfile).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Generator


class ActuatorError(Exception):
    """Raised when actuator operations fail."""

    pass


def fetch_beans_from_url(actuator_base_url: str, timeout: int = 30) -> dict:
    """
    Fetch /actuator/beans from a running Spring Boot app.

    Args:
        actuator_base_url: Base URL (e.g., http://localhost:8080). Must not include trailing /.
        timeout: Request timeout in seconds.

    Returns:
        Raw /actuator/beans response dict.

    Raises:
        ActuatorError: If fetch fails or response is invalid.
    """
    url = f"{actuator_base_url.rstrip('/')}/actuator/beans"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        raise ActuatorError(f"Failed to fetch {url}: {e}") from e


def _wait_for_health(
    actuator_base_url: str, timeout: int = 120, poll_interval: float = 1.0
) -> None:
    """
    Poll /actuator/beans until accessible (indicates app is ready).

    Raises:
        ActuatorError: If health check fails or times out.
    """
    end_time = time.time() + timeout
    elapsed = 0
    while time.time() < end_time:
        try:
            with urllib.request.urlopen(
                f"{actuator_base_url.rstrip('/')}/actuator/beans", timeout=5
            ) as response:
                # If we can read /actuator/beans, the app is ready
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(poll_interval)
        elapsed += poll_interval
        if int(elapsed) % 10 == 0 and elapsed > 0:
            print(f"  ⏳ Still waiting... ({int(elapsed)}s / {timeout}s)", flush=True)
    raise ActuatorError(f"Actuator endpoint did not become ready within {timeout}s")


def _get_service_container_name(compose_file: Path) -> str | None:
    """Return the running container name for the service-profile service."""
    try:
        import yaml

        with open(compose_file) as f:
            compose = yaml.safe_load(f)
        for name, service in (compose.get("services") or {}).items():
            if "service" in (service.get("profiles") or []):
                result = subprocess.run(
                    ["docker-compose", "ps", "-q", name],
                    cwd=compose_file.parent,
                    capture_output=True,
                    text=True,
                )
                container_id = result.stdout.strip()
                if container_id:
                    name_result = subprocess.run(
                        ["docker", "inspect", "--format", "{{.Name}}", container_id],
                        capture_output=True,
                        text=True,
                    )
                    return name_result.stdout.strip().lstrip("/")
        return None
    except Exception:
        return None


def _fetch_beans_via_exec(container_name: str, port: int) -> dict:
    """Fetch /actuator/beans from inside a container via docker exec."""
    result = subprocess.run(
        [
            "docker",
            "exec",
            container_name,
            "curl",
            "-s",
            f"http://localhost:{port}/actuator/beans",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise ActuatorError(f"docker exec curl failed: {result.stderr}")
    return json.loads(result.stdout)


def _wait_for_health_via_exec(
    container_name: str, port: int, timeout: int = 120, poll_interval: float = 2.0
) -> None:
    """Poll /actuator/beans inside the container until it responds."""
    end_time = time.time() + timeout
    elapsed = 0
    while time.time() < end_time:
        result = subprocess.run(
            [
                "docker",
                "exec",
                container_name,
                "curl",
                "-sf",
                f"http://localhost:{port}/actuator/beans",
            ],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            return
        time.sleep(poll_interval)
        elapsed += poll_interval
        if int(elapsed) % 10 == 0 and elapsed > 0:
            print(f"  ⏳ Still waiting... ({int(elapsed)}s / {timeout}s)", flush=True)
    raise ActuatorError(f"Actuator endpoint did not become ready within {timeout}s")


def _find_docker_compose(codebase_root: str) -> Path | None:
    """Check if docker-compose.yml exists in codebase root."""
    compose_file = Path(codebase_root) / "docker-compose.yml"
    if compose_file.exists():
        return compose_file
    return None


def _extract_port_from_compose(compose_file: Path) -> int | None:
    """Return the host port of the service tagged with profiles: [service]."""
    try:
        import yaml

        with open(compose_file) as f:
            compose = yaml.safe_load(f)
        for service in (compose.get("services") or {}).values():
            profiles = service.get("profiles") or []
            if "service" not in profiles:
                continue
            for port_mapping in service.get("ports") or []:
                if isinstance(port_mapping, int):
                    return port_mapping
                if isinstance(port_mapping, str):
                    parts = port_mapping.split(":")
                    try:
                        return int(parts[0])
                    except ValueError:
                        continue
        return None
    except Exception:
        return None


def _find_dockerfile(codebase_root: str) -> Path:
    """Find Dockerfile in codebase root. Raises ActuatorError if not found."""
    dockerfile = Path(codebase_root) / "Dockerfile"
    if dockerfile.exists():
        return dockerfile

    raise ActuatorError(
        f"No docker-compose.yml or Dockerfile found in {codebase_root}. "
        "Enterprise deployments typically use docker-compose.yml."
    )


_TEST_DIR_PATTERNS = {
    "integration-tests",
    "integration-test",
    "integrationtests",
    "integrationtest",
    "e2e",
    "e2e-tests",
    "e2e-test",
    "functional-tests",
    "functional-test",
    "acceptance-tests",
    "acceptance-test",
    "test",
    "tests",
    "it",
    "ittest",
    "it-tests",
    "perf-tests",
    "performance-tests",
    "load-tests",
    "contract-tests",
    "contract-test",
}


def _is_test_dir(subdir: Path) -> bool:
    name = subdir.name.lower()
    return (
        name in _TEST_DIR_PATTERNS or name.endswith("-tests") or name.endswith("-test")
    )


def _detect_build_directories(codebase_root: str) -> list[tuple[str, Path]]:
    """
    Detect all build tool directories: gradle or maven.

    Returns list of (tool_name, build_directory) tuples.
    For multi-module projects, returns submodules with build tools, skipping test modules.
    """
    root = Path(codebase_root)
    root_candidates = []
    submodule_candidates = []

    # Check root
    if (
        (root / "gradlew").exists()
        or (root / "build.gradle").exists()
        or (root / "build.gradle.kts").exists()
    ):
        root_candidates.append(("gradle", root))
    if (root / "mvnw").exists() or (root / "pom.xml").exists():
        root_candidates.append(("maven", root))

    # For multi-module: look for build tools in subdirectories, skip test modules
    seen = set()
    for subdir in sorted(root.glob("*/")):
        if subdir.name.startswith(".") or _is_test_dir(subdir):
            continue
        if subdir in seen:
            continue
        if (
            (subdir / "gradlew").exists()
            or (subdir / "build.gradle").exists()
            or (subdir / "build.gradle.kts").exists()
        ):
            submodule_candidates.append(("gradle", subdir))
            seen.add(subdir)
        elif (subdir / "mvnw").exists() or (subdir / "pom.xml").exists():
            submodule_candidates.append(("maven", subdir))
            seen.add(subdir)

    # Submodules with their own wrapper take priority over root
    if submodule_candidates:
        with_wrapper = [
            c
            for c in submodule_candidates
            if (c[1] / ("gradlew" if c[0] == "gradle" else "mvnw")).exists()
        ]
        return with_wrapper if with_wrapper else submodule_candidates

    return root_candidates


def _detect_build_tool(
    codebase_root: str, build_dir: str | None = None
) -> tuple[str, Path]:
    """
    Detect build tool and return (tool, build_directory).

    Args:
        codebase_root: Root of codebase
        build_dir: Optional explicit build directory (relative to codebase_root)

    Returns:
        (tool_name, build_directory) tuple

    Raises:
        ActuatorError: If build tool not found or multiple submodules with no explicit --build-dir
    """
    root = Path(codebase_root)

    # If explicit build_dir provided, use it
    if build_dir:
        explicit_dir = root / build_dir
        if not explicit_dir.exists():
            raise ActuatorError(f"Build directory not found: {explicit_dir}")

        # Prefer Maven if both exist (more reliable for toolchain issues)
        if (explicit_dir / "mvnw").exists() or (explicit_dir / "pom.xml").exists():
            return ("maven", explicit_dir)
        elif (explicit_dir / "gradlew").exists() or (
            explicit_dir / "build.gradle"
        ).exists():
            return ("gradle", explicit_dir)
        else:
            raise ActuatorError(f"No build tool found in {explicit_dir}")

    # Auto-detect: find all build directories
    candidates = _detect_build_directories(codebase_root)

    if not candidates:
        raise ActuatorError(
            f"No build tool found in {codebase_root}. "
            "Please run 'gradle build' or 'mvn package' manually first, "
            "or specify --build-dir if using a multi-module project."
        )

    if len(candidates) == 1:
        # Single submodule: auto-use it
        return candidates[0]

    # Multiple submodules: require explicit choice
    submodule_list = "\n".join(f"  - {d.relative_to(root)}" for _, d in candidates)
    raise ActuatorError(
        f"Multiple build directories detected in {codebase_root}:\n{submodule_list}\n"
        f"Please specify one with: --build-dir <relative-path>\n"
        f"Example: --build-dir search-api"
    )


def _build_java_app(codebase_root: str, build_dir: str | None = None) -> None:
    """
    Build Java app using detected build tool with clean build.

    Args:
        codebase_root: Root of codebase
        build_dir: Optional explicit build directory (relative to codebase_root)

    Raises:
        ActuatorError: If build fails or no build tool found.
    """
    build_tool, build_path = _detect_build_tool(codebase_root, build_dir)
    root = Path(codebase_root)

    if build_tool == "gradle":
        # Use bootJar to create Spring Boot executable JAR, exclude tests to avoid Java 17 toolchain issues
        cmd = ["./gradlew", "bootJar", "-x", "test", "--no-daemon"]
    else:  # maven
        cmd = ["./mvnw", "clean", "package", "-DskipTests"]

    # Show which directory we're building from
    if build_path != root:
        print(f"Building from submodule: {build_path.relative_to(root)}", flush=True)

    print(f"Building Java app with {build_tool}...", flush=True)
    try:
        result_code = subprocess.run(
            cmd,
            cwd=build_path,
            timeout=900,  # 15 minutes
        ).returncode

        if result_code != 0:
            raise ActuatorError(
                f"Build failed with {build_tool} (exit code {result_code})"
            )
        print("✓ Build successful", flush=True)

        # Verify artifacts exist - check both build_path and root
        gradle_jars = (
            list((build_path / "build" / "libs").glob("*.jar"))
            if (build_path / "build" / "libs").exists()
            else []
        )
        maven_jars = (
            list((build_path / "target").glob("*.jar"))
            if (build_path / "target").exists()
            else []
        )
        # For multi-module, also check root
        root_jars = (
            list((root / "build" / "libs").glob("*.jar"))
            if (root / "build" / "libs").exists()
            else []
        )

        all_jars = gradle_jars + maven_jars + root_jars
        if not all_jars:
            # List what's in the build directories for debugging
            build_dirs = []
            if (build_path / "build" / "libs").exists():
                build_dirs.append(
                    f"  {build_path / 'build' / 'libs'}: {list((build_path / 'build' / 'libs').glob('*'))}"
                )
            if (build_path / "target").exists():
                build_dirs.append(
                    f"  {build_path / 'target'}: {list((build_path / 'target').glob('*'))}"
                )
            if build_path != root and (root / "build" / "libs").exists():
                build_dirs.append(
                    f"  {root / 'build' / 'libs'}: {list((root / 'build' / 'libs').glob('*'))}"
                )

            raise ActuatorError(
                f"Build succeeded but no JAR artifacts found!\n"
                f"Build directories:\n{chr(10).join(build_dirs) if build_dirs else '  (none found)'}\n"
                f"Check your Dockerfile for the expected JAR path."
            )

        print(
            f"✓ Found {len(all_jars)} JAR file(s): {', '.join(str(j.name) for j in all_jars[:3])}"
        )

    except subprocess.TimeoutExpired:
        raise ActuatorError("Build timeout after 15 minutes") from None


def _compute_image_tag(codebase_root: str) -> str:
    """Compute a deterministic image tag from codebase path."""
    digest = hashlib.sha256(codebase_root.encode()).hexdigest()[:8]
    return f"jidra-validate-{digest}"


@contextmanager
def run_docker_and_fetch_beans(
    codebase_root: str,
    port: int = 8080,
    timeout: int = 120,
    skip_build: bool = False,
    build_dir: str | None = None,
) -> Generator[dict, None, None]:
    """
    Build and run all Docker services, fetch actuator beans, then tear down.

    Uses docker-compose if present (starts/stops all services), otherwise falls back to a
    single Dockerfile container. Context manager — yields the /actuator/beans response dict.

    Args:
        codebase_root: Path to Java repo with docker-compose.yml or Dockerfile.
        port: Host port actuator is listening on (auto-detected from compose if possible).
        timeout: Max seconds to wait for app startup.
        skip_build: If True, skip auto-building Java app (assume already built).
        build_dir: Optional build directory (relative to codebase_root) for multi-module projects.

    Yields:
        /actuator/beans response dict.

    Raises:
        ActuatorError: If docker-compose/Dockerfile not found, build fails, or health check fails.
    """
    root = Path(codebase_root)
    compose_file = _find_docker_compose(codebase_root)
    use_compose = compose_file is not None

    if use_compose:
        detected_port = _extract_port_from_compose(compose_file)
        if detected_port:
            port = detected_port
            print(
                f"Auto-detected port {port} from docker-compose.yml (service profile)",
                flush=True,
            )

    actuator_url = f"http://localhost:{port}"
    # Track image_tag so cleanup can reference it even if set before a failure
    image_tag: str | None = None

    def _cleanup() -> None:
        """Stop and remove all containers, swallowing errors so the original exception surfaces."""
        print("Stopping Docker services...", flush=True)
        try:
            if use_compose:
                subprocess.run(
                    [
                        "docker-compose",
                        "--profile",
                        "service",
                        "down",
                        "--remove-orphans",
                    ],
                    cwd=root,
                    capture_output=True,
                    timeout=60,
                )
            elif image_tag:
                for cmd in [
                    ["docker", "stop", image_tag],
                    ["docker", "rm", image_tag],
                    ["docker", "rmi", image_tag],
                ]:
                    subprocess.run(cmd, capture_output=True, timeout=30)
            print("✓ Cleanup complete", flush=True)
        except Exception as cleanup_err:
            print(f"⚠ Cleanup warning (non-fatal): {cleanup_err}", flush=True)

    try:
        if skip_build:
            print("⊘ Skipping Java build (--skip-build)", flush=True)
        else:
            _build_java_app(codebase_root, build_dir)

        if use_compose:
            print("Using docker-compose.yml — starting all services...", flush=True)
            subprocess.run(
                ["docker-compose", "down", "--remove-orphans"],
                cwd=root,
                capture_output=True,
                timeout=60,
            )
            subprocess.run(
                ["docker-compose", "--profile", "service", "up", "-d", "--build"],
                cwd=root,
                check=True,
                timeout=300,
            )
            print("✓ All services started", flush=True)

        else:
            print("Using Dockerfile for single-container build...", flush=True)
            _find_dockerfile(codebase_root)
            image_tag = _compute_image_tag(codebase_root)

            print(f"Building Docker image: {image_tag}...", flush=True)
            subprocess.run(
                ["docker", "build", "-t", image_tag, str(codebase_root)],
                check=True,
                timeout=600,
            )
            print("✓ Docker image built", flush=True)

            print(f"Starting container on port {port}...", flush=True)
            subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "-p",
                    f"{port}:8080",
                    "--name",
                    image_tag,
                    image_tag,
                ],
                check=True,
                timeout=60,
            )
            print("✓ Container started", flush=True)

        # Wait for health and fetch beans — use docker exec if compose (avoids host proxy issues)
        print(f"Waiting for app to be ready (timeout: {timeout}s)...", flush=True)
        container_name = (
            _get_service_container_name(compose_file) if use_compose else None
        )

        if container_name:
            print(f"Using docker exec via container: {container_name}", flush=True)
            _wait_for_health_via_exec(container_name, port, timeout=timeout)
            print("✓ App is healthy", flush=True)
            print("Fetching /actuator/beans...", flush=True)
            beans = _fetch_beans_via_exec(container_name, port)
        else:
            _wait_for_health(actuator_url, timeout=timeout)
            print("✓ App is healthy", flush=True)
            print("Fetching /actuator/beans...", flush=True)
            beans = fetch_beans_from_url(actuator_url)

        total_bean_count = sum(
            len(ctx.get("beans", {})) for ctx in beans.get("contexts", {}).values()
        )
        print(f"✓ Fetched {total_bean_count} beans from actuator", flush=True)
        yield beans

    except subprocess.CalledProcessError as e:
        _cleanup()
        raise ActuatorError(f"Docker operation failed: {e}") from e
    except subprocess.TimeoutExpired as e:
        _cleanup()
        raise ActuatorError(f"Docker operation timed out: {e}") from e
    except ActuatorError:
        _cleanup()
        raise
    except KeyboardInterrupt:
        _cleanup()
        raise
    except Exception as e:
        _cleanup()
        raise ActuatorError(f"Unexpected error during Docker lifecycle: {e}") from e
    else:
        # Success path — normal teardown
        _cleanup()
