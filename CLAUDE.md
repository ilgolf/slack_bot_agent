## Overview

Use `plan.md` as the source of truth. Treat each unchecked test item as the next development increment and complete exactly one TDD slice at a time.

## Core Rules

- Follow the instructions in `plan.md` before applying any other workflow guidance.
- When the user says `go`, open `plan.md`, find the next unchecked or unmarked test, and work only on that item.
- Write one test at a time.
- Make the test fail first.
- Implement only enough production code to make that test pass.
- Refactor only after the test is green.
- Run all fast tests after each green step. Skip only tests that are clearly long-running.
- If `plan.md` is missing, ambiguous, or has no remaining unchecked tests, stop and report that state clearly.

## Workflow

1. Read `plan.md` and identify the next unchecked test item in file order.
2. Translate that item into a single small failing test.
3. Run the narrowest relevant test command to confirm the red state.
4. Change production code with the smallest possible behavioral change.
5. Re-run the relevant test until it passes.
6. Run the full fast test suite.
7. Perform refactoring only while tests are passing.
8. Mark the completed item in `plan.md` only after the tests are green and the change is stable.

## TDD Guidance

- Start with the smallest test that proves the next bit of behavior.
- Use clear behavior-focused test names.
- Keep failures specific and easy to diagnose.
- Do not add speculative functionality beyond the active test.
- When fixing a defect, first write a failing API-level test, then write the smallest test that reproduces the bug, then make both pass.

## Tidy First Guidance

- Separate structural changes from behavioral changes.
- Make structural changes first when both are needed.
- Do not mix structural and behavioral changes in the same commit.
- Run tests before and after each structural change to confirm behavior is unchanged.

## Code Quality Rules

- Eliminate duplication when it improves clarity.
- Keep methods small and focused.
- Make dependencies explicit.
- Minimize side effects and mutable state.
- Prefer the simplest solution that can pass the current test.

## Refactoring Rules

- Refactor only in the green phase.
- Make one refactoring step at a time.
- Run tests after each refactoring step.
- Prefer refactorings that remove duplication or improve intent.

## Commit Discipline

- Commit only when all fast tests are passing.
- Resolve compiler and linter warnings before committing.
- Keep each commit to one logical unit of work.
- Label commits clearly as structural or behavioral when applicable.

## Execution Pattern

When the user says `go`:

1. Read `plan.md`.
2. Pick the next unchecked test.
3. Add that test and make it fail.
4. Implement the minimum code to make it pass.
5. Run the relevant test, then all fast tests.
6. Refactor if needed.
7. Mark the item in `plan.md`.
8. Stop after that single increment unless the user asks to continue.
