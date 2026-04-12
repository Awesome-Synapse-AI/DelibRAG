"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError, register } from "@/lib/api";
import { setTokens } from "@/lib/auth";
import type { UserRole } from "@/lib/types";

const ROLE_OPTIONS: UserRole[] = ["clinician", "manager", "admin"];

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState<UserRole>("clinician");
  const [department, setDepartment] = useState("general");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const tokens = await register({
        email,
        password,
        full_name: fullName,
        role,
        department,
      });
      setTokens(tokens);
      router.replace("/chat");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="page-shell" style={{ display: "grid", placeItems: "center" }}>
      <div className="card" style={{ width: "100%", maxWidth: 520, padding: 22 }}>
        <h1 className="heading" style={{ marginTop: 0, marginBottom: 8 }}>
          Create Account
        </h1>
        <p className="muted" style={{ marginTop: 0 }}>
          Register and receive JWT tokens immediately.
        </p>
        <form onSubmit={onSubmit} style={{ display: "grid", gap: 12 }}>
          <input
            className="input"
            type="email"
            placeholder="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <input
            className="input"
            type="password"
            placeholder="password (min 8 chars)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
          />
          <input
            className="input"
            type="text"
            placeholder="full name"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
          />
          <div className="grid-two">
            <select className="select" value={role} onChange={(e) => setRole(e.target.value)}>
              {ROLE_OPTIONS.map((roleOption) => (
                <option key={roleOption} value={roleOption}>
                  {roleOption}
                </option>
              ))}
            </select>
            <input
              className="input"
              type="text"
              placeholder="department"
              value={department}
              onChange={(e) => setDepartment(e.target.value)}
              required
            />
          </div>
          {error && <div className="banner banner-warning">{error}</div>}
          <button className="btn btn-primary" type="submit" disabled={loading}>
            {loading ? "Creating..." : "Create account"}
          </button>
        </form>
        <p className="muted" style={{ marginBottom: 0, marginTop: 14 }}>
          Have an account? <Link href="/login">Login</Link>
        </p>
      </div>
    </main>
  );
}
