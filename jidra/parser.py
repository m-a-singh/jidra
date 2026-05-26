from __future__ import annotations

from tree_sitter import Language, Parser
import tree_sitter_java as tsjava

JAVA_LANGUAGE = Language(tsjava.language())


def make_parser() -> Parser:
    return Parser(JAVA_LANGUAGE)
