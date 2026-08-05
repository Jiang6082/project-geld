"""A tiny, safe recursive-descent parser for candidate factor expressions.

Faithful port of ``emberforge.dsl.parser``. Grammar (whitespace-insensitive)::

    expr   := term  (('+' | '-') term)*
    term   := unary (('*' | '/') unary)*
    unary  := '-' unary | atom
    atom   := number | field | ident '(' [expr (',' expr)*] ')' | '(' expr ')'

Function names map to operators in :mod:`operators`. Binary operators desugar to
calls (``a + b`` -> ``Call('add', (a, b))``). This never uses ``eval``/``exec``;
the only thing that can come out is a :mod:`nodes` tree.
"""

from __future__ import annotations

import re

from .nodes import RAW_FIELDS, Call, Const, Field, Node

_TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+\.?\d*(?:[eE][+-]?\d+)?)|(?P<ident>[A-Za-z_][A-Za-z0-9_]*)|(?P<op>[()+\-*/,]))"
)

_BINOP = {"+": "add", "-": "subtract", "*": "multiply", "/": "divide"}


class ParseError(ValueError):
    pass


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ParseError(f"unexpected character at position {pos}: {text[pos:pos+10]!r}")
        pos = m.end()
        tokens.append(m.group().strip())
    return tokens


class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.i = 0

    def peek(self) -> str | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def next(self) -> str:
        tok = self.peek()
        if tok is None:
            raise ParseError("unexpected end of expression")
        self.i += 1
        return tok

    def expect(self, tok: str) -> None:
        got = self.next()
        if got != tok:
            raise ParseError(f"expected {tok!r}, got {got!r}")

    def parse(self) -> Node:
        node = self.expr()
        if self.peek() is not None:
            raise ParseError(f"trailing tokens: {self.tokens[self.i:]}")
        return node

    def expr(self) -> Node:
        node = self.term()
        while self.peek() in ("+", "-"):
            op = _BINOP[self.next()]
            node = Call(op, (node, self.term()))
        return node

    def term(self) -> Node:
        node = self.unary()
        while self.peek() in ("*", "/"):
            op = _BINOP[self.next()]
            node = Call(op, (node, self.unary()))
        return node

    def unary(self) -> Node:
        if self.peek() == "-":
            self.next()
            return Call("neg", (self.unary(),))
        return self.atom()

    def atom(self) -> Node:
        tok = self.next()
        if tok == "(":
            node = self.expr()
            self.expect(")")
            return node
        if re.fullmatch(r"\d+\.?\d*(?:[eE][+-]?\d+)?", tok):
            return Const(float(tok))
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tok):
            if self.peek() == "(":  # function call
                self.next()
                args: list[Node] = []
                if self.peek() != ")":
                    args.append(self.expr())
                    while self.peek() == ",":
                        self.next()
                        args.append(self.expr())
                self.expect(")")
                return Call(tok, tuple(args))
            if tok in RAW_FIELDS:
                return Field(tok)
            raise ParseError(f"bare identifier {tok!r} is not a raw field")
        raise ParseError(f"unexpected token {tok!r}")


def parse(text: str) -> Node:
    """Parse ``text`` into an expression tree. Raises :class:`ParseError`."""
    tokens = _tokenize(text)
    if not tokens:
        raise ParseError("empty expression")
    return _Parser(tokens).parse()
