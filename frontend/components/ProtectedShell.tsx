"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { ApiError, getMe } from "@/lib/api";
import { clearTokens, getAccessToken } from "@/lib/auth";
import type { CurrentUser, UserRole } from "@/lib/types";

const ROLE_RANK: Record<string, number> = {
  clinician: 2,
  manager: 3,
  admin: 4,
};

interface ProtectedShellProps {
  title: string;
  children: React.ReactNode;
  allowedRoles?: UserRole[];
}

function NavLink({ href, label, active }: { href: string; label: string; active: boolean }) {
  return (
    <Link
      href={href}
      className="btn"
      style={{
        background: active ? "#ffedd5" : "white",
        borderColor: active ? "#fdba74" : "#cbd5e1",
        fontWeight: 600,
      }}
    >
      {label}
    </Link>
  );
}

export default function ProtectedShell({ title, children, allowedRoles }: ProtectedShellProps) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = getAccessToken();
    if (!token) {
      router.replace("/login");
      return;
    }

    void (async () => {
      try {
        const me = await getMe();
        setUser(me);
      } catch (err) {
        if (err instanceof ApiError) {
          setError(err.message);
        } else {
          setError("Cannot load current user");
        }
        clearTokens();
        router.replace("/login");
      } finally {
        setLoading(false);
      }
    })();
  }, [router]);

  const isAllowed = useMemo(() => {
    if (!user || !allowedRoles || allowedRoles.length === 0) {
      return true;
    }
    return allowedRoles.includes(user.role);
  }, [allowedRoles, user]);

  if (loading) {
    return (
      <main className="page-shell">
        <div className="card" style={{ padding: 24 }}>
          Loading...
        </div>
      </main>
    );
  }

  return (
    <main className="page-shell">
      <div className="card" style={{ padding: 16, marginBottom: 12 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 10,
          }}
        >
          <div>
            <div className="heading" style={{ fontSize: 26, fontWeight: 700 }}>
              {title}
            </div>
            <div className="muted">
              {user?.email} - {user?.role} ({user?.department})
            </div>
          </div>
          <button
            className="btn"
            onClick={() => {
              clearTokens();
              router.replace("/login");
            }}
          >
            Logout
          </button>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 14 }}>
          <NavLink href="/chat" label="Chat" active={pathname?.startsWith("/chat") ?? false} />
          {(ROLE_RANK[user?.role ?? "clinician"] ?? 0) >= ROLE_RANK.manager && (
            <>
              <NavLink href="/admin/gaps" label="Gaps" active={pathname?.startsWith("/admin/gaps") ?? false} />
              <NavLink href="/admin/audit" label="Audit" active={pathname?.startsWith("/admin/audit") ?? false} />
            </>
          )}
          {user?.role === "admin" && (
            <NavLink href="/admin/index" label="Index" active={pathname?.startsWith("/admin/index") ?? false} />
          )}
        </div>
      </div>
      {error && (
        <div className="card" style={{ padding: 12, borderColor: "#fecaca", background: "#fef2f2", marginBottom: 12 }}>
          {error}
        </div>
      )}
      {!isAllowed ? (
        <div className="card" style={{ padding: 16 }}>
          You do not have permission to access this page.
        </div>
      ) : (
        children
      )}
    </main>
  );
}
