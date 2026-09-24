"""B17 public retirement must fail closed without rewriting historical state."""
import pytest
from sqlalchemy import func, select

from app.catalog.seed import load_seed
from app.models import Project, RequirementsProfile
from tests.test_ci import _make_project, _mint_token, _register


@pytest.mark.parametrize('path,payload', [
    ('/recommendations', {'taskTypes': ['ci_review'], 'budgetSensitivity': 'high'}),
    ('/recommendations/prefill', {'text': 'cheap review'}),
    ('/projects', {'name': 'old', 'selectedOptionId': 1, 'baselineModelId': 1}),
])
def test_retired_creation_is_authenticated_and_writes_nothing(client, db_session, path, payload):
    load_seed(db_session)
    headers, _ = _register(client, db_session, 'retirement@example.com')
    before = [db_session.scalar(select(func.count()).select_from(m))
              for m in (Project, RequirementsProfile)]
    assert client.post(path, json=payload).status_code == 401
    result = client.post(path, headers=headers, json=payload)
    assert result.status_code == 410
    assert '/execution/v1' in result.json()['detail']
    assert [db_session.scalar(select(func.count()).select_from(m))
            for m in (Project, RequirementsProfile)] == before


@pytest.mark.parametrize('change', [
    {'selectedOptionId': 1}, {'baselineModelId': 1}, {'taskType': 'ci_review'},
    {'baselineModelId': 1, 'name': 'must not partly rename'},
])
def test_retired_repick_preserves_project_token_and_old_config(client, db_session, change):
    load_seed(db_session)
    headers, _ = _register(client, db_session, 'old-project@example.com')
    pid = _make_project(client, headers)
    token = _mint_token(client, headers, pid)
    before = client.get(f'/projects/{pid}', headers=headers).json()
    config_before = client.get(f'/projects/{pid}/agent-config', headers={'X-CI-Token': token})
    response = client.patch(f'/projects/{pid}', headers=headers, json=change)
    assert response.status_code == 410
    assert client.get(f'/projects/{pid}', headers=headers).json() == before
    config_after = client.get(f'/projects/{pid}/agent-config', headers={'X-CI-Token': token})
    assert config_after.status_code == config_before.status_code == 200
    assert config_after.json() == config_before.json()
    assert client.patch(f'/projects/{pid}', headers=headers,
                        json={'name': 'Renamed', 'reviewPreferences': 'Focus on bugs'}).status_code == 200


def test_chat_prompt_cannot_claim_savings_or_verified_suitability():
    from app.chat.prompts import ANSWER_GEN
    assert 'Never claim measured' in ANSWER_GEN.system
    assert 'not recall or coverage' in ANSWER_GEN.system
    assert 'v3' == ANSWER_GEN.version


@pytest.fixture
def evidence(db_session):
    from tests.test_selections import evidence as fixture
    return fixture.__wrapped__(db_session)


def test_explicit_chat_keeps_history_without_legacy_grounding(client, db_session, evidence):
    from app.api.chat import get_chat_llm_client
    from app.config import get_settings
    from app.models import ChatMessage, User
    from tests.test_selections import create, pick
    from unittest.mock import Mock

    headers, uid = _register(client, db_session, 'explicit-chat@example.com')
    user = db_session.get(User, uid)
    user.is_operator = True
    db_session.commit()
    settings = get_settings()
    before = settings.chat_enabled
    settings.chat_enabled = True
    fake = Mock()
    client.app.dependency_overrides[get_chat_llm_client] = lambda: fake
    try:
        project = create(client, headers, pick(*evidence[0]))
        pid = project['id']
        db_session.add(ChatMessage(project_id=pid, role='assistant', text='Historical conversation'))
        db_session.commit()
        result = client.get(f'/projects/{pid}/chat', headers=headers)
        assert [m['text'] for m in result.json()['messages']] == ['Historical conversation']
        answer = client.post(f'/projects/{pid}/chat', headers=headers, json={'question': 'Any savings?'})
        assert answer.status_code == 200 and 'offline' in answer.json()['answer']
        fake.complete.assert_not_called()
        assert db_session.scalar(select(func.count()).select_from(ChatMessage)) == 1
    finally:
        settings.chat_enabled = before
        client.app.dependency_overrides.pop(get_chat_llm_client, None)
