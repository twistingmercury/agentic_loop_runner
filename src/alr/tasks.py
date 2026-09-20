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


class TaskStatus(StrEnum):
    PENDING = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    BLOCKED = auto()
    ABANDONED = auto()


type NonBlankString = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]


class Task(BaseModel):
    id: int = Field(gt=0, strict=True)
    title: NonBlankString
    agent: NonBlankString
    checkpoint: str
    prompt: NonBlankString
    status: TaskStatus = TaskStatus.PENDING


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
