"""Path-boundary and log-redaction helpers (GOAL.md §27).

This module carries two of the §27 prohibitions in code:

* ``resolve_within`` keeps an envelope path below its configured root.
  §27 requires that path traversal is rejected, absolute paths outside
  the root are rejected, and **symlink escapes are rejected** — a link
  inside the root whose target is outside it. A naive string
  comparison passes the traversal case and fails this one, because the
  link's path string stays inside the root while the filesystem
  disagrees. The function resolves the real path before comparing.

* ``redact`` masks credential-shaped values in a log line. §27 says
  logs must redact tokens and credentials. This is a **log helper,
  not a security control** — a determined attacker can construct a
  value whose name does not match the heuristic. Use it to keep
  accidental leaks out of the log stream, not to defend a secret
  that is already in a bad place.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


_SECRET_NAMES = (
    "key",
    "token",
    "secret",
    "password",
    "credential",
    "credentials",
)


_SECRET_NAME_ALT = "|".join(_SECRET_NAMES)


_REDACT_PATTERN = re.compile(
    r"""
    \b
    (?P<name>
        (?:[A-Za-z0-9_]+_)?
        (?:
            key
            | token
            | secret
            | password
            | credential
            | credentials
        )
    )
    \b
    \s* = \s*
    (?P<quote> " ? )
    (?P<value> [^\s"']+ )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def resolve_within(root, candidate):
    """Resolve ``candidate`` and assert it stays under ``root``.

    Both arguments may be ``str`` or :class:`pathlib.Path`. The real
    path of ``candidate`` is computed (symlinks followed) and compared
    against the real path of ``root``. Returns the resolved
    :class:`pathlib.Path` on success.

    Raises :class:`ValueError` when the candidate resolves to a path
    outside ``root`` — including traversal (``../../etc/passwd``),
    absolute paths outside the root (``/etc/passwd``), and symlink
    escapes (a link inside the root whose target is outside it).

    The candidate does not need to exist. An expected deliverable is
    often a path that has not been written yet; what we are
    preventing is the path *string* pointing somewhere it must not.
    """
    resolved_root = Path(os.path.realpath(str(root))).resolve()
    resolved_candidate = Path(os.path.realpath(str(candidate))).resolve()
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"path {candidate!r} resolves outside the allowed root "
            f"{root!r}: {resolved_candidate!r}"
        ) from exc
    return resolved_candidate


def _mask(match: "re.Match[str]") -> str:
    return f"{match.group('name')}=***"


def redact(text: str) -> str:
    """Mask credential-shaped values in a log line.

    Replaces ``NAME=value`` (and ``NAME="value"``) pairs whose name
    matches the secret heuristic — at least ``key``, ``token``,
    ``secret``, ``password``, ``credential`` and ``credentials``,
    matched case-insensitively, both as a bare name (``token=...``)
    and as a suffix of a longer identifier
    (``ANTHROPIC_API_KEY=...``) — with ``NAME=***``. Other text is
    returned unchanged.

    This is a log helper, not a security control. The name heuristic
    is best-effort: a determined attacker can construct a value whose
    name does not match (and there is no public naming convention
    that covers every provider). Use it to keep obvious secrets out
    of the log stream, not to defend a secret that is already in a
    bad place.
    """
    return _REDACT_PATTERN.sub(_mask, text)
