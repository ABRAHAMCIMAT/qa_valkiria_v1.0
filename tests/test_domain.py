import pytest
from pydantic import ValidationError

from valkiria.domain.models import AcceptanceCriterion, TestMatrix, UserStory


def story():
    return UserStory(title="Login seguro", description="Como usuario quiero iniciar sesión", acceptance_criteria=[AcceptanceCriterion(id="AC1", text="Dado usuario válido, entonces accede")])


def test_story_is_ai_draft():
    assert story().generated_by_ai and story().status.value == "draft"


def test_test_matrix_limit():
    cases = [{"id": str(i), "criterion_id": "AC1", "scenario": "x", "expected_result": "x", "type": "positive"} for i in range(31)]
    with pytest.raises(ValidationError):
        TestMatrix(story_id=story().id, cases=cases)
