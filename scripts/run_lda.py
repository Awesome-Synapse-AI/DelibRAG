import json
from pathlib import Path
from typing import List

from sklearn.decomposition import LatentDirichletAllocation
from sklearn.feature_extraction.text import CountVectorizer
import joblib


def build_scope_manifest(documents: List[str], n_topics: int = 20):
    vectorizer = CountVectorizer(max_df=0.95, min_df=2, stop_words="english")
    X = vectorizer.fit_transform(documents)
    lda = LatentDirichletAllocation(n_components=n_topics, random_state=42)
    lda.fit(X)

    topic_keywords = []
    for topic_idx, topic in enumerate(lda.components_):
        top_words = [
            vectorizer.get_feature_names_out()[i] for i in topic.argsort()[:-11:-1]
        ]
        topic_keywords.append(
            {"topic_id": topic_idx, "keywords": top_words, "label": f"topic_{topic_idx}"}
        )

    manifest = {"topics": topic_keywords, "n_topics": n_topics}
    with open("scope_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    joblib.dump(lda, "lda_model.pkl")
    joblib.dump(vectorizer, "lda_vectorizer.pkl")
    return manifest


if __name__ == "__main__":
    sample_docs_path = Path("sample_docs.txt")
    if not sample_docs_path.exists():
        raise SystemExit("Provide documents list or sample_docs.txt to build manifest.")

    docs = sample_docs_path.read_text(encoding="utf-8").splitlines()
    build_scope_manifest(docs)
