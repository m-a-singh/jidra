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


def _find_docker_compose(codebase_root: str) -> Path | None:
    """Check if docker-compose.yml exists in codebase root."""
    compose_file = Path(codebase_root) / "docker-compose.yml"
    if compose_file.exists():
        return compose_file
    return None


def _find_service_in_compose(compose_file: Path, service_name: str = "search") -> dict | None:
    """
    Parse docker-compose.yml and extract service config.

    Args:
        compose_file: Path to docker-compose.yml
        service_name: Name of service to find (default: search)

    Returns:
        Service config dict or None if not found.
    """
    try:
        import yaml

        with open(compose_file) as f:
            compose = yaml.safe_load(f)

        services = compose.get("services", {})
        return services.get(service_name)
    except Exception as e:
        raise ActuatorError(f"Failed to parse docker-compose.yml: {e}") from e


def _extract_port_from_compose(compose_file: Path, service_name: str = "search") -> int | None:
    """
    Extract the host port from docker-compose service.

    Looks for ports like "80:80" or "8080:8080" and returns the host port (first number).

    Args:
        compose_file: Path to docker-compose.yml
        service_name: Name of service to find (default: search)

    Returns:
        Host port number or None if not found.
    """
    try:
        service = _find_service_in_compose(compose_file, service_name)
        if not service:
            return None

        ports = service.get("ports", [])
        if not ports:
            return None

        # ports can be list of strings like ["80:80", "8001:8001"]
        for port_mapping in ports:
            if isinstance(port_mapping, str):
                # Format: "80:80" or "127.0.0.1:80:80"
                parts = port_mapping.split(":")
                host_port = parts[0]  # Get first part (host port)
                try:
                    return int(host_port)
                except ValueError:
                    continue
            elif isinstance(port_mapping, int):
                return port_mapping

        return None
    except Exception:
        return None


def _find_dockerfile(codebase_root: str) -> Path:
    """Find Dockerfile in codebase root or via docker-compose.yml. Raises ActuatorError if not found."""
    # Try docker-compose first
    compose_file = _find_docker_compose(codebase_root)
    if compose_file:
        try:
            service = _find_service_in_compose(compose_file)
            if service and "build" in service:
                build_config = service["build"]
                if isinstance(build_config, dict):
                    dockerfile_path = build_config.get("dockerfile", "Dockerfile")
                    context = build_config.get("context", ".")
                else:
                    dockerfile_path = "Dockerfile"
                    context = build_config if isinstance(build_config, str) else "."

                dockerfile = Path(codebase_root) / context / dockerfile_path
                if dockerfile.exists():
                    return dockerfile
        except Exception:
            pass

    # Fall back to root Dockerfile
    dockerfile = Path(codebase_root) / "Dockerfile"
    if dockerfile.exists():
        return dockerfile

    raise ActuatorError(
        f"No docker-compose.yml or Dockerfile found in {codebase_root}. "
        "Enterprise deployments typically use docker-compose.yml."
    )


def _detect_build_directories(codebase_root: str) -> list[tuple[str, Path]]:
    """
    Detect all build tool directories: gradle or maven.

    Returns list of (tool_name, build_directory) tuples.
    For multi-module projects, returns all submodules with build tools.
    Prioritizes submodules with their own gradlew/mvnw over root.
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

    # For multi-module: look for build tools in subdirectories
    for subdir in sorted(root.glob("*/")):
        if subdir.name.startswith("."):
            continue
        if (
            (subdir / "gradlew").exists()
            or (subdir / "build.gradle").exists()
            or (subdir / "build.gradle.kts").exists()
        ):
            submodule_candidates.append(("gradle", subdir))
        elif (subdir / "mvnw").exists() or (subdir / "pom.xml").exists():
            submodule_candidates.append(("maven", subdir))

    # Prioritize submodules with their own wrapper over root
    if submodule_candidates:
        # Filter to only submodules with their own wrapper script
        with_wrapper = [
            c
            for c in submodule_candidates
            if (c[1] / ("gradlew" if c[0] == "gradle" else "mvnw")).exists()
        ]
        if with_wrapper:
            return with_wrapper + submodule_candidates

    return submodule_candidates + root_candidates


def _detect_build_tool(codebase_root: str, build_dir: str | None = None) -> tuple[str, Path]:
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
        elif (explicit_dir / "gradlew").exists() or (explicit_dir / "build.gradle").exists():
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
            raise ActuatorError(f"Build failed with {build_tool} (exit code {result_code})")
        print("✓ Build successful", flush=True)

        # Verify artifacts exist - check both build_path and root
        gradle_jars = (
            list((build_path / "build" / "libs").glob("*.jar"))
            if (build_path / "build" / "libs").exists()
            else []
        )
        maven_jars = (
            list((build_path / "target").glob("*.jar")) if (build_path / "target").exists() else []
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
    service_name: str = "search",
    build_dir: str | None = None,
) -> Generator[dict, None, None]:
    """
    Build and run a Docker container using docker-compose or Dockerfile, fetch actuator beans, then cleanup.

    Context manager that yields the /actuator/beans response dict.
    Automatically stops and removes containers on exit.

    Args:
        codebase_root: Path to Java repo with docker-compose.yml or Dockerfile.
        port: Host port to map to container's 8080.
        timeout: Max seconds to wait for app startup.
        skip_build: If True, skip auto-building Java app (assume already built).
        service_name: Service name in docker-compose.yml (default: search).
        build_dir: Optional build directory (relative to codebase_root) for multi-module projects.

    Yields:
        /actuator/beans response dict.

    Raises:
        ActuatorError: If docker-compose/Dockerfile not found, build fails, or health check fails.
    """
    root = Path(codebase_root)
    compose_file = _find_docker_compose(codebase_root)
    use_compose = compose_file is not None

    # Auto-detect port from docker-compose if available
    if use_compose:
        detected_port = _extract_port_from_compose(compose_file, service_name)
        if detected_port:
            port = detected_port
            print(f"Auto-detected port {port} from docker-compose.yml", flush=True)

    actuator_url = f"http://localhost:{port}"

    try:
        # Auto-build Java app (unless explicitly skipped)
        if skip_build:
            print("⊘ Skipping Java build (--skip-build)", flush=True)
        else:
            _build_java_app(codebase_root, build_dir)

        if use_compose:
            print("Using docker-compose.yml for orchestration...", flush=True)
            # Check if service exists in docker-compose
            service = _find_service_in_compose(compose_file, service_name)
            if not service:
                # Service not found in compose - fall back to Dockerfile
                print(
                    f"Service '{service_name}' not found in docker-compose.yml, falling back to Dockerfile...",
                    flush=True,
                )
                use_compose = False
            else:
                # Clean up any orphaned containers ONLY when service is confirmed
                print("Cleaning up orphaned containers...", flush=True)
                subprocess.run(
                    ["docker-compose", "down", "--remove-orphans"],
                    cwd=root,
                    capture_output=True,
                    timeout=60,
                )

                print("Starting all services with docker-compose up...", flush=True)
                try:
                    subprocess.run(
                        ["docker-compose", "up", "-d", "--build"],
                        cwd=root,
                        check=True,
                        timeout=300,
                    )
                except subprocess.CalledProcessError as e:
                    print(
                        f"docker-compose up failed: {e.stderr if e.stderr else str(e)}", flush=True
                    )
                    raise ActuatorError(f"Failed to start docker-compose: {e}") from e
                print("✓ All services started", flush=True)

        if not use_compose:
            print("Using Dockerfile for single-container build...", flush=True)
            _find_dockerfile(codebase_root)
            image_tag = _compute_image_tag(codebase_root)

            # Build Docker image
            print(f"Building Docker image: {image_tag}...", flush=True)
            subprocess.run(
                ["docker", "build", "-t", image_tag, str(codebase_root)],
                check=True,
                timeout=600,
            )
            print("✓ Docker image built", flush=True)

            # Run container
            print(f"Starting container on port {port}...", flush=True)
            subprocess.run(
                ["docker", "run", "-d", "-p", f"{port}:8080", "--name", image_tag, image_tag],
                check=True,
                timeout=60,
            )
            print("✓ Container started", flush=True)

        # Wait for health
        print(f"Waiting for app to be ready (timeout: {timeout}s)...", flush=True)
        _wait_for_health(actuator_url, timeout=timeout)
        print("✓ App is healthy", flush=True)

        # Fetch beans
        print("Fetching /actuator/beans...", flush=True)
        beans = fetch_beans_from_url(actuator_url)
        # Count total beans across all contexts
        total_bean_count = 0
        for context_data in beans.get("contexts", {}).values():
            total_bean_count += len(context_data.get("beans", {}))
        print(f"✓ Fetched {total_bean_count} beans from actuator", flush=True)
        yield beans

    except subprocess.CalledProcessError as e:
        raise ActuatorError(f"Docker operation failed: {e}") from e
    finally:
        # Cleanup
        print("Cleaning up Docker resources...", flush=True)
        if use_compose:
            subprocess.run(
                ["docker-compose", "down"],
                cwd=root,
                capture_output=True,
                timeout=30,
            )
        else:
            image_tag = _compute_image_tag(codebase_root)
            for cmd in [
                ["docker", "stop", image_tag],
                ["docker", "rm", image_tag],
                ["docker", "rmi", image_tag],
            ]:
                subprocess.run(cmd, capture_output=True, timeout=30)
        print("✓ Cleanup complete", flush=True)
