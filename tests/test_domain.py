import pytest
from valkiria.domain.models import UserStory, AcceptanceCriterion, TestMatrix

def story(): return UserStory(title='Login seguro',description='Como usuario quiero iniciar sesión',acceptance_criteria=[AcceptanceCriterion(id='AC1',text='Dado usuario válido, entonces accede')])
def test_story_is_ai_draft(): assert story().generated_by_ai and story().status.value=='draft'
def test_test_matrix_limit():
    with pytest.raises(Exception): TestMatrix(story_id=story().id,cases=[{'id':str(i),'criterion_id':'AC1','scenario':'x','expected_result':'x','type':'positive'} for i in range(31)])
