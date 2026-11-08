<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use PhpParser\Error;

use function array_map;
use function max;

/**
 * The `structure` answer for each file: its functions, imports, and loading.
 *
 * A file that does not parse is answered with its syntax error and nothing
 * else; the core reports it as a structure finding.
 */
final readonly class Structure
{
    public function __construct(private Psr4 $autoload) {}

    /**
     * @param list<string> $files
     * @return list<array<string, mixed>>
     */
    public function answer(array $files): array
    {
        return array_map(fn(string $path): array => $this->file($path), $files);
    }

    /** @return array<string, mixed> */
    public function file(string $path): array
    {
        try {
            $source = Source::parse($path);
        } catch (Error $error) {
            return [
                'file' => $path,
                'functions' => [],
                'imports' => [],
                'loadable' => false,
                'units' => 0,
                'error' => ['line' => max(0, $error->getStartLine()), 'message' => $error->getRawMessage()],
            ];
        }
        return [
            'file' => $path,
            'functions' => Functions::of($source),
            'imports' => Imports::of($source, $this->autoload),
            'loadable' => Loading::declaresOnly($source->statements),
            'units' => Loading::units($source->statements),
            'error' => null,
        ];
    }
}
