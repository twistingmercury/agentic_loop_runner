import pathlib
from enum import StrEnum, auto
from typing import Annotated

import yaml
from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
)


class TaskState(StrEnum):
    PENDING = auto()
    COMPLETED = auto()
    FAILED = auto()


type NonBlankString = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]


class Task(BaseModel):
    id: int = Field(gt=0, strict=True)
    name: NonBlankString
    prompt: NonBlankString
    state: TaskState = Field(default=TaskState.PENDING, validate_default=True)

    @field_validator("state", mode="before")
    @classmethod
    def validate_task_state(cls, state: TaskState) -> TaskState:
        if state == "":
            state = TaskState.PENDING

        return state

    def __str__(self) -> str:
        normalized = f"{self.id}: {self.name}\n\n{self.prompt}"
        return normalized


class TaskList(BaseModel):
    tasks: list[Task] = Field(min_length=1)

    @field_validator("tasks")
    @classmethod
    def validate_ids_unique(cls, tasks: list[Task]) -> list[Task]:
        ids = [task.id for task in tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("items must have unique ids")

        return tasks


def parse_tasks(path: str | pathlib.Path):
    file = pathlib.Path(path)
    data = file.read_text()
    tasks = yaml.safe_load(data)
    task_list = TaskList.model_validate(tasks)
    return task_list


def summarize_tasks(task_list: TaskList):
    color_red = "\033[1;91m"
    color_grn = "\033[92m"
    color_rst = "\033[0m"

    # Pad the plain text first, then add color, so the escape codes
    # don't throw off the column widths.
    id_width = max(len("ID"), *(len(str(task.id)) for task in task_list.tasks))
    state_width = max(len(state) for state in TaskState)

    print(f"   {'ID':>{id_width}}  {'STATE':<{state_width}}  NAME")
    print(f"   {'-' * id_width}  {'-' * state_width}  {'-' * 4}")

    for task in task_list.tasks:
        emoji = "  "
        color = ""
        note = ""

        match task.state:
            case TaskState.FAILED:
                emoji = "\u274c"
                color = color_red
                note = f"  {color_red}\u2190 Needs review!{color_rst}"
            case TaskState.COMPLETED:
                emoji = "\u2705"
                color = color_grn

        state = f"{color}{task.state.upper():<{state_width}}{color_rst}"
        print(f"{emoji} {task.id:>{id_width}}  {state}  {task.name}{note}")


def validate_tasks(task_list: TaskList) -> bool:
    for task in task_list.tasks:
        match task.state:
            case TaskState.FAILED:
                return False
            case TaskState.COMPLETED | TaskState.PENDING:
                continue

    return True
