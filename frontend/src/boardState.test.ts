import { describe, expect, it } from "vitest";
import { applyIssue, applyPlacement, Column, Issue, placementFor, removeIssue } from "./boardState";

const issue = (id: number, column: number, rank: string, version = 1): Issue => ({
  id,
  key: `APP-${id}`,
  project: 1,
  column,
  number: id,
  title: `Issue ${id}`,
  description: "",
  assignee: null,
  priority: "MEDIUM",
  story_points: null,
  due_date: null,
  rank,
  version,
});

const board = (): Column[] => [
  { id: 10, name: "To Do", position: 0, issues: [issue(1, 10, "F"), issue(2, 10, "M"), issue(3, 10, "T")] },
  { id: 20, name: "Done", position: 1, issues: [issue(4, 20, "M")] },
];

const ids = (c: Column) => c.issues.map((i) => i.id);

describe("applyIssue", () => {
  it("moves an issue between columns", () => {
    const next = applyIssue(board(), issue(1, 20, "T", 2));
    expect(ids(next[0])).toEqual([2, 3]);
    expect(ids(next[1])).toEqual([4, 1]);
  });

  it("sorts by rank bytewise, so uppercase sorts before lowercase", () => {
    const next = applyIssue(board(), issue(9, 10, "a"));
    expect(ids(next[0])).toEqual([1, 2, 3, 9]);
    expect(ids(applyIssue(board(), issue(9, 10, "G"))[0])).toEqual([1, 9, 2, 3]);
  });

  it("is idempotent, because the mover also receives its own broadcast", () => {
    const once = applyIssue(board(), issue(1, 20, "T", 2));
    expect(applyIssue(once, issue(1, 20, "T", 2))).toEqual(once);
  });

  it("ignores a delayed broadcast older than what it has", () => {
    const newer = applyIssue(board(), issue(1, 20, "T", 5));
    expect(applyIssue(newer, issue(1, 10, "F", 4))).toBe(newer);
  });

  it("leaves untouched columns as the same object", () => {
    const b = board();
    expect(applyIssue(b, issue(1, 10, "G", 2))[1]).toBe(b[1]);
  });
});

describe("removeIssue", () => {
  it("removes it wherever it is", () => {
    expect(ids(removeIssue(board(), 4)[1])).toEqual([]);
  });
});

describe("placementFor", () => {
  it("dragging down lands below the card under the pointer", () => {
    expect(placementFor(board(), 1, 2, 10)).toEqual({ column: 10, before: 2, after: 3 });
  });

  it("dragging up lands above the card under the pointer", () => {
    expect(placementFor(board(), 3, 1, 10)).toEqual({ column: 10, before: null, after: 1 });
  });

  it("dropping on another column's card lands above it", () => {
    expect(placementFor(board(), 1, 4, 20)).toEqual({ column: 20, before: null, after: 4 });
  });

  it("dropping on a column appends", () => {
    expect(placementFor(board(), 1, null, 20)).toEqual({ column: 20, before: 4, after: null });
  });

  it("dropping on itself or in the same slot does nothing", () => {
    expect(placementFor(board(), 1, 1, 10)).toBeNull();
    expect(placementFor(board(), 3, null, 10)).toBeNull();
  });
});

describe("applyPlacement", () => {
  it("matches what the placement asked for", () => {
    const b = board();
    expect(ids(applyPlacement(b, 1, placementFor(b, 1, 2, 10)!)[0])).toEqual([2, 1, 3]);
    expect(ids(applyPlacement(b, 3, placementFor(b, 3, 1, 10)!)[0])).toEqual([3, 1, 2]);
    expect(ids(applyPlacement(b, 1, placementFor(b, 1, 4, 20)!)[1])).toEqual([1, 4]);
  });
});
