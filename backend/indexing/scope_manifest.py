import json
from pathlib import Path
from typing import Any, Dict, Optional

import joblib

DEFAULT_MANIFEST_PATH = Path("scope_manifest.json")
DEFAULT_LDA_MODEL_PATH = Path("lda_model.pkl")
DEFAULT_VECTORIZER_PATH = Path("lda_vectorizer.pkl")


def load_scope_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {"topics": [], "n_topics": 0}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_lda_artifacts(
    model_path: Path = DEFAULT_LDA_MODEL_PATH, vectorizer_path: Path = DEFAULT_VECTORIZER_PATH
):
    model = joblib.load(model_path) if model_path.exists() else None
    vectorizer = joblib.load(vectorizer_path) if vectorizer_path.exists() else None
    return model, vectorizer


def classify_scope(text: str, model=None, vectorizer=None) -> Optional[int]:
    if model is None or vectorizer is None:
        return None
    X = vectorizer.transform([text])
    topic_distribution = model.transform(X)
    return int(topic_distribution.argmax())
