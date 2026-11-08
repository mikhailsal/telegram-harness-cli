<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use PhpParser\Node;
use PhpParser\Node\Expr;
use PhpParser\Node\Expr\AssignOp;
use PhpParser\Node\Expr\BinaryOp;
use PhpParser\Node\FunctionLike;
use PhpParser\Node\MatchArm;
use PhpParser\Node\Stmt;

use function in_array;

/**
 * Cyclomatic complexity with the counting rules of decision 0007.
 *
 * One plus each `if`, `elseif`, conditional expression, loop, `catch`, `&&`,
 * `||`, `and`, `or`, `??`, and `??=`, and each `case` and `match` arm except
 * `default`. Nested functions count on their own.
 */
final class Complexity
{
    private const array DECISIONS = [
        Stmt\If_::class,
        Stmt\ElseIf_::class,
        Expr\Ternary::class,
        Stmt\For_::class,
        Stmt\Foreach_::class,
        Stmt\While_::class,
        Stmt\Do_::class,
        Stmt\Catch_::class,
        BinaryOp\BooleanAnd::class,
        BinaryOp\BooleanOr::class,
        BinaryOp\LogicalAnd::class,
        BinaryOp\LogicalOr::class,
        BinaryOp\Coalesce::class,
        AssignOp\Coalesce::class,
    ];

    /** @param list<Node> $nodes */
    public static function of(array $nodes): int
    {
        return 1 + self::sum($nodes);
    }

    /** @param list<Node> $nodes */
    private static function sum(array $nodes): int
    {
        $total = 0;
        foreach ($nodes as $node) {
            $total += self::decisions($node);
        }
        return $total;
    }

    private static function decisions(Node $node): int
    {
        if ($node instanceof FunctionLike) {
            return 0;
        }
        return self::own($node) + self::sum(Tree::children($node));
    }

    /** The decisions a node adds on its own. */
    private static function own(Node $node): int
    {
        return match (true) {
            $node instanceof Stmt\Case_ => $node->cond === null ? 0 : 1,
            $node instanceof MatchArm => $node->conds === null ? 0 : 1,
            default => in_array($node::class, self::DECISIONS, true) ? 1 : 0,
        };
    }
}
