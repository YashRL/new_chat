from types import SimpleNamespace

import embedding_client


def test_create_embeddings_uses_openai_model(monkeypatch):
    calls = {}

    class FakeEmbeddingsAPI:
        def create(self, input, model):
            calls["input"] = input
            calls["model"] = model
            return SimpleNamespace(
                data=[
                    SimpleNamespace(embedding=[0.1, 0.2]),
                    SimpleNamespace(embedding=[0.3, 0.4]),
                ]
            )

    class FakeClient:
        def __init__(self):
            self.embeddings = FakeEmbeddingsAPI()

    monkeypatch.setattr(embedding_client, "_openai_client_cache", FakeClient())
    monkeypatch.setattr(embedding_client, "OPENAI_MODEL", "text-embedding-3-small")

    vectors = embedding_client.create_embeddings(["a", "b"])

    assert calls["input"] == ["a", "b"]
    assert calls["model"] == "text-embedding-3-small"
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]


def test_create_single_embedding_returns_first_vector(monkeypatch):
    monkeypatch.setattr(
        embedding_client,
        "create_embeddings",
        lambda texts, input_type="query": [[0.9, 0.8]],
    )

    assert embedding_client.create_single_embedding("hello") == [0.9, 0.8]
