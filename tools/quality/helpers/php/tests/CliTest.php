<?php

declare(strict_types=1);

namespace Quality\PhpHelper\Tests;

use PHPUnit\Framework\Attributes\DataProvider;
use Quality\PhpHelper\Cli;

final class CliTest extends Measuring
{
    public function testAStructureRequestIsAnswered(): void
    {
        $file = $this->write('src/Sample.php', "<?php\nfunction f() {}\n");
        $request = $this->request(['autoload' => [], 'files' => [$file]]);
        $answer = $this->decode(Cli::run(['structure', $request]));
        self::assertSame(1, $answer['protocol']);
        self::assertIsArray($answer['files']);
        self::assertCount(1, $answer['files']);
    }

    /** @return iterable<string, array{list<string>|null, mixed, string}> */
    public static function failures(): iterable
    {
        yield 'no arguments' => [[], null, 'usage: main.php <command> <request.json>'];
        yield 'an unknown command' => [['lint', 'request.json'], null, 'unknown command "lint"; usage: main.php <command> <request.json>'];
        yield 'a missing request' => [['structure', '/missing/request.json'], null, 'cannot read the request /missing/request.json'];
        yield 'not JSON' => [null, '{"protocol": 1', 'the request is not JSON: Syntax error'];
        yield 'not an object' => [null, '"text"', 'the request is not an object of protocol 1'];
        yield 'another protocol' => [null, ['protocol' => 2], 'the request is not an object of protocol 1'];
        yield 'no autoload list' => [null, ['protocol' => 1, 'autoload' => ['a' => 1]], '"autoload" must be a list of prefixes'];
        yield 'an autoload entry that is no object' => [null, ['protocol' => 1, 'autoload' => [1]], 'an "autoload" entry must be an object'];
        yield 'an autoload entry without prefix' => [null, ['protocol' => 1, 'autoload' => [['directories' => []]]], 'an "autoload" entry needs a string "prefix"'];
        yield 'relative directories' => [null, ['protocol' => 1, 'autoload' => [['prefix' => 'A\\', 'directories' => ['src']]]], '"directories" holds a path that is not absolute'];
        yield 'no file list' => [null, ['protocol' => 1, 'autoload' => []], '"files" must be a list of paths'];
        yield 'a file that is no string' => [null, ['protocol' => 1, 'autoload' => [], 'files' => [1]], '"files" holds a path that is not absolute'];
        yield 'a missing file' => [null, ['protocol' => 1, 'autoload' => [], 'files' => ['/missing.php']], 'cannot read /missing.php'];
    }

    /** @param list<string>|null $arguments */
    #[DataProvider('failures')]
    public function testARequestThatCannotBeAnsweredIsAFailure(?array $arguments, mixed $document, string $message): void
    {
        $text = is_string($document) ? $document : (string) json_encode($document);
        $answer = $this->decode(Cli::run($arguments ?? ['structure', $this->write('request.json', $text)]));
        self::assertSame(['protocol' => 1, 'failure' => $message], $answer);
    }

    public function testAFailureMessageKeepsItsFirstLine(): void
    {
        $answer = $this->decode(Cli::run(["two\nlines", 'request.json']));
        self::assertSame('unknown command "two', $answer['failure']);
    }

    public function testTheProgramPrintsTheAnswer(): void
    {
        $argv = ['main.php'];
        ob_start();
        require __DIR__ . '/../bin/main.php';
        $output = (string) ob_get_clean();
        self::assertSame("{\"protocol\":1,\"failure\":\"usage: main.php <command> <request.json>\"}\n", $output);
        self::assertSame(['main.php'], $argv);
    }

    /** @param array<string, mixed> $document */
    private function request(array $document): string
    {
        return $this->write('request.json', (string) json_encode(['protocol' => 1, ...$document]));
    }

    /** @return array<mixed> */
    private function decode(string $text): array
    {
        $answer = json_decode($text, true);
        self::assertIsArray($answer);
        return $answer;
    }
}
