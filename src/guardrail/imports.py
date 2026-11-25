"""Check imports in added lines using source and package metadata only."""

import ast
import sys
from collections.abc import Iterator
from functools import cached_property
from importlib.metadata import packages_distributions

from guardrail.source import ReviewSource


def _static_exports(tree: ast.Module) -> set[str] | None:
    """Only conclude a name is absent when the module has simple bindings."""
    names = set()
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(stmt.name)
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            for alias in stmt.names:
                if alias.name == "*":
                    return None
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            if any(not isinstance(t, ast.Name) for t in targets):
                return None
            if stmt.value is not None:
                if any(
                    isinstance(n, (ast.Call, ast.NamedExpr))
                    for n in ast.walk(stmt.value)
                ):
                    return None
                names.update(t.id for t in targets)
        elif isinstance(stmt, ast.TypeAlias):
            names.add(stmt.name.id)
        elif isinstance(stmt, ast.Pass) or (
            isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
        ):
            continue
        else:
            return None
    return None if "__getattr__" in names else names


class _Modules:
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.paths: dict[str, str] = {}
        self.packages: set[str] = set()
        self.package_for_file: dict[str, list[str]] = {}
        self.exports: dict[str, set[str] | None] = {}
        for path in sorted(files):
            name = path if "src/__init__.py" in files else path.removeprefix("src/")
            parts = name.removesuffix(".py").split("/")
            package = parts[:-1]
            self.package_for_file[path] = package
            if parts[-1] == "__init__":
                parts = package
                self.packages.add(".".join(parts))
            module = ".".join(parts)
            self.paths[module] = path
            self.packages.update(".".join(parts[:i]) for i in range(1, len(parts)))

    @cached_property
    def installed(self) -> set[str]:
        return set(packages_distributions())

    def unresolved(self, module: str) -> bool:
        if module in self.paths or module in self.packages:
            return False
        root = module.partition(".")[0]
        if root in self.paths:
            return True
        return root not in sys.stdlib_module_names and root not in self.installed

    def missing_name(self, module: str, name: str) -> bool:
        if name == "*" or (name.startswith("__") and name.endswith("__")):
            return False
        child = f"{module}.{name}"
        if module not in self.paths:
            return module in self.packages and self.unresolved(child)
        if module in self.packages and (child in self.paths or child in self.packages):
            return False
        if module not in self.exports:
            path = self.paths[module]
            try:
                tree = ast.parse(self.files[path], filename=path)
            except SyntaxError:
                self.exports[module] = None
            else:
                self.exports[module] = _static_exports(tree)
        names = self.exports[module]
        return names is not None and name not in names


def _from_module(node: ast.ImportFrom, package: list[str]) -> str | None:
    if not node.level:
        return node.module
    if node.level > len(package):
        return None
    base = package[: len(package) - node.level + 1]
    return ".".join([*base, node.module] if node.module else base)


def _issues(
    tree: ast.Module, path: str, added: set[int], modules: _Modules
) -> Iterator[tuple[int, str]]:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        # A changed 'from' header affects every alias. On continuation lines
        # only the individual added aliases are in scope.
        header = None
        if isinstance(node, ast.ImportFrom):
            header_end = max(node.lineno + 1, node.names[0].lineno)
            header = next(
                (i for i in range(node.lineno, header_end) if i in added), None
            )
        for alias in node.names:
            line = next(
                (i for i in range(alias.lineno, alias.end_lineno + 1) if i in added),
                header,
            )
            if line is None:
                continue
            if isinstance(node, ast.Import):
                if modules.unresolved(alias.name):
                    yield line, f"Cannot resolve module '{alias.name}'."
                continue
            module = _from_module(node, modules.package_for_file[path])
            if module is None:
                yield line, "Relative import goes beyond the top-level package."
                break
            if modules.unresolved(module):
                yield line, f"Cannot resolve module '{module}'."
                break
            if modules.missing_name(module, alias.name):
                yield line, f"Local module '{module}' has no name '{alias.name}'."


class ImportCheck:
    name = "imports"
    requires_whole_file = True

    def run(self, source: ReviewSource) -> Iterator[dict[str, object]]:
        modules = _Modules(source.python_files)
        for path, added in source.changed_python_lines():
            tree = ast.parse(source.python_files[path], filename=path)
            for line, message in _issues(tree, path, added, modules):
                yield {
                    "check": self.name,
                    "file": path,
                    "line": line,
                    "severity": "warning",
                    "message": message,
                }
