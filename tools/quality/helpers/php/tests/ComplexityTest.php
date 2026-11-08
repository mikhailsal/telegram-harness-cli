<?php

declare(strict_types=1);

namespace Quality\PhpHelper\Tests;

use PHPUnit\Framework\Attributes\DataProvider;

final class ComplexityTest extends Measuring
{
    /** @return iterable<string, array{string, int}> */
    public static function bodies(): iterable
    {
        yield 'straight' => ['return 1;', 1];
        yield 'if, elseif, else' => ['if ($a) { return 1; } elseif ($b) { return 2; } else { return 3; }', 3];
        yield 'conditional expressions' => ['return $a ? ($b ?: 1) : 2;', 3];
        yield 'loops' => ['for (;;) {} foreach ($a as $b) {} while ($a) {} do {} while ($a);', 5];
        yield 'catch clauses' => ['try {} catch (A $e) {} catch (B | C $e) {} finally {}', 3];
        yield 'logical operators' => ['return $a && $b || $c and $d or $e xor $f;', 5];
        yield 'null coalescing' => ['$a ??= $b; return $a ?? $c;', 3];
        yield 'switch cases' => ['switch ($a) { case 1: case 2: return 1; default: return 2; }', 3];
        yield 'match arms' => ['return match ($a) { 1, 2 => 3, 4 => 5, default => 6 };', 3];
        yield 'nested functions count alone' => ['$f = fn() => $a && $b; return function () { if ($a) {} };', 1];
    }

    #[DataProvider('bodies')]
    public function testDecisionsAddToOne(string $body, int $expected): void
    {
        self::assertSame($expected, $this->functions("function f() {\n{$body}\n}\n")['f']->complexity);
    }

    public function testANestedFunctionIsMeasuredOnItsOwn(): void
    {
        $functions = $this->functions("function f() {\n\$g = fn() => \$a && \$b;\nif (\$a) {}\n}\n");
        self::assertSame(2, $functions['f']->complexity);
        self::assertSame(2, $functions['f.g']->complexity);
    }
}
