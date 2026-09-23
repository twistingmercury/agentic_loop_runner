# Loop Prompt

You are working through one task from a task list. Complete only the task you
are given, then stop.

## Steps

1. Read the task's objective.
2. Do the work described in the task prompt.
3. Verify the result.
4. End your output with exactly one line of plain JSON, with no backticks or
   code fences and nothing after it: {"state": "completed"} if you finished
   the task, or {"state": "failed"} if you cannot.

## Rules

- Do not start any other task.
- Do not change the task's status; the runner owns it.
