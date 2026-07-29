from pathlib import Path

import pytest

# The example run shipped with screenshot_generator; used as the
# canonical fixture for bundle/planner tests.
EXAMPLE_RUN = Path(__file__).resolve().parents[2] / "runs" / "20260728_163942"


@pytest.fixture(scope="session")
def example_run_dir() -> Path:
    if not EXAMPLE_RUN.exists():
        pytest.skip(f"example run not present: {EXAMPLE_RUN}")
    return EXAMPLE_RUN


@pytest.fixture(scope="session")
def bundle(example_run_dir):
    from deck_builder.run_bundle import load_run_bundle

    return load_run_bundle(example_run_dir)
