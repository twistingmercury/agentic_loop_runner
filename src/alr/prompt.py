from pathlib import Path


def parse_prompt(path: str | Path) -> str:
    file_path = Path(path)
    file_ext = file_path.suffix.lower()
    if file_ext not in [".md", ".markdown", ".txt"]:
        raise ValueError(
            f"File type {file_ext} is not supported. Must be a markdown or plain text file."
        )

    data = file_path.read_text().strip()
    if data == "":
        raise ValueError("The prompt is empty or all whitespace.")

    return data
