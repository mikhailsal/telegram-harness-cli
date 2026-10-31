<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use JsonException;

use function array_is_list;
use function file_get_contents;
use function is_array;
use function is_file;
use function is_string;
use function json_decode;
use function sprintf;
use function str_starts_with;

/** A request of the helper protocol: one JSON object, version 1. */
final class Request
{
    public const int PROTOCOL = 1;

    /**
     * Read the JSON request in the file at `$path`.
     *
     * @return array<mixed>
     */
    public static function read(string $path): array
    {
        $text = is_file($path) ? file_get_contents($path) : false;
        if ($text === false) {
            throw new RequestError(sprintf('cannot read the request %s', $path));
        }
        try {
            $document = json_decode($text, true, 512, JSON_THROW_ON_ERROR);
        } catch (JsonException $error) {
            throw new RequestError('the request is not JSON: ' . $error->getMessage());
        }
        if (!is_array($document) || ($document['protocol'] ?? null) !== self::PROTOCOL) {
            throw new RequestError(sprintf('the request is not an object of protocol %d', self::PROTOCOL));
        }
        return $document;
    }

    /**
     * Require a list of absolute paths.
     *
     * @return list<string>
     */
    public static function paths(mixed $value, string $what): array
    {
        if (!is_array($value) || !array_is_list($value)) {
            throw new RequestError(sprintf('"%s" must be a list of paths', $what));
        }
        $paths = [];
        foreach ($value as $path) {
            if (!is_string($path) || !str_starts_with($path, '/')) {
                throw new RequestError(sprintf('"%s" holds a path that is not absolute', $what));
            }
            $paths[] = $path;
        }
        return $paths;
    }
}
