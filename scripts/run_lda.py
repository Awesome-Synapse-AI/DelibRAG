from pathlib import Path
import json, joblib
from sklearn.decomposition import LatentDirichletAllocation
from sklearn.feature_extraction.text import CountVectorizer

pairs = {
    "manager": [
        "../sample-docs/low-stake-manager-doc.md",
        "../sample-docs/high-stake-manager-doc.md",
    ],
    "clinical": [
        "../sample-docs/low-stake-clinical-doc.md",
        "../sample-docs/high-stake-clinical-doc.md",
    ],
}

out_root = Path("lda_domains")
out_root.mkdir(exist_ok=True)

for domain, files in pairs.items():
    texts = []
    for f in files:
        p = Path(f)
        if not p.exists():
            print(f"Missing {p}, skipping its content")
            continue
        txt = p.read_text(encoding="utf-8").strip()
        if txt:
            texts.append(txt)
    if not texts:
        print(f"No text for domain {domain}; skipping.")
        continue

    n_topics = 5  # small corpus; tweak if you want fewer/more topics
    vectorizer = CountVectorizer(max_df=0.95, min_df=1, stop_words="english")
    X = vectorizer.fit_transform(texts)
    lda = LatentDirichletAllocation(n_components=n_topics, random_state=42)
    lda.fit(X)

    vocab = vectorizer.get_feature_names_out()
    topics = []
    for topic_id, comp in enumerate(lda.components_):
        top_words = [vocab[i] for i in comp.argsort()[:-11:-1]]
        topics.append({"topic_id": topic_id, "keywords": top_words, "label": f"{domain}_topic_{topic_id}"})

    out_dir = out_root / domain
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scope_manifest.json").write_text(
        json.dumps({"topics": topics, "n_topics": n_topics, "domain": domain, "source_files": files}, indent=2),
        encoding="utf-8",
    )
    joblib.dump(lda, out_dir / "lda_model.pkl")
    joblib.dump(vectorizer, out_dir / "lda_vectorizer.pkl")
    print(f"Built {domain} LDA with {len(texts)} docs -> {n_topics} topics")
