import re
from pathlib import Path

NEW_TESTS = """
@pytest.mark.asyncio
async def test_scorer_uses_meta_learner_when_available() -> None:
    scorer = _make_scorer()
    mock_learner = MagicMock()
    mock_learner.model = MagicMock()
    mock_learner.predict.return_value = 0.85
    scorer.meta_learner = mock_learner
    
    agent = _make_agent("technical", 100, 220)
    result = await scorer.score([agent], "BTCUSDT")
    assert result.score == 85
    
@pytest.mark.asyncio
async def test_scorer_falls_back_when_meta_learner_model_is_none() -> None:
    scorer = _make_scorer()
    mock_learner = MagicMock()
    mock_learner.model = None
    scorer.meta_learner = mock_learner
    
    agent = _make_agent("technical", 100, 220)
    result = await scorer.score([agent], "BTCUSDT")
    assert result.score > 0
"""


def main() -> None:
    content = Path("atlas/orchestrator/test_scorer.py").read_text()
    content = re.sub(
        r"async def test_scorer_uses_meta_learner_when_available.*",
        "",
        content,
        flags=re.DOTALL,
    )
    Path("atlas/orchestrator/test_scorer.py").write_text(content + NEW_TESTS)


if __name__ == "__main__":
    main()
