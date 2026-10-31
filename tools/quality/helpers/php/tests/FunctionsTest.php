<?php

declare(strict_types=1);

namespace Quality\PhpHelper\Tests;

use PhpParser\Node\Expr;
use PhpParser\Node\Param;
use PhpParser\Node\PropertyHook;
use Quality\PhpHelper\FunctionMetrics;
use Quality\PhpHelper\Naming;

final class FunctionsTest extends Measuring
{
    public function testFunctionsAreNamedByTheirScopes(): void
    {
        $code = <<<'PHP'
            namespace App;

            function top(int $a) {}

            interface Shape
            {
                public function area(): float;
            }

            abstract class Base
            {
                abstract public function name(): string;

                public function run() {}
            }

            trait Greets
            {
                public function greet() {}
            }

            enum Mode
            {
                case On;

                public function label() {}
            }
            PHP;
        self::assertSame(['top', 'Base.run', 'Greets.greet', 'Mode.label'], $this->names($code));
    }

    public function testClosuresAreNamedByWhatTheyAreAssignedTo(): void
    {
        $code = <<<'PHP'
            class Base
            {
                public function run($a, $b, ...$rest)
                {
                    $f = function () {};
                    $this->handler = fn() => 1;
                    self::$shared = fn() => 2;
                    $list[] = fn() => 3;
                    $g = &$f;
                    call(callback: fn() => 4);
                    call(fn() => 5);
                    $this->{$a} = fn() => 6;
                    $$b = fn() => 7;
                    return new class {
                        public function inner() {}
                    };
                }
            }
            PHP;
        self::assertSame(
            [
                'Base.run',
                'Base.run.f',
                'Base.run.handler',
                'Base.run.shared',
                'Base.run.<anonymous>',
                'Base.run.callback',
                'Base.run.<anonymous>',
                'Base.run.<anonymous>',
                'Base.run.<anonymous>',
                'Base.run.class@anonymous.inner',
            ],
            $this->names($code),
        );
    }

    public function testPropertyHooksAreNamedByTheirProperty(): void
    {
        $code = <<<'PHP'
            class Account
            {
                public string $label {
                    get => strtoupper($this->label);
                    set (string $value) {
                        $this->label = $value;
                    }
                }

                public function __construct(public int $id { set => $value + 1; }) {}
            }

            interface Named
            {
                public string $name { get; }
            }
            PHP;
        self::assertSame(
            ['Account.get label', 'Account.set label', 'Account.__construct', 'Account.__construct.set id'],
            $this->names($code),
        );
    }

    public function testAHookWithoutANamedPropertyIsAnonymous(): void
    {
        // The parser stops at errors instead of building error nodes, so only built nodes reach these.
        $hook = new PropertyHook('get', null);
        self::assertSame('get <anonymous>', Naming::function($hook, null));
        self::assertSame('get <anonymous>', Naming::function($hook, new Param(new Expr\Error())));
    }

    public function testTheLineIsTheFirstLineAfterTheAttributes(): void
    {
        $code = "class A\n{\n    #[Attribute]\n    /** doc */\n    public function f() {}\n\n    public function g() {}\n}\n";
        $functions = $this->functions($code);
        self::assertSame(6, $functions['A.f']->line);
        self::assertSame(8, $functions['A.g']->line);
    }

    public function testParametersAreCounted(): void
    {
        $functions = $this->functions("function f(\$a, int \$b = 1, ...\$c) {}\n\$g = fn() => 1;\n");
        self::assertSame(3, $functions['f']->parameters);
        self::assertSame(0, $functions['g']->parameters);
    }

    public function testMetricsAreSerializedWithTheProtocolNames(): void
    {
        $encoded = json_encode($this->only("function f() {\n    return 1;\n}\n"));
        self::assertSame(
            '{"name":"f","line":2,"code_lines":1,"complexity":1,"nesting":0,"parameters":0}',
            $encoded,
        );
    }

    /** @return list<string> */
    private function names(string $code): array
    {
        $functions = $this->measure("<?php\n" . $code)['functions'];
        self::assertIsArray($functions);
        $names = [];
        foreach ($functions as $function) {
            self::assertInstanceOf(FunctionMetrics::class, $function);
            $names[] = $function->name;
        }
        return $names;
    }
}
