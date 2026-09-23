import subprocess

from alr.tasks import TaskList, Task, TaskState


def start(prompt: str, task_list: TaskList):
    for _, task in enumerate(task_list.tasks):
        if task.state == TaskState.FAILED:
            # need to output a warning
            continue

        if task.state == TaskState.COMPLETED:
            # perhaps have something output that says
            # Already complete. Skipping...
            continue

        try:
            run_task(prompt, task)
            task.state = TaskState.COMPLETED
        except subprocess.CalledProcessError as e:
            task.state = TaskState.FAILED
            break

    # need to update tasks.yaml and write it back to file
    return task_list


def run_task(prompt: str, task: Task):
    combo_prompt = f"{prompt}\n\n{str(task)}\n"

    print(combo_prompt)
    result = subprocess.run(
        "claude --print --dangerously-skip-permissions",
        input=combo_prompt,
        capture_output=True,
        text=True,
        shell=True,
    )

    if result.returncode != 0:
        raise ChildProcessError(f"Task {task.id}: {task.name} failed: {result.stderr} ")


def verify_task_list(task_list: TaskList) -> bool:
    for _, task in enumerate(task_list.tasks):
        if task.state == TaskState.FAILED:
            # need to output a warning
            continue

        if task.state == TaskState.COMPLETED:
            # perhaps have something output that says
            # Already complete. Skipping...
            continue
