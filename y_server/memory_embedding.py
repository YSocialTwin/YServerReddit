import math
import re
import threading


class MemoryEmbeddingService:
    """
    Ollama-backed embedding wrapper for memory embeddings.

    The service intentionally degrades gracefully: if the model cannot be loaded,
    callers receive None embeddings and can use lexical fallback retrieval.
    """

    def __init__(self, model_name: str | None = None, ollama_host: str | None = None):
        self.model_name = str(model_name or "").strip()
        self.requested_model_name = self.model_name
        self.ollama_host = str(ollama_host or "").strip()
        self._client = None
        self._available = None
        self._lock = threading.Lock()
        self._last_error = None
        if not self.model_name or not self.ollama_host:
            self._available = False
            self._last_error = "embedding service not configured"

    def _installed_model_names(self, client):
        try:
            listing = client.list()
        except Exception:
            return []

        models = getattr(listing, "models", None)
        if models is None and isinstance(listing, dict):
            models = listing.get("models")
        if not isinstance(models, list):
            return []

        names = []
        for model in models:
            name = getattr(model, "model", None)
            if name is None and isinstance(model, dict):
                name = model.get("model") or model.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
        return names

    def _resolve_model_name(self, client):
        installed = self._installed_model_names(client)
        if not installed:
            return self.model_name

        lowered = {name.lower(): name for name in installed}
        candidates = []
        for candidate in [
            self.requested_model_name,
            self.model_name,
            "embeddinggemma",
            "snowflake-arctic-embed:110m",
            "snowflake-arctic-embed",
            "nomic-embed-text",
            "mxbai-embed-large",
        ]:
            if isinstance(candidate, str) and candidate.strip():
                candidates.append(candidate.strip())

        for candidate in candidates:
            exact = lowered.get(candidate.lower())
            if exact:
                return exact
            prefix = f"{candidate.lower()}:"
            for lowered_name, original_name in lowered.items():
                if lowered_name.startswith(prefix):
                    return original_name

        for name in installed:
            lowered_name = name.lower()
            if "embed" in lowered_name:
                return name
        return self.model_name

    def _ensure_client(self):
        if self._available is False:
            return None
        if self._client is not None:
            return self._client

        with self._lock:
            if self._client is not None:
                return self._client
            if self._available is False:
                return None
            try:
                from ollama import Client as OllamaClient
            except Exception as exc:
                self._available = False
                self._last_error = f"ollama import failed: {exc}"
                return None

            try:
                client = OllamaClient(host=self.ollama_host)
                resolved_model = self._resolve_model_name(client)
                if resolved_model != self.model_name:
                    self.model_name = resolved_model
                # Verify connectivity with a test embed call
                client.embed(model=self.model_name, input="test")
                self._client = client
                self._available = True
            except Exception as exc:
                self._available = False
                self._last_error = f"ollama connect/embed failed ({self.model_name}): {exc}"
                self._client = None
            return self._client

    def embed_text(self, text: str):
        if not isinstance(text, str) or not text.strip():
            return None
        client = self._ensure_client()
        if client is None:
            return None
        try:
            r = client.embed(model=self.model_name, input=text.strip())
            return [float(x) for x in r.embeddings[0]]
        except Exception:
            return None

    def embed_texts(self, texts):
        if not isinstance(texts, list) or not texts:
            return []
        client = self._ensure_client()
        if client is None:
            return [None for _ in texts]
        clean = [str(t).strip() if isinstance(t, str) else "" for t in texts]
        try:
            r = client.embed(model=self.model_name, input=clean)
        except Exception:
            return [None for _ in texts]
        out = []
        for row in r.embeddings:
            try:
                out.append([float(x) for x in row])
            except Exception:
                out.append(None)
        return out

    @property
    def available(self):
        if self._available is None:
            # Trigger lazy status resolution.
            self._ensure_client()
        return bool(self._available)

    @property
    def last_error(self):
        return self._last_error


def cosine_similarity(vec_a, vec_b) -> float:
    if not isinstance(vec_a, list) or not isinstance(vec_b, list):
        return 0.0
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for a, b in zip(vec_a, vec_b):
        try:
            fa = float(a)
            fb = float(b)
        except Exception:
            continue
        dot += fa * fb
        na += fa * fa
        nb += fb * fb
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return float(dot / (math.sqrt(na) * math.sqrt(nb)))


def lexical_relevance(query_text: str, memory_text: str) -> float:
    if not isinstance(query_text, str) or not isinstance(memory_text, str):
        return 0.0
    q_tokens = set(re.findall(r"[a-z0-9]+", query_text.lower()))
    m_tokens = set(re.findall(r"[a-z0-9]+", memory_text.lower()))
    if not q_tokens or not m_tokens:
        return 0.0
    inter = len(q_tokens & m_tokens)
    if inter <= 0:
        return 0.0
    return float(inter / math.sqrt(len(q_tokens) * len(m_tokens)))
