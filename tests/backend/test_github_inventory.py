import pytest
from cli.github_inventory import redact, safe_path, validate


def test_inventory_skips_credentials_and_generated_directories():
    for path in ['.env','app/.env.production','credentials.json','node_modules/a/index.ts','.local/facts.json','key.pem']:
        assert not safe_path(path)
    assert safe_path('src/app.py')
    assert 'super-private' not in redact('API_KEY=super-private\nprint("ok")')
    assert 'print("ok")' in redact('API_KEY=super-private\nprint("ok")')


def test_model_inventory_requires_citations_from_actual_files():
    result={'summary':'Project','technologies':['Python'],'evidence_refs':['real'],
            'application_draft':'Implemented service','needs_confirmation':['Production usage']}
    assert validate(result,['real'])==result
    with pytest.raises(ValueError):validate(dict(result,evidence_refs=['invented']),['real'])
    with pytest.raises(ValueError):validate(dict(result,evidence_refs=[]),['real'])
