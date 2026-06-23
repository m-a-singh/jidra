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
