"use client";

import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type Query } from "@/lib/api";
import type { Universe, User } from "@/lib/types";

export function useApi<T>(path: string | null, query?: Query, opts?: Partial<UseQueryOptions<T>>) {
  return useQuery<T>({
    queryKey: [path, query],
    queryFn: () => api<T>(path as string, { query }),
    enabled: path !== null,
    ...opts,
  });
}

export function useMe() {
  return useQuery<User>({ queryKey: ["me"], queryFn: () => api<User>("/auth/me"), retry: false, staleTime: 60_000 });
}

/** Mutation that invalidates the given query prefixes on success. */
export function useApiMutation<TBody, TRes = unknown>(
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  path: string | ((b: TBody) => string),
  invalidate: string[] = [],
) {
  const qc = useQueryClient();
  return useMutation<TRes, Error, TBody>({
    mutationFn: (body: TBody) =>
      api<TRes>(typeof path === "function" ? path(body) : path, {
        method,
        body: method === "DELETE" ? undefined : body,
      }),
    onSuccess: () => {
      for (const key of invalidate) qc.invalidateQueries({ predicate: (q) => String(q.queryKey[0] ?? "").startsWith(key) });
    },
  });
}

const UNIVERSE_KEY = "nse.universe";

/** Selected data universe (real vs synthetic). Stored per browser; null = server default. */
export function useUniverse(): [Universe | null, (u: Universe | null) => void] {
  const [u, setU] = useState<Universe | null>(null);
  useEffect(() => {
    try {
      const v = window.localStorage.getItem(UNIVERSE_KEY);
      if (v === "real" || v === "synthetic") setU(v);
    } catch {
      /* storage unavailable: use server default */
    }
    const onChange = () => {
      try {
        const v = window.localStorage.getItem(UNIVERSE_KEY);
        setU(v === "real" || v === "synthetic" ? v : null);
      } catch {
        setU(null);
      }
    };
    window.addEventListener("nse-universe", onChange);
    return () => window.removeEventListener("nse-universe", onChange);
  }, []);
  const set = (v: Universe | null) => {
    try {
      if (v) window.localStorage.setItem(UNIVERSE_KEY, v);
      else window.localStorage.removeItem(UNIVERSE_KEY);
    } catch {
      /* ignore */
    }
    setU(v);
    window.dispatchEvent(new Event("nse-universe"));
  };
  return [u, set];
}

/** Subscribes to the backend SSE stream and refetches queries when new data has been ingested. */
export function useDataUpdates() {
  const qc = useQueryClient();
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const es = new EventSource("/api/v1/stream/updates", { withCredentials: true });
    es.addEventListener("data_updated", () => qc.invalidateQueries());
    return () => es.close();
  }, [qc]);
}
