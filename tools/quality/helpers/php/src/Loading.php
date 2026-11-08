<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use PhpParser\Node;
use PhpParser\Node\Stmt;

use function array_filter;
use function count;

/**
 * What loading a file does, for the coverage run of decision 0013.
 *
 * A file that only declares classes, interfaces, traits, enums, functions,
 * and constants runs no statement when it is included, so the unit run loads
 * it before the tests. Its units are the functions and methods with a body
 * that PHPUnit's coverage measures branches of: named functions and the
 * methods of named classes, traits, and enums.
 */
final class Loading
{
    /** @param array<Node> $statements */
    public static function declaresOnly(array $statements): bool
    {
        foreach ($statements as $statement) {
            if (!self::declaration($statement)) {
                return false;
            }
        }
        return true;
    }

    /** @param array<Node> $nodes */
    public static function units(array $nodes): int
    {
        $total = 0;
        foreach ($nodes as $node) {
            $total += self::own($node) + self::units(Tree::children($node));
        }
        return $total;
    }

    private static function declaration(Node $node): bool
    {
        return match (true) {
            $node instanceof Stmt\Namespace_ => self::declaresOnly($node->stmts),
            $node instanceof Stmt\Declare_ => $node->stmts === null,
            default => $node instanceof Stmt\ClassLike
                || $node instanceof Stmt\Function_
                || $node instanceof Stmt\Use_
                || $node instanceof Stmt\GroupUse
                || $node instanceof Stmt\Const_
                || $node instanceof Stmt\Nop,
        };
    }

    private static function own(Node $node): int
    {
        if ($node instanceof Stmt\Function_) {
            return 1;
        }
        if (!$node instanceof Stmt\ClassLike || $node->name === null) {
            return 0;
        }
        $bodies = array_filter($node->getMethods(), static fn(Stmt\ClassMethod $method): bool => $method->stmts !== null);
        return count($bodies);
    }
}
