"""
System Tools — host workstation management for the steward agent.

File operations, shell execution, Docker management, process monitoring,
package management. All scoped to safe directories with approval tiers.

Tier assignments:
  AUTO    — read_file, list_dir, find_files, grep_code, system_info,
            docker_ps, docker_logs, disk_usage, check_endpoint
  NOTIFY  — write_file, edit_file, append_file, run_shell, docker_restart,
            install_package
  APPROVE — delete_file, move_file, docker_stop, docker_rm, docker_compose_up,
            docker_compose_down, run_shell_destructive, system_service
"""

import asyncio
import fnmatch
import os
from src.env import env
import shutil
from pathlib import Path

from langchain_core.tools import tool

from src.tools.approval import auto, notify, approve

# =============================================================================
# Configuration
# =============================================================================

# Base directories Stuart can operate in — expand as needed
DATA_DIR = Path(env("STUART_DATA_DIR", "/app/data"))
PROJECTS_DIR = DATA_DIR / "projects"
HOME_DIR = Path(env("HOME", "/root"))

# Directories Stuart can READ from (broad access)
VAULT_DIR = Path("/vault")  # synced LP-Vault (mounted read-only from host via Syncthing)
CONFIG_DIR = Path("/app/config")  # agent config: agents.yaml, personas, models.json, user-context.md
SRC_DIR = Path("/app/src")  # merged runtime source — read + quick testing (rebuilt on restart)
OVERLAY_DIR = Path("/overlay/src")  # Stuart-specific code — PERSISTS to host. Save finished code here.
COVE_CORE_DIR = Path("/cove-core/src")  # shared base — read-only
SITES_DIR = Path("/sites")  # website repos (GitHub → Cloudflare Pages) — read-write for content updates
READ_ROOTS = [DATA_DIR, HOME_DIR, Path("/etc"), Path("/var/log"), VAULT_DIR, CONFIG_DIR, SRC_DIR, OVERLAY_DIR, COVE_CORE_DIR, SITES_DIR]

# Directories Stuart can WRITE to (scoped)
WRITE_ROOTS = [PROJECTS_DIR, DATA_DIR / "scratch", HOME_DIR / ".config", VAULT_DIR, SRC_DIR, OVERLAY_DIR, SITES_DIR]

# Never touch these
FORBIDDEN_PATTERNS = [
    ".env", "credentials", "secret", "token", "password",
    ".ssh/id_", "private_key", ".gnupg",
]

# Shell safety
SHELL_SAFE_COMMANDS = {
    "ls", "cat", "head", "tail", "less", "wc", "grep", "rg", "find", "file",
    "stat", "du", "df", "free", "top", "htop", "ps", "uptime", "uname",
    "which", "echo", "date", "whoami", "hostname", "ip", "ss", "ping",
    "dig", "nslookup", "sort", "uniq", "tr", "cut", "awk", "sed", "diff",
    "python", "python3", "node", "npm", "npx", "pip", "pip3",
    "pytest", "git", "docker", "docker-compose", "tailscale",
    "jq", "yq", "curl", "wget",  # read operations
}

SHELL_BLOCKED_PATTERNS = [
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":(){ :|:& };:",
    "> /dev/sd", "chmod -R 777 /", "shutdown", "reboot", "halt",
    "init 0", "init 6",
]


# =============================================================================
# Credential redaction (Companion B) — never echo a secret into a tool result
# =============================================================================
# On 2026-07-11 a run_shell that catted ~/.git-credentials printed the push PAT
# into the chat. Any tool that returns command output OR file contents runs its
# text through redact_credentials() before returning, so a secret that lands in
# stdout/a file is masked in the surface even when the underlying file isn't.
import re as _re

_REDACT_PATTERNS = [
    # GitHub tokens (classic, fine-grained, oauth, app, refresh).
    _re.compile(r"gh[pousr]_[A-Za-z0-9]{20,255}"),
    _re.compile(r"github_pat_[A-Za-z0-9_]{20,255}"),
    # A credential URL: https://user:TOKEN@host  → mask the secret half.
    _re.compile(r"(https?://)([^:@/\s]+):([^@/\s]+)@"),
    # KEY=secret / KEY: secret for anything that looks like a secret var.
    _re.compile(r"(?im)^([ \t]*(?:export[ \t]+)?[A-Z0-9_]*"
                r"(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PAT)[A-Z0-9_]*)"
                r"([ \t]*[:=][ \t]*)(\S+)"),
    # Authorization: Bearer/Basic <blob>.
    _re.compile(r"(?i)(Authorization\s*:\s*(?:Bearer|Basic|token)\s+)(\S+)"),
    # AWS access key ids + generic long hex/secret blobs in *_TOKEN= handled above.
    _re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]

_REDACTED = "«REDACTED»"


def redact_credentials(text: str) -> str:
    """Mask known credential shapes (github_pat_*, gh*_ tokens, *_TOKEN=/SECRET=,
    user:pass@ URLs, Authorization: headers, AWS keys) in returned text. Pure +
    idempotent — safe to run on any tool output. Preserves the key name so the
    output stays readable ('GH_TOKEN=«REDACTED»')."""
    if not text:
        return text
    s = text
    s = _REDACT_PATTERNS[0].sub(_REDACTED, s)
    s = _REDACT_PATTERNS[1].sub(_REDACTED, s)
    s = _REDACT_PATTERNS[2].sub(lambda m: f"{m.group(1)}{m.group(2)}:{_REDACTED}@", s)
    s = _REDACT_PATTERNS[3].sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", s)
    s = _REDACT_PATTERNS[4].sub(lambda m: f"{m.group(1)}{_REDACTED}", s)
    s = _REDACT_PATTERNS[5].sub(_REDACTED, s)
    return s


# =============================================================================
# Path validation
# =============================================================================

def _validate_read_path(path: str) -> Path:
    """Validate a read path is within allowed roots."""
    p = Path(path).resolve()
    for root in READ_ROOTS:
        if str(p).startswith(str(root.resolve())):
            return p
    # Also allow project-relative paths
    project_path = (PROJECTS_DIR / path).resolve()
    if str(project_path).startswith(str(PROJECTS_DIR.resolve())):
        return project_path
    raise ValueError(f"Read path '{path}' is outside allowed directories.")


def _validate_write_path(path: str) -> Path:
    """Validate a write path is within allowed roots and not forbidden."""
    p = Path(path).resolve()

    allowed = False
    for root in WRITE_ROOTS:
        if str(p).startswith(str(root.resolve())):
            allowed = True
            break

    if not allowed:
        # Try as project-relative
        p = (PROJECTS_DIR / path).resolve()
        if str(p).startswith(str(PROJECTS_DIR.resolve())):
            allowed = True

    if not allowed:
        raise ValueError(
            f"Write path '{path}' is outside allowed directories. "
            f"Allowed: {', '.join(str(r) for r in WRITE_ROOTS)}"
        )

    for pattern in FORBIDDEN_PATTERNS:
        if pattern in str(p).lower():
            raise ValueError(f"Path contains forbidden pattern '{pattern}'.")

    return p


# =============================================================================
# File Operations
# =============================================================================

@auto
@tool
async def read_file(path: str, max_chars: int = 15000) -> str:
    """Read a file from the workstation. Accepts absolute or project-relative paths.

    Args:
        path: File path (absolute like /data/projects/foo/bar.py, or relative to projects dir)
        max_chars: Max characters to return (default 15000)
    """
    try:
        resolved = _validate_read_path(path)
        if not resolved.exists():
            return f"File not found: {path}"
        if resolved.is_dir():
            return f"'{path}' is a directory. Use list_dir instead."
        content = resolved.read_text(encoding="utf-8", errors="replace")
        lines = content.count("\n") + 1
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n... [truncated — {lines} lines, {len(content)} chars total]"
        # Companion B: mask credential shapes if this file contains any.
        return redact_credentials(f"FILE: {resolved} ({lines} lines)\n\n{content}")
    except Exception as e:
        return f"Error reading {path}: {e}"


@notify
@tool
async def write_file(path: str, content: str) -> str:
    """Create a NEW file. Use edit_file for existing files.

    Args:
        path: File path (absolute or project-relative, must be in writable dirs)
        content: Full file content
    """
    try:
        resolved = _validate_write_path(path)
        if resolved.exists():
            return (
                f"File already exists: {resolved}. "
                f"Use edit_file to modify existing files, or delete_file first."
            )
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        lines = content.count("\n") + 1
        return f"Created: {resolved} ({lines} lines, {len(content)} chars)"
    except Exception as e:
        return f"Error writing {path}: {e}"


@notify
@tool
async def edit_file(path: str, old_text: str, new_text: str) -> str:
    """Edit an existing file by finding and replacing exact text.

    Args:
        path: File path
        old_text: Exact text to find (must be unique in the file)
        new_text: Replacement text
    """
    try:
        resolved = _validate_write_path(path)
        if not resolved.exists():
            return f"File not found: {path}. Use write_file for new files."

        content = resolved.read_text(encoding="utf-8")
        count = content.count(old_text)

        if count == 0:
            return f"old_text not found in {path}. Check exact whitespace/indentation."
        if count > 1:
            return f"old_text appears {count} times. Provide more context to make match unique."

        new_content = content.replace(old_text, new_text, 1)
        resolved.write_text(new_content, encoding="utf-8")

        added = new_text.count("\n") - old_text.count("\n")
        return f"Edited: {resolved} — {len(old_text)} → {len(new_text)} chars ({'+' if added >= 0 else ''}{added} lines)"
    except Exception as e:
        return f"Error editing {path}: {e}"


@notify
@tool
async def append_file(path: str, content: str) -> str:
    """Append content to an existing file.

    Args:
        path: File path (must exist, must be in writable dirs)
        content: Content to append
    """
    try:
        resolved = _validate_write_path(path)
        if not resolved.exists():
            return f"File not found: {path}. Use write_file for new files."

        existing = resolved.read_text(encoding="utf-8")
        separator = "" if existing.endswith("\n") else "\n"
        resolved.write_text(existing + separator + content, encoding="utf-8")
        return f"Appended to {resolved}: {len(content)} chars"
    except Exception as e:
        return f"Error appending to {path}: {e}"


@approve
@tool
async def delete_file(path: str) -> str:
    """Delete a file. Requires operator approval.

    Args:
        path: File path to delete
    """
    try:
        resolved = _validate_write_path(path)
        if not resolved.exists():
            return f"File not found: {path}"
        resolved.unlink()
        return f"Deleted: {resolved}"
    except Exception as e:
        return f"Error deleting {path}: {e}"


@approve
@tool
async def move_file(src: str, dest: str) -> str:
    """Move or rename a file. Requires operator approval.

    Args:
        src: Source file path
        dest: Destination file path
    """
    try:
        src_resolved = _validate_write_path(src)
        dest_resolved = _validate_write_path(dest)
        if not src_resolved.exists():
            return f"Source not found: {src}"
        dest_resolved.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_resolved), str(dest_resolved))
        return f"Moved: {src_resolved} → {dest_resolved}"
    except Exception as e:
        return f"Error moving {src}: {e}"


@auto
@tool
async def list_dir(path: str = "/app/data", recursive: bool = False, max_items: int = 100) -> str:
    """List files and directories.

    Args:
        path: Directory path (default: /data/projects)
        recursive: If True, list all files recursively
        max_items: Maximum entries to return
    """
    try:
        resolved = _validate_read_path(path)
        if not resolved.exists():
            return f"Directory not found: {path}"
        if not resolved.is_dir():
            return f"'{path}' is a file, not a directory."

        entries = []
        if recursive:
            for root, dirs, files in os.walk(resolved):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__" and d != "node_modules"]
                for f in files:
                    fp = Path(root) / f
                    entries.append(f"  {fp.relative_to(resolved)}  ({fp.stat().st_size} bytes)")
                    if len(entries) >= max_items:
                        break
                if len(entries) >= max_items:
                    break
        else:
            for entry in sorted(resolved.iterdir(), key=lambda p: (p.is_file(), p.name)):
                if entry.name.startswith(".") and not entry.name.startswith(".."):
                    continue
                if entry.is_dir():
                    entries.append(f"  {entry.name}/")
                else:
                    entries.append(f"  {entry.name}  ({entry.stat().st_size} bytes)")
                if len(entries) >= max_items:
                    break

        header = f"DIR: {resolved}/ — {len(entries)} entries"
        if len(entries) >= max_items:
            header += " (limit reached)"
        return header + "\n\n" + "\n".join(entries)
    except Exception as e:
        return f"Error listing {path}: {e}"


# =============================================================================
# Search Operations
# =============================================================================

@auto
@tool
async def grep_code(pattern: str, path: str = "/app/data", file_glob: str = "*.py",
                    max_results: int = 50) -> str:
    """Search file contents by regex pattern.

    Args:
        pattern: Regex pattern to search for
        path: Directory to search in
        file_glob: File pattern filter (e.g. '*.py', '*.html', '*.json')
        max_results: Max matching lines to return
    """
    try:
        resolved = _validate_read_path(path)
        max_results = min(max(max_results, 1), 100)

        cmd = f"grep -rn --include='{file_glob}' -E '{pattern}' '{resolved}' 2>/dev/null | head -{max_results}"
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        output = stdout.decode(errors="replace").strip()

        if not output:
            return f"No matches for '{pattern}' in {path}/"
        lines = output.split("\n")
        result = f"GREP: '{pattern}' in {path}/ — {len(lines)} matches\n\n{output}"
        if len(result) > 12000:
            result = result[:12000] + "\n\n... [truncated]"
        return result
    except asyncio.TimeoutError:
        return "Search timed out. Try a narrower path or pattern."
    except Exception as e:
        return f"Error searching: {e}"


@auto
@tool
async def find_files(pattern: str, path: str = "/app/data") -> str:
    """Find files by name pattern (glob).

    Args:
        pattern: Glob pattern (e.g. '*.py', '*test*', '*.json')
        path: Directory to search in
    """
    try:
        resolved = _validate_read_path(path)
        matches = []
        for root, dirs, files in os.walk(resolved):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__" and d != "node_modules"]
            for f in files:
                if fnmatch.fnmatch(f, pattern):
                    fp = Path(root) / f
                    matches.append(f"  {fp}  ({fp.stat().st_size} bytes)")
                    if len(matches) >= 100:
                        break
            if len(matches) >= 100:
                break

        if not matches:
            return f"No files matching '{pattern}' in {path}/"
        header = f"FIND: '{pattern}' in {path}/ — {len(matches)} files"
        return header + "\n\n" + "\n".join(matches)
    except Exception as e:
        return f"Error finding files: {e}"


# =============================================================================
# Shell Execution
# =============================================================================

def _check_shell_safety(cmd: str) -> str | None:
    """Check if a shell command is safe. Returns error string if blocked."""
    for blocked in SHELL_BLOCKED_PATTERNS:
        if blocked in cmd:
            return f"BLOCKED: Command contains dangerous pattern '{blocked}'."
    return None


@notify
@tool
async def run_shell(command: str, cwd: str = "/app/data", timeout: int = 60) -> str:
    """Execute a shell command on the host.

    Most commands run freely with notification. Destructive system commands
    are blocked — use run_shell_destructive (requires approval) for those.

    Args:
        command: Shell command to run
        cwd: Working directory (default: /data/projects)
        timeout: Max seconds (default 60, max 300)
    """
    safety_err = _check_shell_safety(command)
    if safety_err:
        return safety_err

    timeout = min(timeout, 300)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Command timed out after {timeout}s."

        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        result = out
        if err:
            result += ("\n\nSTDERR:\n" + err) if out else err
        if not result:
            result = "(no output)"
        if len(result) > 15000:
            result = result[:15000] + "\n\n... [truncated]"

        exit_info = f" [exit: {proc.returncode}]" if proc.returncode != 0 else ""
        # Companion B: mask any credential the command echoed (e.g. a cat of
        # ~/.git-credentials) before it reaches the chat surface.
        return redact_credentials(f"SHELL{exit_info}: {command}\n\n{result}")
    except Exception as e:
        return f"Error: {e}"


@approve
@tool
async def run_shell_destructive(command: str, cwd: str = "/app/data",
                                reason: str = "") -> str:
    """Execute a destructive or system-level shell command. Requires operator approval.

    Use for: rm, service restarts, system config changes, package removal, etc.

    Args:
        command: Shell command to run
        cwd: Working directory
        reason: Why this command is needed (shown in approval request)
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        result = out + ("\n" + err if err else "")
        return f"SHELL (approved): {command}\n\n{result or '(no output)'}"
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Docker Management
# =============================================================================

@auto
@tool
async def docker_ps(all_containers: bool = False) -> str:
    """List Docker containers.

    Args:
        all_containers: If True, include stopped containers
    """
    flag = "-a" if all_containers else ""
    return await _run_cmd(f"docker ps {flag} --format 'table {{{{.Names}}}}\\t{{{{.Status}}}}\\t{{{{.Ports}}}}\\t{{{{.Image}}}}'")


@auto
@tool
async def docker_logs(container: str, lines: int = 50) -> str:
    """Get logs from a Docker container.

    Args:
        container: Container name or ID
        lines: Number of lines (default 50)
    """
    return await _run_cmd(f"docker logs --tail {lines} {container}")


@notify
@tool
async def docker_restart(container: str) -> str:
    """Restart a Docker container. Notifies the operator.

    Args:
        container: Container name or ID
    """
    return await _run_cmd(f"docker restart {container}")


@approve
@tool
async def docker_stop(container: str) -> str:
    """Stop a Docker container. Requires approval.

    Args:
        container: Container name or ID
    """
    return await _run_cmd(f"docker stop {container}")


@approve
@tool
async def docker_rm(container: str, force: bool = False) -> str:
    """Remove a Docker container. Requires approval.

    Args:
        container: Container name or ID
        force: Force remove running container
    """
    flag = "-f" if force else ""
    return await _run_cmd(f"docker rm {flag} {container}")


@approve
@tool
async def docker_compose_up(project_dir: str, detach: bool = True, build: bool = False) -> str:
    """Run docker-compose up for a project. Requires approval.

    Args:
        project_dir: Path to directory containing docker-compose.yml
        detach: Run in background (default True)
        build: Rebuild images before starting
    """
    flags = "-d" if detach else ""
    if build:
        flags += " --build"
    return await _run_cmd(f"docker compose up {flags}", cwd=project_dir)


@approve
@tool
async def docker_compose_down(project_dir: str, volumes: bool = False) -> str:
    """Run docker-compose down. Requires approval.

    Args:
        project_dir: Path to directory containing docker-compose.yml
        volumes: Also remove volumes (destructive!)
    """
    flag = "-v" if volumes else ""
    return await _run_cmd(f"docker compose down {flag}", cwd=project_dir)


@auto
@tool
async def docker_images() -> str:
    """List Docker images on the system."""
    return await _run_cmd("docker images --format 'table {{.Repository}}\\t{{.Tag}}\\t{{.Size}}\\t{{.CreatedSince}}'")


# =============================================================================
# System Monitoring
# =============================================================================

@auto
@tool
async def system_info() -> str:
    """Get host system overview: CPU, memory, disk, uptime, GPU."""
    parts = []
    parts.append(await _run_cmd("uname -a"))
    parts.append("--- UPTIME ---")
    parts.append(await _run_cmd("uptime"))
    parts.append("--- MEMORY ---")
    parts.append(await _run_cmd("free -h"))
    parts.append("--- DISK ---")
    parts.append(await _run_cmd("df -h /data /home / 2>/dev/null"))
    parts.append("--- GPU ---")
    parts.append(await _run_cmd("nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu --format=csv,noheader 2>/dev/null || echo 'No NVIDIA GPU or drivers not installed'"))
    parts.append("--- CPU ---")
    parts.append(await _run_cmd("nproc && cat /proc/cpuinfo | grep 'model name' | head -1"))
    return "\n".join(parts)


@auto
@tool
async def disk_usage(path: str = "/data") -> str:
    """Check disk usage for a directory.

    Args:
        path: Directory to check (default: /data)
    """
    return await _run_cmd(f"du -sh {path}/* 2>/dev/null | sort -rh | head -20")


@auto
@tool
async def check_endpoint(url: str = "http://localhost:8100/api/health") -> str:
    """Check if an HTTP endpoint responds.

    Args:
        url: Full URL to check (default: Mission Control health endpoint)
    """
    return await _run_cmd(f"curl -s -o /dev/null -w 'HTTP %{{http_code}} — %{{time_total}}s' '{url}' 2>&1")


@auto
@tool
async def running_services() -> str:
    """List running services and listening ports."""
    parts = []
    parts.append("--- LISTENING PORTS ---")
    parts.append(await _run_cmd("ss -tlnp 2>/dev/null | head -30"))
    parts.append("--- DOCKER CONTAINERS ---")
    parts.append(await _run_cmd("docker ps --format '{{.Names}}: {{.Status}}' 2>/dev/null"))
    parts.append("--- TAILSCALE ---")
    parts.append(await _run_cmd("tailscale status 2>/dev/null || echo 'Tailscale not running'"))
    return "\n".join(parts)


# =============================================================================
# Package Management
# =============================================================================

@notify
@tool
async def install_package(package: str, manager: str = "pip") -> str:
    """Install a package. Notifies the operator.

    Args:
        package: Package name (e.g. 'requests', 'express', 'htop')
        manager: Package manager: pip, npm, apt (default: pip)
    """
    cmds = {
        "pip": f"pip install {package} --break-system-packages",
        "npm": f"npm install -g {package}",
        "apt": f"sudo apt-get install -y {package}",
    }
    if manager not in cmds:
        return f"Unknown package manager: {manager}. Use pip, npm, or apt."
    return await _run_cmd(cmds[manager], timeout=120)


# =============================================================================
# Tailscale
# =============================================================================

@auto
@tool
async def tailscale_status() -> str:
    """Check Tailscale connection status and peers."""
    return await _run_cmd("tailscale status")


# =============================================================================
# Helper
# =============================================================================

async def _run_cmd(cmd: str, cwd: str = None, timeout: int = 30) -> str:
    """Run a command and return output."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"(timed out after {timeout}s)"

        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        if proc.returncode != 0 and err:
            return f"{out}\nERROR: {err}" if out else err
        return out or "(no output)"
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Tool Registry
# =============================================================================

ALL_SYSTEM_TOOLS = [
    # File ops
    read_file, write_file, edit_file, append_file, delete_file, move_file, list_dir,
    # Search
    grep_code, find_files,
    # Shell
    run_shell, run_shell_destructive,
    # Docker
    docker_ps, docker_logs, docker_restart, docker_stop, docker_rm,
    docker_compose_up, docker_compose_down, docker_images,
    # Monitoring
    system_info, disk_usage, check_endpoint, running_services,
    # Packages
    install_package,
    # Network
    tailscale_status,
]
TOOLS = ALL_SYSTEM_TOOLS  # alias for cove-core channels.py loader
