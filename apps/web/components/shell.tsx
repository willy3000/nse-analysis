"use client";

import {
  Activity, Bell, Briefcase, Database, FlaskConical, Gauge, LayoutDashboard, List, LogOut, Moon,
  Newspaper, ScanSearch, Settings, Sparkles, Sun, TrendingUp, Wallet, Zap,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { Badge, cn, Select, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useApi, useDataUpdates, useMe, useUniverse } from "@/lib/hooks";
import { applyTheme, ensureThemeApplied, type Theme } from "@/lib/theme";
import type { DataHealth } from "@/lib/types";

export const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/scanner", label: "Market Scanner", icon: ScanSearch },
  { href: "/opportunities", label: "Opportunities", icon: Sparkles },
  { href: "/surges", label: "Surge Radar", icon: Zap },
  { href: "/trends", label: "Trends", icon: TrendingUp },
  { href: "/instruments", label: "Instruments", icon: List },
  { href: "/fundamentals", label: "Fundamentals", icon: Gauge },
  { href: "/strategy-lab", label: "Strategy Lab", icon: FlaskConical },
  { href: "/portfolio", label: "Paper Portfolio", icon: Wallet },
  { href: "/watchlists", label: "Watchlists", icon: Briefcase },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/news", label: "News & Events", icon: Newspaper },
  { href: "/data", label: "Data Centre", icon: Database },
  { href: "/settings", label: "Settings", icon: Settings },
] as const;

interface Status { is_open: boolean; reason: string; local_time: string; last_completed_session: string }
interface Universes { default: "real" | "synthetic"; real_instruments: number; synthetic_instruments: number }

function useTheme(): [Theme, () => void] {
  // Starts at the default rather than at "unknown": the pre-paint script has already applied the real
  // theme, and the effect below corrects the icon on mount if the stored choice was light.
  const [theme, setTheme] = useState<Theme>("dark");
  // `ensureThemeApplied` rather than a plain read: the class the pre-paint script added sits on an <html>
  // the server rendered without one, so hydration can drop it and leave the page light while storage says
  // dark. This reconciles the document with the stored choice and reports what it settled on.
  useEffect(() => setTheme(ensureThemeApplied()), []);
  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    setTheme(next);
  };
  return [theme, toggle];
}

function SearchBox() {
  const router = useRouter();
  const ref = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) {
        e.preventDefault();
        ref.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <form role="search" onSubmit={(e) => { e.preventDefault(); if (q.trim()) router.push(`/instruments?q=${encodeURIComponent(q.trim())}`); }}>
      <input ref={ref} value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search instruments"
        placeholder="Search symbol or name   ( / )"
        className="h-7 w-56 rounded border border-line bg-bg px-2 text-xs placeholder:text-muted/70 lg:w-72" />
    </form>
  );
}

const HEALTH_TONE = { COMPLETE: "up", PARTIAL: "warn", DEGRADED: "warn", UNAVAILABLE: "down" } as const;

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const qc = useQueryClient();
  const me = useMe();
  const [universe, setUniverse] = useUniverse();
  const [theme, toggleTheme] = useTheme();
  const authed = me.isSuccess;
  const status = useApi<Status>(authed ? "/market/status" : null, undefined, { refetchInterval: 60_000 });
  const health = useApi<DataHealth>(authed ? "/data/health" : null, undefined, { refetchInterval: 120_000 });
  const unis = useApi<Universes>(authed ? "/market/universes" : null);
  const notes = useApi<{ unread: number }>(authed ? "/notifications" : null, { unread_only: true, limit: 1 },
    { refetchInterval: 60_000 });
  useDataUpdates();

  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [me.error, router, pathname]);

  if (me.isLoading) return <Spinner label="Loading session" />;
  if (!authed) return <Spinner label="Redirecting to sign in" />;

  const effective = universe ?? unis.data?.default ?? "real";
  const synthetic = effective === "synthetic";

  async function logout() {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    qc.clear();
    router.replace("/login");
  }

  return (
    <div className="flex min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-panel focus:p-2">Skip to content</a>
      <nav aria-label="Primary" className="sticky top-0 hidden h-screen w-52 shrink-0 flex-col border-r border-line bg-panel md:flex">
        <div className="flex items-center gap-2 border-b border-line px-3 py-3">
          <Activity className="h-5 w-5 text-accent" aria-hidden />
          <div className="leading-tight">
            <div className="text-sm font-bold tracking-tight">NSE Intelligence</div>
            <div className="text-2xs text-muted">Nairobi Securities Exchange</div>
          </div>
        </div>
        <ul className="flex-1 space-y-px overflow-y-auto p-2">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <li key={href}>
                <Link href={href} aria-current={active ? "page" : undefined}
                  className={cn("flex items-center gap-2 rounded px-2 py-1.5 text-sm",
                    active ? "bg-raised font-semibold text-fg" : "text-muted hover:bg-raised hover:text-fg")}>
                  <Icon className="h-4 w-4" aria-hidden /> {label}
                </Link>
              </li>
            );
          })}
        </ul>
        <div className="border-t border-line p-2 text-2xs text-muted">
          Decision support only. No trades are executed.
        </div>
      </nav>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex flex-wrap items-center gap-2 border-b border-line bg-panel/95 px-3 py-1.5 backdrop-blur">
          <select aria-label="Navigate" className="h-7 rounded border border-line bg-bg px-1 text-xs md:hidden"
            value={NAV.find((n) => (n.href === "/" ? pathname === "/" : pathname.startsWith(n.href)))?.href ?? "/"}
            onChange={(e) => router.push(e.target.value)}>
            {NAV.map((n) => <option key={n.href} value={n.href}>{n.label}</option>)}
          </select>
          <SearchBox />
          <div className="flex items-center gap-1.5 text-xs">
            {status.data && (
              <Badge tone={status.data.is_open ? "up" : "neutral"} title={`Local time ${status.data.local_time}`}>
                NSE {status.data.is_open ? "open" : `closed · ${status.data.reason.replace(/_/g, " ").toLowerCase()}`}
              </Badge>
            )}
            {health.data && (
              <Link href="/data"><Badge tone={HEALTH_TONE[health.data.overall]} title={health.data.message}>
                Data: {health.data.overall.toLowerCase()}
                {health.data.best_available_latency && ` · ${health.data.best_available_latency.replace(/_/g, " ").toLowerCase()}`}
              </Badge></Link>
            )}
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Select aria-label="Data universe" className="h-7 text-xs" value={universe ?? ""}
              onChange={(e) => { setUniverse((e.target.value || null) as "real" | "synthetic" | null); qc.invalidateQueries(); }}>
              <option value="">Universe: auto ({unis.data?.default ?? "…"})</option>
              <option value="real">Real market data ({unis.data?.real_instruments ?? 0})</option>
              <option value="synthetic">Synthetic dev data ({unis.data?.synthetic_instruments ?? 0})</option>
            </Select>
            <Link href="/alerts" className="relative rounded p-1 hover:bg-raised" aria-label={`Notifications: ${notes.data?.unread ?? 0} unread`}>
              <Bell className="h-4 w-4" aria-hidden />
              {(notes.data?.unread ?? 0) > 0 && (
                <span className="absolute -right-0.5 -top-0.5 rounded-full bg-down px-1 text-[9px] font-bold text-white">{notes.data?.unread}</span>
              )}
            </Link>
            <button onClick={toggleTheme} className="rounded p-1 hover:bg-raised" aria-label="Toggle colour theme">
              {theme === "dark" ? <Sun className="h-4 w-4" aria-hidden /> : <Moon className="h-4 w-4" aria-hidden />}
            </button>
            <span className="hidden text-2xs text-muted lg:inline">{me.data?.email}</span>
            <button onClick={logout} className="rounded p-1 hover:bg-raised" aria-label="Sign out"><LogOut className="h-4 w-4" aria-hidden /></button>
          </div>
        </header>
        {synthetic && (
          <div role="status" className="border-b border-warn/40 bg-warn/10 px-3 py-1 text-center text-xs font-semibold text-warn">
            SYNTHETIC DEVELOPMENT DATA — fictitious instruments, not market data. Configure a licensed provider in the Data Centre for real NSE data.
          </div>
        )}
        {!synthetic && health.data?.overall === "UNAVAILABLE" && (
          <div role="status" className="border-b border-down/40 bg-down/10 px-3 py-1 text-center text-xs font-semibold text-down">
            No real market data loaded. {health.data.message}
          </div>
        )}
        <main id="main" className="min-w-0 flex-1 p-3">{children}</main>
      </div>
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode;
  actions?: ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {description && <p className="text-xs text-muted">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

