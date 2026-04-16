"use client";

import { useState } from "react";

import ProtectedShell from "@/components/ProtectedShell";
import { ApiError, listSources, triggerIndexing, triggerReindex, updateTrustScore } from "@/lib/api";

export default function IndexManagementPage() {
  const [file, setFile] = useState<File | null>(null);
  const [sourceId, setSourceId] = useState("");
  const [trustScore, setTrustScore] = useState("1.0");
  const [sourcesPayload, setSourcesPayload] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function runAction(action: () => Promise<unknown>) {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const payload = await action();
      setResult(JSON.stringify(payload, null, 2));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <ProtectedShell title="Index Management" allowedRoles={["admin"]}>
      <div style={{ display: "flex", flexDirection: "column", gap: 12, height: "100%", overflow: "auto" }}>
        <div className="grid-two">
        <section className="card" style={{ padding: 14 }}>
          <div className="heading" style={{ fontWeight: 700, marginBottom: 8 }}>
            File Upload + Index
          </div>
          <input className="input" type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <button
            className="btn btn-primary"
            style={{ marginTop: 8 }}
            disabled={loading}
            onClick={() =>
              void runAction(async () => {
                const form = new FormData();
                if (file) {
                  form.append("file", file);
                }
                return triggerIndexing(form);
              })
            }
          >
            Trigger /indexing/index
          </button>
          <p className="muted" style={{ fontSize: 12 }}>
            If backend endpoint is not implemented, this will return a 501 message.
          </p>
        </section>

        <section className="card" style={{ padding: 14 }}>
          <div className="heading" style={{ fontWeight: 700, marginBottom: 8 }}>
            Full Reindex
          </div>
          <button className="btn btn-primary" disabled={loading} onClick={() => void runAction(() => triggerReindex())}>
            Trigger /indexing/reindex
          </button>
        </section>
      </div>

      <div className="grid-two">
        <section className="card" style={{ padding: 14 }}>
          <div className="heading" style={{ fontWeight: 700, marginBottom: 8 }}>
            Source Trust Override
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            <input
              className="input"
              placeholder="source_id"
              value={sourceId}
              onChange={(e) => setSourceId(e.target.value)}
            />
            <input
              className="input"
              type="number"
              min="0"
              max="2"
              step="0.01"
              placeholder="trust score"
              value={trustScore}
              onChange={(e) => setTrustScore(e.target.value)}
            />
            <button
              className="btn"
              disabled={loading}
              onClick={() => void runAction(() => updateTrustScore(sourceId, Number(trustScore)))}
            >
              PATCH /indexing/trust/{sourceId}
            </button>
          </div>
        </section>

        <section className="card" style={{ padding: 14 }}>
          <div className="heading" style={{ fontWeight: 700, marginBottom: 8 }}>
            Sources
          </div>
          <button
            className="btn"
            disabled={loading}
            onClick={() =>
              void runAction(async () => {
                const payload = await listSources();
                setSourcesPayload(JSON.stringify(payload, null, 2));
                return payload;
              })
            }
          >
            GET /indexing/sources
          </button>
          {sourcesPayload && <pre style={{ marginTop: 10, fontSize: 12, overflowX: "auto" }}>{sourcesPayload}</pre>}
        </section>
      </div>

      {error && (
        <div className="card" style={{ borderColor: "#fecaca", background: "#fef2f2", padding: 12, flexShrink: 0 }}>
          {error}
        </div>
      )}
      {result && (
        <div className="card" style={{ padding: 12, flexShrink: 0 }}>
          <div className="heading" style={{ fontWeight: 700, marginBottom: 6 }}>
            Response
          </div>
          <pre style={{ margin: 0, fontSize: 12, overflowX: "auto" }}>{result}</pre>
        </div>
      )}
      </div>
    </ProtectedShell>
  );
}
