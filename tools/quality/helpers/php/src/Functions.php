<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use PhpParser\Node;
use PhpParser\Node\FunctionLike;

use function count;
use function end;
use function implode;

/**
 * Every measured function of a file, with its qualified name.
 *
 * Functions are measured at any depth, each on its own: a nested function's
 * lines also count toward the enclosing body, but its decisions and nesting
 * do not. Names join the enclosing classes and functions with dots.
 */
final class Functions
{
    /** @var list<FunctionMetrics> */
    private array $found = [];

    private function __construct(private readonly Source $source) {}

    /** @return list<FunctionMetrics> */
    public static function of(Source $source): array
    {
        $walker = new self($source);
        foreach ($source->statements as $statement) {
            $walker->visit($statement, null, []);
        }
        return $walker->found;
    }

    /** @param list<string> $scope */
    private function visit(Node $node, ?Node $parent, array $scope): void
    {
        $segment = $node instanceof FunctionLike ? Naming::function($node, $parent) : Naming::scope($node);
        if ($segment !== null) {
            $scope[] = $segment;
        }
        if ($node instanceof FunctionLike) {
            $this->measure($node, implode('.', $scope));
        }
        foreach (Tree::children($node) as $child) {
            $this->visit($child, $node, $scope);
        }
    }

    private function measure(FunctionLike $function, string $name): void
    {
        $body = Tree::body($function);
        if ($body === null) {
            return;
        }
        $this->found[] = new FunctionMetrics(
            name: $name,
            line: $this->line($function),
            codeLines: Lines::count($this->source, $body),
            complexity: Complexity::of($body),
            nesting: (new Nesting($this->source))->of($body),
            parameters: count($function->getParams()),
        );
    }

    /** The line where a function starts: after its attributes, at its first keyword. */
    private function line(FunctionLike $function): int
    {
        $groups = $function->getAttrGroups();
        $start = $groups === [] ? $function->getStartTokenPos() : end($groups)->getEndTokenPos() + 1;
        return $this->source->lineFrom($start);
    }
}
