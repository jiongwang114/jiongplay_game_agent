"""ChromaDB + sentence-transformers vector store for semantic game search."""

import os
from typing import Optional

import chromadb
from sentence_transformers import SentenceTransformer


class VectorStore:
    """
    Local vector database powered by ChromaDB and sentence-transformers.

    Each game is represented by the concatenation of its *name*,
    *description*, and *tags*, embedded with a local model (no API cost).
    """

    def __init__(
        self,
        vector_path: Optional[str] = None,
        embed_model: Optional[str] = None,
    ):
        vector_path = vector_path or os.getenv("VECTOR_PATH", "./data/vector_index")
        embed_model = embed_model or os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")

        os.makedirs(vector_path, exist_ok=True)

        # Load local embedding model
        self._model = SentenceTransformer(embed_model)

        # Persistent ChromaDB client
        self._client = chromadb.PersistentClient(path=vector_path)
        self._collection = self._client.get_or_create_collection("games")

    # ------------------------------------------------------------------
    #  Embedding helpers
    # ------------------------------------------------------------------

    def embed(self, text: str) -> list[float]:
        """Return the embedding vector for a single text string."""
        return self._model.encode(text).tolist()

    def _game_text(self, game) -> str:
        """Build the text blob used for embedding from a Game ORM object or dict."""
        if isinstance(game, dict):
            return f"{game.get('name','')} {game.get('description','')} {game.get('tags','')}"
        return f"{game.name} {game.description} {game.tags}"

    # ------------------------------------------------------------------
    #  Collection operations
    # ------------------------------------------------------------------

    def add_games(self, games: list) -> None:
        """
        Batch-upsert games into the Chroma collection.

        *games* may be a list of ORM ``Game`` objects or dicts — both
        must include ``steam_appid``, ``name``, ``description``, and ``tags``.
        """
        if not games:
            return

        ids = []
        embeddings = []
        documents = []
        metadatas = []

        for game in games:
            appid = str(game.steam_appid if hasattr(game, "steam_appid") else game["steam_appid"])
            text = self._game_text(game)
            ids.append(appid)
            documents.append(text)
            embeddings.append(self.embed(text))
            metadatas.append({"steam_appid": int(appid)})

        # Chroma upsert — overwrites if id already exists
        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    def search(self, query: str, top_k: Optional[int] = None) -> list[int]:
        """
        Semantic search — returns a list of ``steam_appid`` values ranked
        by cosine similarity.
        """
        top_k = top_k or int(os.getenv("TOP_K", "5"))
        query_embedding = self.embed(query)

        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
        )

        # results["metadatas"] is list[list[dict]] — the inner list is per query
        metadatas = results.get("metadatas", [[]])[0]
        return [int(m["steam_appid"]) for m in metadatas if m is not None]
