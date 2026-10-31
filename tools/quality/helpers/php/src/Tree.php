<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use PhpParser\Node;
use PhpParser\Node\Expr;
use PhpParser\Node\FunctionLike;
use PhpParser\Node\PropertyHook;
use PhpParser\Node\Stmt;

use function array_values;
use function is_array;

/**
 * The syntax tree walk shared by the metrics: children and function bodies.
 *
 * Functions are named functions, methods, closures, arrow functions, and
 * property hooks. Each one is measured on its own.
 */
final class Tree
{
    /**
     * The child nodes of `$node`, in source order.
     *
     * @return list<Node>
     */
    public static function children(Node $node): array
    {
        $found = [];
        foreach ($node->getSubNodeNames() as $name) {
            $value = $node->$name;
            foreach (is_array($value) ? $value : [$value] as $item) {
                if ($item instanceof Node) {
                    $found[] = $item;
                }
            }
        }
        return $found;
    }

    /**
     * The nodes of a function's body, or null for a function without one.
     *
     * Abstract and interface methods have no body; an arrow function and a
     * short property hook have an expression.
     *
     * @return list<Node>|null
     */
    public static function body(FunctionLike $function): ?array
    {
        return match (true) {
            $function instanceof Expr\ArrowFunction => [$function->expr],
            $function instanceof PropertyHook => self::hook($function->body),
            default => self::statements($function->getStmts()),
        };
    }

    /**
     * @param Expr|array<Stmt>|null $body
     * @return list<Node>|null
     */
    private static function hook(Expr|array|null $body): ?array
    {
        return $body instanceof Expr ? [$body] : self::statements($body);
    }

    /**
     * @param array<Stmt>|null $statements
     * @return list<Node>|null
     */
    private static function statements(?array $statements): ?array
    {
        return $statements === null ? null : array_values($statements);
    }
}
