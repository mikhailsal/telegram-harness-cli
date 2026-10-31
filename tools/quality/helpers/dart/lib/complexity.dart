/// Cyclomatic complexity with the counting rules of decision 0007.
library;

import 'package:analyzer/dart/ast/ast.dart';
import 'package:analyzer/dart/ast/token.dart';
import 'package:analyzer/dart/ast/visitor.dart';

import 'tree.dart';

const _logical = {
  TokenType.AMPERSAND_AMPERSAND,
  TokenType.BAR_BAR,
  TokenType.QUESTION_QUESTION,
};

/// One plus the decisions in [nodes], not counting nested functions.
int complexity(List<AstNode> nodes) =>
    1 + nodes.fold(0, (total, node) => total + _decisions(node));

int _decisions(AstNode node) {
  if (shapeOf(node) != null) {
    return 0;
  }
  return children(node)
      .fold(_own(node), (total, child) => total + _decisions(child));
}

int _own(AstNode node) => node.accept(const _Decision()) ?? 0;

/// The decisions a node adds on its own; null for a node that adds none.
final class _Decision extends SimpleAstVisitor<int> {
  const _Decision();

  @override
  int visitIfStatement(IfStatement node) => 1;

  @override
  int visitIfElement(IfElement node) => 1;

  @override
  int visitConditionalExpression(ConditionalExpression node) => 1;

  @override
  int visitForStatement(ForStatement node) => 1;

  @override
  int visitForElement(ForElement node) => 1;

  @override
  int visitWhileStatement(WhileStatement node) => 1;

  @override
  int visitDoStatement(DoStatement node) => 1;

  @override
  int visitCatchClause(CatchClause node) => 1;

  @override
  int visitLogicalAndPattern(LogicalAndPattern node) => 1;

  @override
  int visitLogicalOrPattern(LogicalOrPattern node) => 1;

  @override
  int visitSwitchCase(SwitchCase node) => 1;

  // A pattern case is always a member of a switch statement and an arm always
  // a case of a switch expression.
  @override
  int visitSwitchPatternCase(SwitchPatternCase node) => _arm(
    node.guardedPattern,
    last: identical((node.parent as SwitchStatement).members.last, node),
  );

  @override
  int visitSwitchExpressionCase(SwitchExpressionCase node) => _arm(
    node.guardedPattern,
    last: identical((node.parent as SwitchExpression).cases.last, node),
  );

  @override
  int visitBinaryExpression(BinaryExpression node) =>
      _logical.contains(node.operator.type) ? 1 : 0;

  @override
  int visitAssignmentExpression(AssignmentExpression node) =>
      node.operator.type == TokenType.QUESTION_QUESTION_EQ ? 1 : 0;
}

/// A case adds one unless it is the irrefutable final case, like `default`.
int _arm(GuardedPattern guarded, {required bool last}) =>
    last && guarded.whenClause == null && _irrefutable(guarded.pattern) ? 0 : 1;

bool _irrefutable(DartPattern pattern) => switch (pattern) {
  WildcardPattern(type: null) => true,
  DeclaredVariablePattern(type: null) => true,
  _ => false,
};
