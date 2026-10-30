"""Throwaway PostgreSQL cluster that runs SQL through psql.

Context: Migration tests run against a real PostgreSQL started from local binaries; nothing
reaches the Supabase project. | Input: A directory with initdb, pg_ctl and psql. | Output:
PostgresCluster and Database handles.
"""

import json
import os
import re
import shutil
import socket
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from uuid import UUID

BIN_DIR_VARIABLE = "RADAR_PG_BIN"
_DEBIAN_ROOT = Path("/usr/lib/postgresql")
_ERROR = re.compile(r"ERROR:\s+(.+)")
_SUPERUSER = "postgres"
_TIMEOUT_SECONDS = 120


class DatabaseError(Exception):
    """A statement failed; the message is PostgreSQL's error text without psql's prefix."""


def _has_tools(directory: Path) -> bool:
    return all(shutil.which(tool, path=str(directory)) for tool in ("initdb", "pg_ctl", "psql"))


def _pgserver_bin_dir() -> Path | None:
    spec = find_spec("pgserver")
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).parent / "pginstall" / "bin"


def _debian_bin_dirs() -> list[Path]:
    if not _DEBIAN_ROOT.is_dir():
        return []
    versions = [path for path in _DEBIAN_ROOT.iterdir() if path.name.isdigit()]
    return [
        path / "bin" for path in sorted(versions, key=lambda path: int(path.name), reverse=True)
    ]


def find_bin_dir() -> Path | None:
    """Locate PostgreSQL binaries.

    Looks at the directory in RADAR_PG_BIN, then PATH, the Debian layout used by GitHub runners and
    finally the pgserver package.

    Returns:
        Directory holding initdb, pg_ctl and psql, or None when there is none.
    """
    configured = os.environ.get(BIN_DIR_VARIABLE)
    on_path = shutil.which("pg_ctl")
    candidates = [
        Path(configured) if configured else None,
        Path(on_path).parent if on_path else None,
        *_debian_bin_dirs(),
        _pgserver_bin_dir(),
    ]
    return next((path for path in candidates if path is not None and _has_tools(path)), None)


def runs_as_root() -> bool:
    """Tell whether this process is root, which PostgreSQL refuses to run as."""
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@dataclass(frozen=True)
class Caller:
    """Who runs a statement: a database role and the signed-in user Supabase would put in the token.

    Attributes:
        role: Role to switch to, e.g. "authenticated".
        user_id: Subject of the token; None for an anonymous caller.
    """

    role: str
    user_id: UUID | None = None

    def variables(self) -> dict[str, str]:
        """Psql variables that carry the role and the token claims."""
        claims: dict[str, str] = {"role": self.role}
        if self.user_id is not None:
            claims["sub"] = str(self.user_id)
        return {"caller_role": self.role, "caller_claims": json.dumps(claims)}


_SWITCH_TO_CALLER = "set request.jwt.claims = :'caller_claims';\nset role :\"caller_role\";\n"


class PostgresCluster:
    """A PostgreSQL cluster in a temporary directory, listening on a free local port."""

    def __init__(self, bin_dir: Path, root: Path) -> None:
        """Prepare a cluster; nothing starts before start() or entering the context.

        Args:
            bin_dir: Directory holding initdb, pg_ctl and psql.
            root: Empty directory for data and log.
        """
        self._bin_dir = bin_dir
        self._data_dir = root / "data"
        self._log = root / "postgres.log"
        self.port = _free_port()

    def tool(self, name: str) -> str:
        """Full path of a PostgreSQL program.

        Raises:
            FileNotFoundError: If the program is not in the binary directory.
        """
        path = shutil.which(name, path=str(self._bin_dir))
        if path is None:
            raise FileNotFoundError(f"{name} not found in {self._bin_dir}")
        return path

    def _call(self, arguments: list[str], stdin: str | None = None) -> str:
        completed = subprocess.run(
            arguments,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0:
            found = _ERROR.search(completed.stderr)
            raise DatabaseError(found.group(1).strip() if found else completed.stderr.strip())
        return completed.stdout

    def start(self) -> None:
        """Create the data directory and start the server."""
        self._call(
            [
                self.tool("initdb"),
                "-D",
                str(self._data_dir),
                "-U",
                _SUPERUSER,
                "-A",
                "trust",
                "-E",
                "UTF8",
                "--no-locale",
                "--no-sync",
            ]
        )
        options = (
            f"-p {self.port} -c listen_addresses=127.0.0.1 "
            "-c unix_socket_directories='' -c fsync=off"
        )
        self._call(
            [self.tool("pg_ctl"), "-D", str(self._data_dir), "-l", str(self._log), "-o", options]
            + ["-w", "start"]
        )

    def stop(self) -> None:
        """Stop the server without waiting for clients."""
        self._call([self.tool("pg_ctl"), "-D", str(self._data_dir), "-m", "immediate", "stop"])

    def __enter__(self) -> Self:
        """Start the cluster."""
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the cluster."""
        self.stop()

    def psql(
        self,
        database: str,
        script: str,
        variables: Mapping[str, str],
        *,
        single_transaction: bool = True,
    ) -> str:
        """Run a script and answer its unaligned output.

        Args:
            database: Database to connect to.
            script: Statements, read by psql from standard input.
            variables: Psql variables for the statements.
            single_transaction: Wrap the script in one transaction, rolled back on failure.

        Raises:
            DatabaseError: If a statement fails.
        """
        arguments = [self.tool("psql"), "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1"]
        if single_transaction:
            arguments.append("-1")
        arguments += ["-h", "127.0.0.1", "-p", str(self.port), "-U", _SUPERUSER, "-d", database]
        for name, value in variables.items():
            arguments += ["-v", f"{name}={value}"]
        return self._call([*arguments, "-f", "-"], stdin=script)

    def create_database(self, name: str, template: str | None = None) -> "Database":
        """Create a database, as a copy of a template when one is given."""
        source = f' template "{template}"' if template else ""
        self.psql("postgres", f'create database "{name}"{source};', {}, single_transaction=False)
        return Database(self, name)

    def drop_database(self, name: str) -> None:
        """Drop a database and everything in it."""
        self.psql(
            "postgres",
            f'drop database if exists "{name}" with (force);',
            {},
            single_transaction=False,
        )


@dataclass(frozen=True)
class Database:
    """One database of a running cluster.

    Attributes:
        cluster: Cluster that holds the database.
        name: Database name.
    """

    cluster: PostgresCluster
    name: str

    def run(self, sql: str, caller: Caller | None = None, **variables: str) -> str:
        """Run SQL in one transaction, as the superuser or as the given caller.

        Args:
            sql: Statements; values come in as psql variables such as :'website'.
            caller: Role and signed-in user to switch to first; None runs as superuser.
            **variables: Psql variables for the statements.

        Returns:
            Unaligned output of the statements.

        Raises:
            DatabaseError: If a statement fails.
        """
        if caller is None:
            return self.cluster.psql(self.name, sql, variables)
        return self.cluster.psql(
            self.name, _SWITCH_TO_CALLER + sql, {**variables, **caller.variables()}
        )

    def run_file(self, path: Path) -> None:
        """Run a migration file as the superuser in one transaction, like a migration tool does."""
        self.run(path.read_text(encoding="utf-8"))

    def rows(self, query: str, caller: Caller | None = None, **variables: str) -> list[Any]:
        """Answer the rows of a query as JSON objects.

        Args:
            query: A single select without trailing semicolon.
            caller: Role and signed-in user to switch to first; None runs as superuser.
            **variables: Psql variables for the query.
        """
        wrapped = f"select coalesce(json_agg(row_to_json(q)), '[]') from (\n{query}\n) q;"
        result: list[Any] = json.loads(self.run(wrapped, caller, **variables))
        return result

    def value(self, query: str, caller: Caller | None = None, **variables: str) -> Any:
        """Answer the single value of a query that returns one row with one column."""
        rows = self.rows(query, caller, **variables)
        if len(rows) != 1 or len(rows[0]) != 1:
            raise DatabaseError(f"Expected one value, got {rows}")
        return next(iter(rows[0].values()))


def pg_array(items: Iterable[str | None]) -> str:
    """Write texts as a PostgreSQL array literal; None becomes a null element."""

    def element(item: str | None) -> str:
        if item is None:
            return "NULL"
        return '"' + item.replace("\\", "\\\\").replace('"', '\\"') + '"'

    return "{" + ",".join(element(item) for item in items) + "}"
