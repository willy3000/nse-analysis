"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";

import { Button, ErrorState, Field, Input, Tabs } from "@/components/ui";
import { api } from "@/lib/api";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`/auth/${mode}`, { method: "POST", body: { email, password } });
      await qc.invalidateQueries({ queryKey: ["me"] });
      const next = params.get("next");
      router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-sm space-y-4 rounded-md border border-line bg-panel p-6">
        <div className="flex items-center gap-2">
          <Activity className="h-6 w-6 text-accent" aria-hidden />
          <div>
            <h1 className="text-base font-bold">NSE Intelligence</h1>
            <p className="text-2xs text-muted">Research terminal for the Nairobi Securities Exchange</p>
          </div>
        </div>
        <Tabs value={mode} onChange={setMode} options={[{ value: "login", label: "Sign in" },
          { value: "register", label: "Create account" }]} />
        <form onSubmit={submit} className="space-y-3">
          <Field label="Email"><Input type="email" autoComplete="email" required value={email}
            onChange={(e) => setEmail(e.target.value)} /></Field>
          <Field label="Password" hint={mode === "register" ? "At least 10 characters, mixed case and a digit." : undefined}>
            <Input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required
              value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          {error ? <ErrorState error={error} /> : null}
          <Button type="submit" variant="primary" className="w-full" loading={busy}>
            {mode === "login" ? "Sign in" : "Create account"}
          </Button>
        </form>
        <p className="text-2xs leading-relaxed text-muted">
          Decision-support and research only. This platform never places orders. Analytics describe historical data
          and do not guarantee future outcomes.
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
