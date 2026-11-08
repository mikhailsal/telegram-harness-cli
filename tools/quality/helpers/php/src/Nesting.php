<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use PhpParser\Node;
use PhpParser\Node\FunctionLike;
use PhpParser\Node\Stmt;

use function array_map;
use function in_array;
use function max;

/**
 * Nesting depth of statement blocks with the counting rules of decision 0007.
 *
 * `if`, the loops, `switch`, and `try` open a level; an `elseif` or `else if`
 * stays on its chain's level, and a nested function starts at zero.
 */
final readonly class Nesting
{
    private const array OPENING = [
        Stmt\If_::class,
        Stmt\For_::class,
        Stmt\Foreach_::class,
        Stmt\While_::class,
        Stmt\Do_::class,
        Stmt\Switch_::class,
        Stmt\TryCatch::class,
    ];

    public function __construct(private Source $source) {}

    /** @param list<Node> $nodes */
    public function of(array $nodes): int
    {
        return max([0, ...array_map(fn(Node $node): int => $this->depth($node, false), $nodes)]);
    }

    private function depth(Node $node, bool $chained): int
    {
        if ($node instanceof FunctionLike) {
            return 0;
        }
        $inner = 0;
        foreach (Tree::children($node) as $child) {
            $inner = max($inner, $this->depth($child, $this->chained($node, $child)));
        }
        $opens = !$chained && in_array($node::class, self::OPENING, true);
        return $opens ? $inner + 1 : $inner;
    }

    /** Whether `$child` is the `if` of an `else if`: the next token after `else`. */
    private function chained(Node $parent, Node $child): bool
    {
        return $parent instanceof Stmt\Else_
            && $child instanceof Stmt\If_
            && $this->source->next($parent->getStartTokenPos() + 1) === $child->getStartTokenPos();
    }
}
