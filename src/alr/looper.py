import subprocess

from alr.tasks import TaskList, Task, TaskState, save_tasks


def start(prompt: str, task_list: TaskList, task_list_path: str):
    for _, task in enumerate(task_list.tasks):
        if task.state == TaskState.COMPLETED:
            # perhaps have something output that says
            # Already complete. Skipping...
            continue

        try:
            run_task(prompt, task)
            task.state = TaskState.COMPLETED
            save_tasks(task_list_path, task_list)
        except ChildProcessError:
            task.state = TaskState.FAILED
            save_tasks(task_list_path, task_list)
            raise

    return task_list


def run_task(prompt: str, task: Task):
    combo_prompt = f"{prompt}\n\n{str(task)}\n"

    print(combo_prompt)
    result = subprocess.run(
        ["claude", "--print", "--dangerously-skip-permissions"],
        input=combo_prompt,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise ChildProcessError(f"Task {task.id}: {task.name} failed: {result.stderr} ")
