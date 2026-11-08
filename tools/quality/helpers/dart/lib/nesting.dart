/// Nesting depth of statement blocks with the counting rules of decision 0007.
library;

import 'package:analyzer/dart/ast/ast.dart';

import 'tree.dart';

/// The deepest chain of nesting statements in [nodes], not counting nested
/// functions, which start at zero on their own.
int nesting(List<AstNode> nodes) =>
    nodes.fold(0, (deepest, node) => _max(deepest, _depth(node)));

int _depth(AstNode node) {
  if (shapeOf(node) != null) {
    return 0;
  }
  final inner = children(node).fold(0, (deepest, child) {
    return _max(deepest, _depth(child));
  });
  return _opens(node) ? inner + 1 : inner;
}

/// Whether a statement opens a level; an `else if` stays on its chain's level.
bool _opens(AstNode node) => switch (node) {
  IfStatement(:final parent) => !_isElseBranch(node, parent),
  ForStatement() ||
  WhileStatement() ||
  DoStatement() ||
  SwitchStatement() ||
  TryStatement() => true,
  _ => false,
};

bool _isElseBranch(IfStatement node, AstNode? parent) =>
    parent is IfStatement && identical(parent.elseStatement, node);

int _max(int a, int b) => a > b ? a : b;
