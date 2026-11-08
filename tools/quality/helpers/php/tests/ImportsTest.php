<?php

declare(strict_types=1);

namespace Quality\PhpHelper\Tests;

final class ImportsTest extends Measuring
{
    public function testUseDeclarationsAndClassNamesResolveThroughPsr4(): void
    {
        $model = $this->write('Models/User.php', "<?php\n");
        $helper = $this->write('Helper.php', "<?php\n");
        $code = <<<'PHP'
            <?php
            namespace App;

            use App\Models\User;
            use App\Models\{User as Person, Missing};
            use function App\helper;
            use const App\LIMIT;
            use function App\Models\{first};
            use Vendor\Package;

            final class Sample extends Helper
            {
                public function run(): User
                {
                    \strlen('x');
                    helper();
                    echo LIMIT;
                    return new \App\Models\User();
                }
            }
            PHP;
        self::assertSame(
            [
                ['specifier' => 'App\Models\User', 'line' => 4, 'resolved' => $model],
                ['specifier' => 'App\Models\Missing', 'line' => 5, 'resolved' => null],
                ['specifier' => 'App\helper', 'line' => 6, 'resolved' => null],
                ['specifier' => 'App\LIMIT', 'line' => 7, 'resolved' => null],
                ['specifier' => 'App\Models\first', 'line' => 8, 'resolved' => null],
                ['specifier' => 'Vendor\Package', 'line' => 9, 'resolved' => null],
                ['specifier' => 'App\Helper', 'line' => 11, 'resolved' => $helper],
            ],
            $this->imports($code),
        );
    }

    public function testIncludesResolveFromTheirDirectoryOrAnAbsolutePath(): void
    {
        $target = $this->write('lib/target.php', "<?php\n");
        $code = "<?php\nrequire __DIR__ . '/lib/target.php';\ninclude_once '{$target}';\n"
            . "require __DIR__ . '/lib/missing.php';\ninclude 'relative.php';\nrequire \$path;\n";
        self::assertSame(
            [
                ['specifier' => "__DIR__ . '/lib/target.php'", 'line' => 2, 'resolved' => $target],
                ['specifier' => "'{$target}'", 'line' => 3, 'resolved' => $target],
                ['specifier' => "__DIR__ . '/lib/missing.php'", 'line' => 4, 'resolved' => null],
                ['specifier' => "'relative.php'", 'line' => 5, 'resolved' => null],
                ['specifier' => '$path', 'line' => 6, 'resolved' => null],
            ],
            $this->imports($code),
        );
    }

    public function testAnImportIsReportedOnceAtItsFirstLine(): void
    {
        $this->write('Other.php', "<?php\n");
        $code = "<?php\nnamespace App;\nnew Other();\nnew Other();\n";
        self::assertSame([['specifier' => 'App\Other', 'line' => 3, 'resolved' => $this->directory . '/Other.php']], $this->imports($code));
    }

    /** @return mixed */
    private function imports(string $code): mixed
    {
        return json_decode((string) json_encode($this->measure($code)['imports']), true);
    }
}
