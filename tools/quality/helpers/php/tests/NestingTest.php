<?php

declare(strict_types=1);

namespace Quality\PhpHelper\Tests;

use PHPUnit\Framework\Attributes\DataProvider;

final class NestingTest extends Measuring
{
    /** @return iterable<string, array{string, int}> */
    public static function bodies(): iterable
    {
        yield 'flat' => ['return 1;', 0];
        yield 'if in a loop' => ['foreach ($a as $b) { if ($b) { return 1; } }', 2];
        yield 'every block kind' => [
            'for (;;) { while ($a) { do { switch ($a) { default: try {} finally {} } } while ($a); } }',
            5,
        ];
        yield 'elseif stays on the chain' => ['if ($a) {} elseif ($b) { if ($c) {} }', 2];
        yield 'else if stays on the chain' => ['if ($a) {} else if ($b) { if ($c) {} }', 2];
        yield 'else if with a comment' => ["if (\$a) {} else /* note */ if (\$b) {}", 1];
        yield 'an if inside else braces opens' => ['if ($a) {} else { if ($b) {} }', 2];
        yield 'a match opens nothing' => ['return match ($a) { default => 1 };', 0];
        yield 'nested functions start at zero' => ['if ($a) { $f = function () { if ($b) {} }; }', 1];
    }

    #[DataProvider('bodies')]
    public function testTheDeepestChainOfBlocksCounts(string $body, int $expected): void
    {
        self::assertSame($expected, $this->functions("function f() {\n{$body}\n}\n")['f']->nesting);
    }
}
