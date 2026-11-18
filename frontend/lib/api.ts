export type Incident = {
  id: number; repo: string; run_id: number; workflow: string; branch: string; status: string;
  category: string | null; root_cause: string | null; confidence: number | null;
  patch_source: string | null; llm_provider: string | null; pr_url: string | null; error: string | null;
  created_at: string; updated_at: string; has_patch: boolean;
};
export type Edit = { path: string; search: string; replace: string };
export type IncidentDetail = Incident & {
  logs: string; patch: { edits: Edit[]; explanation?: string } | null;
  sandbox_result: { passed: boolean; command: string; output: string } | null;
  audit: { actor: string; action: string; detail: string; at: string }[];
};
export type Stats = {
  total: number; by_status: Record<string, number>; by_category: Record<string, number>;
  recovery_rate: number | null; memory_entries: number;
};
export type MemoryEntry = { signature: string; category: string; root_cause: string; successes: number; failures: number; last_seen: string };

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/backend/${path}`, { ...init, cache: "no-store" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${res.status})`);
  }
  return res.json();
}
