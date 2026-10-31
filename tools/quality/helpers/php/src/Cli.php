<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use Throwable;

use function array_slice;
use function count;
use function explode;
use function json_encode;
use function sprintf;

/**
 * The command line: `main.php <command> <request.json>`.
 *
 * The answer is one JSON document on standard output. A request the helper
 * cannot answer is answered with a one-line `failure` instead of a result, so
 * the program never needs `exit`: the exit status is not zero only when PHP
 * itself fails.
 */
final class Cli
{
    private const string USAGE = 'usage: main.php <command> <request.json>';

    /**
     * Answer the arguments that follow the program name.
     *
     * @param list<string> $arguments
     */
    public static function run(array $arguments): string
    {
        try {
            $answer = self::answer($arguments);
        } catch (Throwable $error) {
            $answer = ['protocol' => Request::PROTOCOL, 'failure' => self::oneLine($error->getMessage())];
        }
        return json_encode($answer, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
    }

    /**
     * Print the answer for the program's arguments.
     *
     * @param array<int, string> $argv
     */
    public static function main(array $argv): void
    {
        echo self::run(array_slice($argv, 1)), "\n";
    }

    /**
     * @param list<string> $arguments
     * @return array<string, mixed>
     */
    private static function answer(array $arguments): array
    {
        if (count($arguments) !== 2) {
            throw new RequestError(self::USAGE);
        }
        [$command, $path] = $arguments;
        if ($command !== 'structure') {
            throw new RequestError(sprintf('unknown command "%s"; %s', $command, self::USAGE));
        }
        $request = StructureRequest::from(Request::read($path));
        $structure = new Structure($request->autoload);
        return ['protocol' => Request::PROTOCOL, 'files' => $structure->answer($request->files)];
    }

    private static function oneLine(string $text): string
    {
        return explode("\n", $text)[0];
    }
}
