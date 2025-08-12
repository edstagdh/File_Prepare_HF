from pathlib import Path
import json
from loguru import logger


def sort_json_by_names_in_place(file_path: str) -> None:
    json_file = Path(file_path)

    if not json_file.exists():
        logger.error(f"File does not exist: {json_file}")
        return

    try:
        with json_file.open('r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.exception(f"Failed to decode JSON: {e}")
        return
    except Exception as e:
        logger.exception(f"Failed to read the file: {e}")
        return

    if not isinstance(data, dict):
        logger.error("JSON must be an object with full names as keys.")
        return

    try:
        name_counts = {}
        for name in data.keys():
            name_counts[name] = name_counts.get(name, 0) + 1

        sorted_data = dict(sorted(data.items()))

    except Exception as e:
        logger.exception(f"Failed to process or sort data: {e}")
        return

    try:
        with json_file.open('w', encoding='utf-8') as f:
            json.dump(sorted_data, f, indent=2, ensure_ascii=False)
        logger.success(f"Successfully sorted and updated: {json_file}")
    except Exception as e:
        logger.exception(f"Failed to write sorted data to file: {e}")


if __name__ == '__main__':
    # Replace with your actual file path
    sort_json_by_names_in_place(r'Performers_Images.json')
