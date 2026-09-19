"""AST-aware code chunking via tree-sitter."""

from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Language, Parser

LANG_MAP: dict[str, Language] = {}
CHUNK_TYPES: dict[str, set[str]] = {}

JS_TYPES = {
    "function_declaration",
    "class_declaration",
    "arrow_function",
    "method_definition",
}


def _register(extensions: list[str], loader, chunk_types: set[str]) -> None:
    """Register a grammar, skipping it if the package isn't installed.

    Grammars are optional: a missing one costs support for that language, not a
    backend that refuses to start.
    """
    try:
        language = Language(loader())
    except Exception:
        return
    for ext in extensions:
        LANG_MAP[ext] = language
        CHUNK_TYPES[ext] = chunk_types


def _load_grammars() -> None:
    try:
        import tree_sitter_python as ts
        _register([".py"], ts.language, {"function_definition", "class_definition"})
    except ImportError:
        pass

    try:
        import tree_sitter_javascript as ts
        _register([".js", ".jsx", ".mjs", ".cjs"], ts.language, JS_TYPES)
    except ImportError:
        pass

    try:
        import tree_sitter_typescript as ts
        _register([".ts"], ts.language_typescript, JS_TYPES)
        _register([".tsx"], ts.language_tsx, JS_TYPES)
    except ImportError:
        pass

    try:
        import tree_sitter_go as ts
        _register(
            [".go"],
            ts.language,
            {"function_declaration", "method_declaration", "type_declaration"},
        )
    except ImportError:
        pass

    try:
        import tree_sitter_rust as ts
        _register(
            [".rs"],
            ts.language,
            {"function_item", "struct_item", "enum_item", "impl_item", "trait_item"},
        )
    except ImportError:
        pass

    try:
        import tree_sitter_java as ts
        _register(
            [".java"],
            ts.language,
            {"method_declaration", "class_declaration", "interface_declaration",
             "constructor_declaration", "enum_declaration"},
        )
    except ImportError:
        pass

    try:
        import tree_sitter_cpp as ts
        _register(
            [".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h", ".c"],
            ts.language,
            {"function_definition", "class_specifier", "struct_specifier"},
        )
    except ImportError:
        pass

    try:
        import tree_sitter_c_sharp as ts
        _register(
            [".cs"],
            ts.language,
            {"method_declaration", "class_declaration", "interface_declaration",
             "struct_declaration"},
        )
    except ImportError:
        pass

    try:
        import tree_sitter_ruby as ts
        _register([".rb"], ts.language, {"method", "class", "module", "singleton_method"})
    except ImportError:
        pass

    try:
        import tree_sitter_php as ts
        _register(
            [".php"],
            ts.language_php,
            {"function_definition", "class_declaration", "method_declaration"},
        )
    except ImportError:
        pass


_load_grammars()

SUPPORTED_EXTENSIONS = set(LANG_MAP.keys())


@dataclass
class CodeChunk:
    text: str
    file_path: str
    line_start: int
    line_end: int
    symbol_name: str
    symbol_type: str  # "function" | "class" | "module"
    language: str


# Each grammar names its identifier nodes differently. Order matters: a Java
# method declaration contains its return type as a type_identifier *before* the
# method name, so plain identifiers must win or `String greet()` is named
# "String".
PRIMARY_NAME_NODES = (
    "identifier",
    "property_identifier",
    "field_identifier",
    "constant",  # Ruby class names
    "name",  # PHP
)

FALLBACK_NAME_NODES = (
    "type_identifier",
    "scoped_identifier",
    "scoped_type_identifier",
)

# Nodes that wrap the real name one level down (C/C++ declarators, Rust impls).
WRAPPER_NODES = {
    "function_declarator",
    "pointer_declarator",
    "reference_declarator",
    "declarator",
    "qualified_identifier",
    "generic_type",
    "type_spec",  # Go: `type Server struct { ... }`
}


def get_symbol_name(node, depth: int = 0) -> str:
    """Extract the name from a function/class node, across grammars."""
    for group in (PRIMARY_NAME_NODES, FALLBACK_NAME_NODES):
        for child in node.children:
            if child.type in group:
                return child.text.decode("utf-8", errors="replace")

    # Descend through wrappers: C++ `int compute(int)` hides the name inside a
    # function_declarator; Go's `type Server struct` inside a type_spec.
    if depth < 3:
        for child in node.children:
            if child.type in WRAPPER_NODES:
                found = get_symbol_name(child, depth + 1)
                if found != "<anonymous>":
                    return found

    return "<anonymous>"


# Type-like declarations across grammars, so a Rust struct isn't called a function.
TYPE_LIKE = ("class", "struct", "enum", "interface", "trait", "impl", "type_declaration")


def get_symbol_type(node_type: str) -> str:
    if node_type in ("module", "namespace_definition"):
        return "module"
    if any(marker in node_type for marker in TYPE_LIKE):
        return "class"
    return "function"


def embedding_text(chunk: "CodeChunk", workspace: str | None = None) -> str:
    """The text actually embedded for a chunk.

    Queries are English ("where are api keys stored"); raw source is not. Naming
    the symbol and file in front of the code gives the embedding something in
    plain words to match against. Measured on the repo's own eval set, this took
    top-1 accuracy from 71% to 86% with no model change.
    """
    location = chunk.file_path
    if workspace:
        try:
            location = str(Path(chunk.file_path).relative_to(workspace))
        except ValueError:
            location = Path(chunk.file_path).name

    return f"{chunk.symbol_type} {chunk.symbol_name} in {location}\n{chunk.text}"


def chunk_file(file_path: str) -> list[CodeChunk]:
    """Parse a file and extract function/class chunks."""
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext not in LANG_MAP:
        return []

    try:
        source = path.read_bytes()
    except (OSError, UnicodeDecodeError):
        return []

    parser = Parser(LANG_MAP[ext])
    tree = parser.parse(source)
    source_text = source.decode("utf-8", errors="replace")
    lines = source_text.splitlines()
    chunk_types = CHUNK_TYPES.get(ext, set())
    chunks: list[CodeChunk] = []

    # Walk AST, extract top-level and nested definitions
    def walk(node, parent_name: str = ""):
        if node.type in chunk_types:
            name = get_symbol_name(node)
            full_name = f"{parent_name}.{name}" if parent_name else name
            start = node.start_point[0]
            end = node.end_point[0]
            text = "\n".join(lines[start : end + 1])

            chunks.append(
                CodeChunk(
                    text=text,
                    file_path=file_path,
                    line_start=start + 1,  # 1-indexed
                    line_end=end + 1,
                    symbol_name=full_name,
                    symbol_type=get_symbol_type(node.type),
                    language=ext.lstrip("."),
                )
            )
            # Recurse for nested defs (methods inside classes)
            for child in node.children:
                walk(child, parent_name=full_name)
        else:
            for child in node.children:
                walk(child, parent_name=parent_name)

    walk(tree.root_node)

    # If no chunks found (no functions/classes), treat whole file as one chunk
    # ponytail: whole-file fallback, fine for small config/script files
    if not chunks and source_text.strip():
        chunks.append(
            CodeChunk(
                text=source_text[:3000],  # cap at 3k chars
                file_path=file_path,
                line_start=1,
                line_end=len(lines),
                symbol_name=path.stem,
                symbol_type="module",
                language=ext.lstrip("."),
            )
        )

    return chunks


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m app.services.chunker <file>")
        sys.exit(1)
    for c in chunk_file(sys.argv[1]):
        print(f"{c.symbol_type} {c.symbol_name} @ {c.file_path}:{c.line_start}-{c.line_end}")
        print(c.text[:200])
        print("---")
