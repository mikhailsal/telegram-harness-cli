<?php

declare(strict_types=1);

namespace Quality\PhpHelper\Tests;

use FilesystemIterator;
use PHPUnit\Framework\TestCase;
use Quality\PhpHelper\FunctionMetrics;
use Quality\PhpHelper\Psr4;
use Quality\PhpHelper\Structure;
use RecursiveDirectoryIterator;
use RecursiveIteratorIterator;
use SplFileInfo;

/** Writes PHP files into a new directory and measures them with the helper. */
abstract class Measuring extends TestCase
{
    protected string $directory = '';

    protected function setUp(): void
    {
        $this->directory = sys_get_temp_dir() . '/php-helper-' . bin2hex(random_bytes(8));
        mkdir($this->directory);
    }

    protected function tearDown(): void
    {
        $this->remove($this->directory);
    }

    /** Write `$code` to `$name` below the directory; return the file's path. */
    protected function write(string $name, string $code): string
    {
        $path = $this->directory . '/' . $name;
        if (!is_dir(dirname($path))) {
            mkdir(dirname($path), recursive: true);
        }
        file_put_contents($path, $code);
        return $path;
    }

    /**
     * Measure `$code` as a file of a component whose prefix `App\` maps to the directory.
     *
     * @return array<string, mixed>
     */
    protected function measure(string $code, string $name = 'Sample.php'): array
    {
        $path = $this->write($name, $code);
        return (new Structure($this->autoload()))->file($path);
    }

    /**
     * Return the functions of `$code` by name.
     *
     * @return array<string, FunctionMetrics>
     */
    protected function functions(string $code): array
    {
        $functions = $this->measure("<?php\n" . $code)['functions'];
        self::assertIsArray($functions);
        $found = [];
        foreach ($functions as $function) {
            self::assertInstanceOf(FunctionMetrics::class, $function);
            $found[$function->name] = $function;
        }
        return $found;
    }

    /** Measure the only function of `$code`. */
    protected function only(string $code): FunctionMetrics
    {
        $functions = $this->functions($code);
        self::assertCount(1, $functions);
        return array_values($functions)[0];
    }

    protected function autoload(): Psr4
    {
        return Psr4::from([['prefix' => 'App\\', 'directories' => [$this->directory]]]);
    }

    private function remove(string $path): void
    {
        $entries = new RecursiveIteratorIterator(
            new RecursiveDirectoryIterator($path, FilesystemIterator::SKIP_DOTS),
            RecursiveIteratorIterator::CHILD_FIRST,
        );
        foreach ($entries as $entry) {
            self::assertInstanceOf(SplFileInfo::class, $entry);
            $entry->isDir() ? rmdir($entry->getPathname()) : unlink($entry->getPathname());
        }
        rmdir($path);
    }
}
