from __future__ import annotations

from app import docgen


def test_parse_llm_mapping_handles_code_block():
    payload = "```json\n{\"class:Foo\": \"Doc text\"}\n```"
    parsed, error = docgen._parse_llm_mapping(payload)
    assert error is None
    assert parsed == {"class:Foo": "Doc text"}


def test_generate_llm_docs_surfaces_errors(monkeypatch):
    files_data = {
        "foo.py": {
            "text": "def foo():\n    pass",
            "language": "python",
            "decls": [{"type": "def", "name": "foo", "lineno": 1, "end_lineno": 2}],
            "existing_docs": {},
        }
    }

    def fake_call(*_args, **_kwargs):
        return {"status": "error", "error": "no quota"}

    monkeypatch.setattr(docgen.langraph, "call_groq_api", fake_call)
    docs, errors = docgen._generate_llm_docs_for_missing(files_data, use_llm=True)
    assert docs == {}
    assert errors and "foo.py" in errors[0]

