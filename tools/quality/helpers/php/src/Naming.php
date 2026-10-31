<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use PhpParser\Node;
use PhpParser\Node\Arg;
use PhpParser\Node\Expr;
use PhpParser\Node\FunctionLike;
use PhpParser\Node\Identifier;
use PhpParser\Node\Param;
use PhpParser\Node\PropertyHook;
use PhpParser\Node\Stmt;

use function is_string;

/** Names of functions and of the declarations that qualify them (decision 0007). */
final class Naming
{
    public const string ANONYMOUS = '<anonymous>';
    public const string ANONYMOUS_CLASS = 'class@anonymous';

    /** The own name of a function, without its scopes; `$parent` is the node that holds it. */
    public static function function(FunctionLike $function, ?Node $parent): string
    {
        return match (true) {
            $function instanceof Stmt\Function_, $function instanceof Stmt\ClassMethod => $function->name->toString(),
            $function instanceof PropertyHook => $function->name->toString() . ' ' . self::property($parent),
            default => self::closure($function, $parent),
        };
    }

    /** The scope segment a class, interface, trait, or enum adds, if any. */
    public static function scope(Node $node): ?string
    {
        if (!$node instanceof Stmt\ClassLike) {
            return null;
        }
        return $node->name?->toString() ?? self::ANONYMOUS_CLASS;
    }

    /** The property a hook belongs to: a declared or a promoted one. */
    private static function property(?Node $parent): string
    {
        return match (true) {
            $parent instanceof Stmt\Property => $parent->props[0]->name->toString(),
            $parent instanceof Param => self::variable($parent->var),
            default => self::ANONYMOUS,
        };
    }

    /** A closure takes the name of what it is assigned to or passed as. */
    private static function closure(FunctionLike $closure, ?Node $parent): string
    {
        return match (true) {
            $parent instanceof Expr\Assign, $parent instanceof Expr\AssignRef => self::target($parent->var),
            $parent instanceof Arg && $parent->name !== null => $parent->name->toString(),
            default => self::ANONYMOUS,
        };
    }

    private static function target(Expr $target): string
    {
        return match (true) {
            $target instanceof Expr\Variable => self::variable($target),
            $target instanceof Expr\PropertyFetch, $target instanceof Expr\NullsafePropertyFetch,
            $target instanceof Expr\StaticPropertyFetch => self::identifier($target->name),
            default => self::ANONYMOUS,
        };
    }

    private static function variable(Expr $variable): string
    {
        return $variable instanceof Expr\Variable ? self::identifier($variable->name) : self::ANONYMOUS;
    }

    private static function identifier(Expr|Identifier|string $name): string
    {
        return match (true) {
            is_string($name) => $name,
            $name instanceof Identifier => $name->toString(),
            default => self::ANONYMOUS,
        };
    }
}
