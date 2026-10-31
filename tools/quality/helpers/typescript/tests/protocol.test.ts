import { expect, test } from "vitest";
import {
  absolutePath,
  describe,
  field,
  isRecord,
  optionalPath,
  optionalStrings,
  parseRequest,
  pathList,
  RequestError,
} from "../src/protocol.ts";

test("a request is a JSON object with protocol 1", () => {
  expect(parseRequest('{"protocol": 1, "x": 2}')).toEqual({
    protocol: 1,
    x: 2,
  });
  expect(() => parseRequest("{")).toThrow(/the request is not JSON/);
  expect(() => parseRequest("[1]")).toThrow("the request is not a JSON object");
  expect(() => parseRequest("null")).toThrow(RequestError);
  expect(() => parseRequest('{"protocol": 2}')).toThrow("protocol 2 is not 1");
  expect(() => parseRequest("{}")).toThrow("protocol undefined is not 1");
});

test("paths must be absolute", () => {
  const request = { a: "/abs", b: "relative", c: 3, d: null };
  expect(absolutePath(request, "a")).toBe("/abs");
  expect(() => absolutePath(request, "b")).toThrow(
    '"b" holds a value that is not an absolute path',
  );
  expect(() => absolutePath(request, "c")).toThrow(RequestError);
  expect(() => absolutePath(request, "missing")).toThrow(RequestError);
  expect(optionalPath(request, "d")).toBeNull();
  expect(optionalPath(request, "a")).toBe("/abs");
  expect(() => optionalPath(request, "missing")).toThrow(RequestError);
});

test("a path list holds absolute paths only", () => {
  expect(pathList({ files: ["/a", "/b"] }, "files")).toEqual(["/a", "/b"]);
  expect(pathList({ files: [] }, "files")).toEqual([]);
  expect(() => pathList({ files: "/a" }, "files")).toThrow(
    '"files" is not a list of absolute paths',
  );
  expect(() => pathList({ files: ["/a", "b"] }, "files")).toThrow(RequestError);
});

test("an optional string list may be left out", () => {
  expect(optionalStrings({ rules: ["a", "b"] }, "rules")).toEqual(["a", "b"]);
  expect(optionalStrings({}, "rules")).toEqual([]);
  expect(() => optionalStrings({ rules: "a" }, "rules")).toThrow(
    '"rules" is not a list of strings',
  );
  expect(() => optionalStrings({ rules: ["a", 1] }, "rules")).toThrow(
    RequestError,
  );
});

test("records, fields, and error messages", () => {
  expect(isRecord({})).toBe(true);
  expect(isRecord(null)).toBe(false);
  expect(isRecord([])).toBe(false);
  expect(isRecord("text")).toBe(false);
  expect(field({ a: 1 }, "a")).toBe(1);
  expect(field([1], "0")).toBeUndefined();
  expect(field(undefined, "a")).toBeUndefined();
  expect(describe(new Error("broken"))).toBe("broken");
  expect(describe("thrown text")).toBe("thrown text");
});
