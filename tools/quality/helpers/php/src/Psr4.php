<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use function array_is_list;
use function is_array;
use function is_file;
use function is_string;
use function str_replace;
use function str_starts_with;
use function strlen;
use function substr;
use function uksort;

/**
 * The PSR-4 prefixes of a component's composer.json, which map a class name to a file.
 *
 * A name resolves to the first existing file below the directories of the
 * longest matching prefix, as Composer's class loader finds it.
 */
final readonly class Psr4
{
    /** @param array<string, list<string>> $prefixes directories by prefix, longest first */
    private function __construct(private array $prefixes) {}

    /** Validate the `autoload` list of a request: objects with a prefix and directories. */
    public static function from(mixed $value): self
    {
        if (!is_array($value) || !array_is_list($value)) {
            throw new RequestError('"autoload" must be a list of prefixes');
        }
        $prefixes = [];
        foreach ($value as $entry) {
            [$prefix, $directories] = self::entry($entry);
            $prefixes[$prefix] = [...$prefixes[$prefix] ?? [], ...$directories];
        }
        uksort($prefixes, static fn(string $a, string $b): int => strlen($b) <=> strlen($a));
        return new self($prefixes);
    }

    /** @return array{string, list<string>} */
    private static function entry(mixed $entry): array
    {
        if (!is_array($entry)) {
            throw new RequestError('an "autoload" entry must be an object');
        }
        $prefix = $entry['prefix'] ?? null;
        if (!is_string($prefix)) {
            throw new RequestError('an "autoload" entry needs a string "prefix"');
        }
        return [$prefix, Request::paths($entry['directories'] ?? null, 'directories')];
    }

    /** Return the file that defines the class `$name`, or null when none exists. */
    public function resolve(string $name): ?string
    {
        foreach ($this->prefixes as $prefix => $directories) {
            if (!str_starts_with($name, (string) $prefix)) {
                continue;
            }
            $relative = str_replace('\\', '/', substr($name, strlen((string) $prefix))) . '.php';
            foreach ($directories as $directory) {
                $path = Paths::normalize($directory . '/' . $relative);
                if (is_file($path)) {
                    return $path;
                }
            }
        }
        return null;
    }
}
