import type { AgnesResponse, Daily, IntegrationStatus, Project, RecordMap, ReviewCandidate, ReviewDetail, Session, Source, Todo, Knowledge, Activity, LunaDraft } from "../types";
import { mapCapture, mapDraft, mapKnowledge, mapProject, mapReview, mapReviewDetail, mapSource, mapTodo } from "../types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string) { super(message); this.name = "ApiError"; }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (["POST", "PATCH", "DELETE", "PUT"].includes(method) && !path.startsWith("/v1/session/login")) headers.set("X-PAW-CSRF", "1");
  const response = await fetch(path, { ...init, headers, credentials: "include" });
  const body = await response.json().catch(() => null) as RecordMap | null;
  if (!response.ok) {
    const detail = body?.detail;
    const detailObject = typeof detail === "object" && detail !== null ? detail as RecordMap : null;
    const message = detailObject ? String(detailObject.message ?? detailObject.detail ?? "请求失败，请稍后重试。") : String(detail ?? "请求失败，请稍后重试。");
    const code = detailObject ? String(detailObject.code ?? "") : undefined;
    if (response.status === 401) window.dispatchEvent(new Event("paw:unauthorized"));
    throw new ApiError(response.status, message, code);
  }
  return body as T;
}

const query = (params: Record<string, string | number | boolean | undefined>) => {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => value !== undefined && value !== "" && search.set(key, String(value)));
  const encoded = search.toString(); return encoded ? `?${encoded}` : "";
};

export class PawClient {
  private captureRequests = new Map<string, Promise<unknown>>();

  session() { return request<Session>("/v1/session"); }
  // v1.3C read-only projections (authority-aware, evidence-traceable).
  projectProjection(id: string) { return request<RecordMap>(`/v1/web/workspace/projection/project/${encodeURIComponent(id)}`); }
  knowledgeProjection(id: string) { return request<RecordMap>(`/v1/web/workspace/projection/knowledge/${encodeURIComponent(id)}`); }
  knowledgeSearch(q: string, includeProvisional = false) { return request<RecordMap>(`/v1/web/workspace/projection/knowledge-search${query({ q, limit: 20, include_provisional: includeProvisional })}`); }
  evidenceLookup(sourceVersionId: string, locator: string) { return request<RecordMap>(`/v1/web/workspace/projection/evidence${query({ source_version_id: sourceVersionId, locator })}`); }
  login(username: string, password: string) { return request<Session>("/v1/session/login", { method: "POST", body: JSON.stringify({ username, password }) }); }
  logout() { return request<{ authenticated: false }>("/v1/session/logout", { method: "POST" }); }
  status() { return request<IntegrationStatus>("/v1/web/status"); }
  saveCapture(rawText: string, idempotencyKey: string) {
    const cached = this.captureRequests.get(idempotencyKey); if (cached) return cached;
    const task = request("/v1/web/workspace/captures", { method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify({ raw_text: rawText, source: "web" }) });
    this.captureRequests.set(idempotencyKey, task); task.catch(() => this.captureRequests.delete(idempotencyKey)); return task;
  }
  async today() {
    const raw = await request<RecordMap>("/v1/web/workspace/today");
    return { date: String(raw.date ?? ""), captures: Array.isArray(raw.captures) ? raw.captures.map(mapCapture) : [], todos: Array.isArray(raw.todos) ? raw.todos.map(mapTodo) : [], daily: raw.daily };
  }
  async todos(params: Record<string, string | number | boolean | undefined> = {}) { const raw = await request<{items?: RecordMap[]}>(`/v1/web/workspace/todos${query(params)}`); return (raw.items ?? []).map(mapTodo); }
  async setTodoDone(id: string, done: boolean) { return request(`/v1/web/workspace/todos/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ status: done ? "done" : "open" }) }); }
  async projects() { const raw = await request<{items?: RecordMap[]}>("/v1/web/workspace/projects"); return (raw.items ?? []).map(mapProject); }
  async project(id: string) {
    try {
      const raw = await request<RecordMap>(`/v1/web/workspace/projects/${encodeURIComponent(id)}/context`);
      return { ...raw, project: mapProject({ ...(raw.project as RecordMap), explainer: raw.explainer ?? raw.project_explainer ?? (raw.project as RecordMap | undefined)?.explainer }), todos: Array.isArray(raw.todos) ? raw.todos.map(mapTodo) : [], decisions: raw.decisions, pending: raw.pending, knowledge: raw.knowledge, sources: raw.sources };
    } catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; }
  }
  async knowledge() { const raw = await request<{items?: RecordMap[]}>("/v1/web/workspace/knowledge?limit=1000"); return (raw.items ?? []).map(mapKnowledge); }
  async knowledgeItem(id: string) {
    try { const raw = await request<RecordMap>(`/v1/web/workspace/knowledge/${encodeURIComponent(id)}`); return { ...raw, page: mapKnowledge(raw.page as RecordMap), latest_revision: raw.latest_revision, sources: raw.sources, projects: raw.projects }; }
    catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; }
  }
  async drafts(params: Record<string, string | number | boolean | undefined> = {}) {
    const raw = await request<{items?: RecordMap[]; total?: number}>(`/v1/web/workspace/drafts${query(params)}`);
    return { items: (raw.items ?? []).map(mapDraft), total: raw.total ?? (raw.items ?? []).length };
  }
  async draft(id: string) {
    try {
      const raw = await request<RecordMap>(`/v1/web/workspace/drafts/${encodeURIComponent(id)}`);
      return mapDraft((raw.draft as RecordMap) ?? raw);
    } catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; }
  }
  async sources() { const raw = await request<{items?: RecordMap[]}>("/v1/web/workspace/sources?limit=500"); return (raw.items ?? []).map(mapSource); }
  async source(id: string) {
    try {
      const source = mapSource(await request<RecordMap>(`/v1/web/workspace/sources/${encodeURIComponent(id)}`));
      if (!source.wpsDocumentId) return source;
      try { const preview = await request<RecordMap>("/v1/web/workspace/wps/preview", { method: "POST", body: JSON.stringify({ document_id: source.wpsDocumentId, max_chars: 20000 }) }); return { ...source, content: typeof preview.content === "string" ? preview.content : null }; }
      catch { return source; }
    } catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; }
  }
  async daily(date?: string): Promise<Daily> { const raw = await request<RecordMap>(`/v1/web/workspace/daily${date ? `?date=${encodeURIComponent(date)}` : ""}`); return { date: String(raw.date ?? date ?? ""), daily: raw.daily as RecordMap | null, readOnly: true, captures: Array.isArray(raw.captures) ? raw.captures.map(mapCapture) : [] }; }
  calendar(month: string) { return request<{ month: string; days: Record<string, number> }>(`/v1/web/workspace/daily/calendar?month=${encodeURIComponent(month)}`); }
  searchCaptures(q: string) { return request<{query: string; total: number; next_cursor?: string | null; items?: RecordMap[]}>(`/v1/web/workspace/captures/search${query({ q, limit: 20 })}`).then(raw => ({ query: raw.query, total: raw.total, nextCursor: raw.next_cursor ?? null, items: (raw.items ?? []).map(item => ({ id: String(item.capture_id ?? ""), date: String(item.date ?? ""), capturedAt: String(item.captured_at ?? ""), snippet: String(item.snippet ?? "") })) })); }
  async reviewCandidates(status: "pending" | "accepted" | "rejected" = "pending") { const raw = await request<{items?: RecordMap[]}>(`/v1/web/brain/review/candidates${query({ status, limit: 50 })}`); return (raw.items ?? []).map(mapReview); }
  async reviewCandidate(id: string) { return mapReviewDetail(await request<RecordMap>(`/v1/web/brain/review/candidates/${encodeURIComponent(id)}`)); }
  acceptCandidate(id: string, reviewNotes?: string) {
    return request<RecordMap>(`/v1/web/brain/review/candidates/${encodeURIComponent(id)}/accept`, {
      method: "POST",
      body: JSON.stringify(reviewNotes ? { review_notes: reviewNotes } : {}),
    });
  }
  rejectCandidate(id: string) { return request<RecordMap>(`/v1/web/brain/review/candidates/${encodeURIComponent(id)}/reject`, { method: "POST" }); }
  applyCandidate(id: string) { return request<RecordMap>(`/v1/web/brain/review/candidates/${encodeURIComponent(id)}/apply`, { method: "POST" }); }
  editCandidate(id: string, payload: RecordMap) { return request<RecordMap>(`/v1/web/brain/review/candidates/${encodeURIComponent(id)}/edit`, { method: "POST", body: JSON.stringify({ edited_payload: payload }) }); }
  clarifyCandidate(id: string, response: string) { return request<RecordMap>(`/v1/web/brain/review/candidates/${encodeURIComponent(id)}/clarify`, { method: "POST", body: JSON.stringify({ user_response: response }) }); }
  ignoreCandidate(id: string) { return request<RecordMap>(`/v1/web/brain/review/candidates/${encodeURIComponent(id)}/ignore`, { method: "POST" }); }
  resolveConflict(id: string, option: "A" | "B" | "NEITHER", response?: string) { return request<RecordMap>(`/v1/web/brain/review/candidates/${encodeURIComponent(id)}/conflict`, { method: "POST", body: JSON.stringify({ selected_option: option, user_response: response || undefined }) }); }
  activities() { return request<{items?: Activity[]}>("/v1/web/brain/activity?limit=20"); }
  async createAgnesChat() { return request<{session_id: string; mode: string}>("/v1/web/agy/chat", { method: "POST", body: JSON.stringify({ mode: "chat" }) }); }
  sendAgnesMessage(sessionId: string, message: string, clientMessageId?: string) { return request<AgnesResponse>(`/v1/web/agy/chat/${encodeURIComponent(sessionId)}/messages`, { method: "POST", body: JSON.stringify(clientMessageId ? { message, client_message_id: clientMessageId } : { message }) }); }
  async listAgnesChats() { const raw = await request<{sessions?: RecordMap[]}>("/v1/web/agy/chats"); return (raw.sessions ?? []).map(item => ({ session_id: String(item.session_id ?? ""), title: String(item.title ?? ""), project_id: item.project_id ? String(item.project_id) : null, created_at: String(item.created_at ?? ""), updated_at: String(item.updated_at ?? ""), message_count: Number(item.message_count ?? 0) })); }
  async getAgnesChat(sessionId: string) {
    const raw = await request<RecordMap>(`/v1/web/agy/chat/${encodeURIComponent(sessionId)}`);
    const messages = Array.isArray(raw.messages) ? (raw.messages as RecordMap[]).map(m => ({ message_id: String(m.message_id ?? ""), role: String(m.role ?? ""), content: String(m.content ?? ""), created_at: String(m.created_at ?? ""), status: String(m.status ?? "success") })) : [];
    return { session_id: String(raw.session_id ?? ""), title: String(raw.title ?? ""), project_id: raw.project_id ? String(raw.project_id) : null, created_at: String(raw.created_at ?? ""), updated_at: String(raw.updated_at ?? ""), message_count: Number(raw.message_count ?? 0), history_available: Boolean(raw.history_available), messages };
  }
}

export const client = new PawClient();
