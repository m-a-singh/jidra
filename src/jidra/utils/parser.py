from __future__ import annotations

from tree_sitter import Language, Parser
import tree_sitter_java as tsjava
import tree_sitter_go as tsgo

JAVA_LANGUAGE = Language(tsjava.language())
GO_LANGUAGE = Language(tsgo.language())


def make_parser() -> Parser:
    return Parser(JAVA_LANGUAGE)


def make_go_parser() -> Parser:
    return Parser(GO_LANGUAGE)


def make_ts_parser(tsx: bool = False) -> Parser:
    """In-process TypeScript/TSX parser (Phase 7). Imported lazily so projects
    without the optional `tree-sitter-typescript` dependency still load; raises
    ImportError if it's missing so callers can fall back to the Docker sidecar."""
    import tree_sitter_typescript as tsts

    lang = tsts.language_tsx() if tsx else tsts.language_typescript()
    return Parser(Language(lang))
