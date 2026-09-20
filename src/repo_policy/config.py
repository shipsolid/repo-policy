from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from repo_policy.models import PolicyConfig


class ConfigError(Exception):
    """Raised when policy.yml is missing, malformed, or fails schema validation."""


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader without two of YAML 1.1's legacy scalar-resolution surprises: the yes/no/on/off
    boolean words, and octal/sexagesimal integers.

    PyYAML's default bool resolver treats yes/no/on/off as booleans (confirmed:
    `yaml.safe_load("no")` returns `False`), which would silently turn a branch name or
    status-check context like "no" or "on" into a Python bool before pydantic ever validates it.
    Its default int resolver also treats a leading-zero scalar as octal (confirmed:
    `yaml.safe_load("010")` returns `8`, not `10`), which would silently weaken a leading-zero
    approvals count (e.g. from copy/paste alignment or a %02d-formatted generator) instead of
    matching what was typed -- Python 3's own `int("010")` is decimal `10`, and pydantic coerces a
    numeric-looking string the same way, so leaving `010` as a plain string here and letting
    pydantic's (now strict-mode, see models._PolicyModel) int validation reject it outright rather
    than silently guess which of 8/10 the human meant.

    Removing only the bool resolver's y/Y/n/N/o/O entries and narrowing the int resolver to
    binary/decimal/hex (dropping octal and the obscure base-60 `12:34` form) leaves every other
    SafeLoader behavior, including true/false and 0x/0b-prefixed ints, untouched -- still exactly
    as safe as SafeLoader itself against arbitrary object construction.

    Also overrides construct_mapping(): PyYAML's default is "last key wins" -- a duplicate
    `strict:`, `allow_force_push:`, or branch-name entry silently overwrites the earlier one with
    no warning, which for governance policy is exactly the kind of operator mistake (copy/paste
    duplication, a merge conflict resolved wrong) this parser needs to fail closed on rather than
    guess which value was intended."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        """Reimplements yaml.constructor.BaseConstructor's own construct_mapping body (including
        the SafeConstructor merge-key flattening step it normally does first) with one addition:
        a key already seen in this mapping raises yaml.constructor.ConstructorError -- a
        yaml.YAMLError subclass, so it's caught by load_policy's existing
        `except yaml.YAMLError` alongside every other YAML syntax error, before pydantic ever
        sees the data -- carrying the mark (line/column, 1-indexed for humans) of the duplicate
        key itself."""
        if isinstance(node, yaml.MappingNode):
            self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    "found unhashable key", key_node.start_mark,
                ) from exc
            if duplicate:
                mark = key_node.start_mark
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r} at line {mark.line + 1}, column {mark.column + 1}",
                    mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


_StrictLoader.yaml_implicit_resolvers = {
    first_char: [
        (tag, regexp)
        for tag, regexp in resolvers
        if not (tag == "tag:yaml.org,2002:bool" and first_char in "yYnNoO")
        and tag != "tag:yaml.org,2002:int"
    ]
    for first_char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_StrictLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    re.compile(r"^(?:[-+]?0b[0-1_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+)$"),
    list("-+0123456789"),
)


def load_policy(path: str | Path) -> PolicyConfig:
    file_path = Path(path)

    if not file_path.exists():
        raise ConfigError(f"policy file not found: {file_path}")

    try:
        raw_text = file_path.read_text()
    except OSError as exc:
        raise ConfigError(f"could not read policy file {file_path}: {exc}") from exc

    try:
        data = yaml.load(raw_text, Loader=_StrictLoader)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {file_path}: {exc}") from exc

    if data is None:
        raise ConfigError(f"policy file is empty: {file_path}")

    try:
        return PolicyConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(file_path, exc)) from exc


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"invalid policy config in {path}:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<policy file root>"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)
