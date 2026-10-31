<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use JsonSerializable;
use PhpParser\Node;
use PhpParser\Node\Expr;
use PhpParser\Node\Name;
use PhpParser\Node\Scalar;
use PhpParser\Node\Stmt;
use PhpParser\Node\UseItem;
use PhpParser\PrettyPrinter\Standard;

use function array_values;
use function dirname;
use function is_file;
use function str_starts_with;

/**
 * The imports of a file and the files they resolve to.
 *
 * Imports are `use` declarations, `include` and `require` expressions, and
 * class names in code that resolve through the component's PSR-4 prefixes.
 * A class name resolves to the file the class loader would read; a path to
 * an existing file when it is absolute or `__DIR__` followed by a literal.
 * Each specifier is reported once, at its first line.
 */
final class Imports implements JsonSerializable
{
    /** @var array<string, array{specifier: string, line: int, resolved: ?string}> */
    private array $found = [];

    private function __construct(
        private readonly Source $source,
        private readonly Psr4 $autoload,
    ) {}

    public static function of(Source $source, Psr4 $autoload): self
    {
        $imports = new self($source, $autoload);
        $imports->visit($source->statements, null);
        return $imports;
    }

    /** @return list<array{specifier: string, line: int, resolved: ?string}> */
    public function jsonSerialize(): array
    {
        return array_values($this->found);
    }

    /** @param array<Node> $nodes */
    private function visit(array $nodes, ?Node $parent): void
    {
        foreach ($nodes as $node) {
            match (true) {
                $node instanceof Stmt\Use_ => $this->uses($node->uses, '', $node->type),
                $node instanceof Stmt\GroupUse => $this->uses($node->uses, $node->prefix->toString() . '\\', $node->type),
                $node instanceof Expr\Include_ => $this->include($node),
                $node instanceof Name\FullyQualified => $this->reference($node, $parent),
                default => null,
            };
            $this->visit(Tree::children($node), $node);
        }
    }

    /** @param array<UseItem> $items */
    private function uses(array $items, string $prefix, int $type): void
    {
        foreach ($items as $item) {
            $name = $prefix . $item->name->toString();
            $class = ($item->type === Stmt\Use_::TYPE_UNKNOWN ? $type : $item->type) === Stmt\Use_::TYPE_NORMAL;
            $this->add($name, $item->getStartLine(), $class ? $this->autoload->resolve($name) : null);
        }
    }

    /** A class name in code, which is not the name of a called function or a constant. */
    private function reference(Name\FullyQualified $name, ?Node $parent): void
    {
        if ($parent instanceof Expr\FuncCall || $parent instanceof Expr\ConstFetch) {
            return;
        }
        $resolved = $this->autoload->resolve($name->toString());
        if ($resolved !== null) {
            $this->add($name->toString(), $name->getStartLine(), $resolved);
        }
    }

    private function include(Expr\Include_ $include): void
    {
        $path = $this->path($include->expr);
        $resolved = $path !== null && is_file($path) ? $path : null;
        $this->add((new Standard())->prettyPrintExpr($include->expr), $include->getStartLine(), $resolved);
    }

    /** The path an include names: absolute, or `__DIR__` followed by a literal. */
    private function path(Expr $expr): ?string
    {
        if ($expr instanceof Scalar\String_ && str_starts_with($expr->value, '/')) {
            return Paths::normalize($expr->value);
        }
        $relative = $expr instanceof Expr\BinaryOp\Concat && $expr->left instanceof Scalar\MagicConst\Dir;
        if ($relative && $expr->right instanceof Scalar\String_) {
            return Paths::normalize(dirname($this->source->path) . $expr->right->value);
        }
        return null;
    }

    private function add(string $specifier, int $line, ?string $resolved): void
    {
        $this->found[$specifier] ??= ['specifier' => $specifier, 'line' => $line, 'resolved' => $resolved];
    }
}
