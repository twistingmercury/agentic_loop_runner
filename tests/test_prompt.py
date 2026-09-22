from pathlib import Path

import pytest

from alr import prompt


@pytest.mark.parametrize(
    "path,expected_err",
    [
        ("bad_prompt_file.md", FileNotFoundError),
        ("bad_prompt_file.json", ValueError),
        (str(Path(__file__).parent / "data" / "empty_prompt.txt"), ValueError),
        (str(Path(__file__).parent / "data" / "prompt.md"), None),
    ],
)
def test_parse_prompt_errors(path: str | Path, expected_err):
    if expected_err is not None:
        with pytest.raises(expected_err):
            prompt.parse_prompt(path)
    else:
        prompt.parse_prompt(path)
