<?php

declare(strict_types=1);

namespace Quality\PhpHelper\Tests;

use PHPUnit\Framework\Attributes\DataProvider;

final class LoadingTest extends Measuring
{
    /** @return iterable<string, array{string, bool}> */
    public static function files(): iterable
    {
        yield 'declarations' => [
            "declare(strict_types=1);\nnamespace App;\nuse A\\B;\nuse A\\{C, D};\nconst X = 1;\n"
            . "interface I {}\nclass K {}\ntrait T {}\nenum E {}\nfunction f() {}\n",
            true,
        ];
        yield 'bracketed namespaces' => ["namespace A { class K {} }\nnamespace B { function f() {} }\n", true];
        yield 'an empty file' => ['', true];
        yield 'a statement' => ["function f() {}\nf();\n", false];
        yield 'a statement in a namespace' => ["namespace A;\necho 1;\n", false];
        yield 'a declare block' => ["declare(ticks=1) { }\n", false];
        yield 'a conditional declaration' => ["if (!function_exists('f')) { function f() {} }\n", false];
        yield 'output after the code' => ["class K {}\n?>\ntext\n", false];
    }

    #[DataProvider('files')]
    public function testOnlyDeclarationsAreLoadable(string $code, bool $loadable): void
    {
        self::assertSame($loadable, $this->measure("<?php\n" . $code)['loadable']);
    }

    public function testUnitsAreNamedFunctionsAndMethodsWithABody(): void
    {
        $code = <<<'PHP'
            <?php
            function top() { $f = function () {}; function inner() {} }
            interface I { public function a(); }
            abstract class K { abstract public function a(); public function b() { return new class { public function c() {} }; } }
            trait T { public function d() {} }
            enum E { case A; public function e() {} }
            PHP;
        self::assertSame(5, $this->measure($code)['units']);
    }
}
