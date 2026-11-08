/// Code lines of a function body: physical lines that hold one of its tokens.
///
/// Comments are not tokens, so comment-only and blank lines never count, and a
/// token that spans lines, such as part of a multi-line string, counts every
/// line it spans.
library;

import 'package:analyzer/dart/ast/ast.dart';
import 'package:analyzer/dart/ast/token.dart';
import 'package:analyzer/source/line_info.dart';

import 'tree.dart';

/// The number of distinct lines that hold a token of the function's body.
int codeLines(Shape shape, LineInfo lines) {
  final found = <int>{};
  final initializers = shape.initializers;
  if (initializers.isNotEmpty) {
    _span(
      initializers.first.beginToken,
      initializers.last.endToken,
      lines,
      found,
    );
  }
  switch (shape.body) {
    case BlockFunctionBody(:final block):
      _between(block.leftBracket, block.rightBracket, lines, found);
    case ExpressionFunctionBody(:final expression):
      _span(expression.beginToken, expression.endToken, lines, found);
    default:
      break;
  }
  return found.length;
}

/// Add the lines of the tokens strictly between [open] and [close].
void _between(Token open, Token close, LineInfo lines, Set<int> found) {
  for (var token = open.next!; !identical(token, close); token = token.next!) {
    _add(token, lines, found);
  }
}

/// Add the lines of the tokens from [first] to [last], both included.
void _span(Token first, Token last, LineInfo lines, Set<int> found) {
  _between(first.previous!, last.next!, lines, found);
}

void _add(Token token, LineInfo lines, Set<int> found) {
  final start = lines.getLocation(token.offset).lineNumber;
  final end = lines.getLocation(token.end - 1).lineNumber;
  for (var line = start; line <= end; line++) {
    found.add(line);
  }
}
