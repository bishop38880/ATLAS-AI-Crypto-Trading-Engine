import re
from pathlib import Path


def main() -> None:
    for file in ["atlas/orchestrator/test_scorer.py", "atlas/pipeline/test_orchestrator.py"]:
        path = Path(file)
        if not path.exists():
            continue
        content = path.read_text()

        # Check if pytest is imported, if not add it
        if "import pytest" not in content:
            content = "import pytest\n" + content

        # Add @pytest.mark.asyncio to test methods
        # Be careful not to add it multiple times
        if "@pytest.mark.asyncio" not in content:
            content = re.sub(r'(\s+)def test_', r'\1@pytest.mark.asyncio\1async def test_', content)
        else:
            content = re.sub(r'(\s+)def test_', r'\1async def test_', content)

        content = content.replace("scorer.score(", "await scorer.score(")
        content = content.replace("orchestrator.run(", "await orchestrator.run(")

        path.write_text(content)


if __name__ == "__main__":
    main()
