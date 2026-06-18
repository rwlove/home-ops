// Cron: daily.
//
// Deletes LightRAG docs whose Paperless source no longer exists — the
// graph-RAG mirror of paperless-rag-tombstone (which does this for Qdrant).
// The ingest flow keys every doc on file_source "paperless:<id>"; this
// sweep removes any LightRAG doc whose <id> is gone from Paperless.

const LIGHTRAG = Deno.env.get("LIGHTRAG_URL") ??
    "http://lightrag.ai.svc.cluster.local:9621";
const PAPERLESS = Deno.env.get("PAPERLESS_URL") ??
    "http://paperless.collab.svc.cluster.local:8000";
const PAGE_SIZE = 100;
const SOURCE_PREFIX = "paperless:";
const DELETE_BATCH = 20;

type DocStatus = { id: string; file_path: string };
type DocsStatusesResponse = { statuses: Record<string, DocStatus[]> };

export async function main() {
    const token = Deno.env.get("PAPERLESS_TOKEN");
    if (!token) throw new Error("PAPERLESS_TOKEN env not set");
    const apiKey = Deno.env.get("LIGHTRAG_API_KEY");
    if (!apiKey) throw new Error("LIGHTRAG_API_KEY env not set");

    const started = new Date().toISOString();
    const liveIds = await paperlessIds(token);            // Set<number> currently in Paperless
    const indexed = await lightragPaperlessDocs(apiKey);  // [{ paperlessId, docId }]

    const stale = indexed.filter((d) => !liveIds.has(d.paperlessId));
    const docIds = stale.map((d) => d.docId);

    let deleted = 0;
    for (let i = 0; i < docIds.length; i += DELETE_BATCH) {
        const batch = docIds.slice(i, i + DELETE_BATCH);
        await lightragDelete(apiKey, batch);
        deleted += batch.length;
    }

    return {
        started,
        finished: new Date().toISOString(),
        paperless_live: liveIds.size,
        lightrag_indexed: indexed.length,
        deleted_stale: deleted,
    };
}

async function paperlessIds(token: string): Promise<Set<number>> {
    const ids = new Set<number>();
    let page = 1;
    while (true) {
        const url = new URL(`${PAPERLESS}/api/documents/`);
        url.searchParams.set("page", String(page));
        url.searchParams.set("page_size", String(PAGE_SIZE));
        url.searchParams.set("ordering", "id");
        const r = await fetch(url, {
            headers: { Authorization: `Token ${token}` },
            signal: AbortSignal.timeout(60_000),
        });
        if (!r.ok) throw new Error(`paperless list ${r.status}: ${await r.text()}`);
        const body = (await r.json()) as { next: string | null; results: { id: number }[] };
        for (const d of body.results) ids.add(d.id);
        if (!body.next) break;
        page += 1;
    }
    return ids;
}

async function lightragPaperlessDocs(apiKey: string): Promise<{ paperlessId: number; docId: string }[]> {
    const r = await fetch(`${LIGHTRAG}/documents`, {
        headers: { "X-API-Key": apiKey },
        signal: AbortSignal.timeout(60_000),
    });
    if (!r.ok) throw new Error(`lightrag GET /documents ${r.status}: ${await r.text()}`);
    const body = (await r.json()) as DocsStatusesResponse;
    const out: { paperlessId: number; docId: string }[] = [];
    for (const docs of Object.values(body.statuses ?? {})) {
        for (const d of docs) {
            if (!d.file_path?.startsWith(SOURCE_PREFIX)) continue;
            const pid = Number(d.file_path.slice(SOURCE_PREFIX.length));
            if (Number.isFinite(pid)) out.push({ paperlessId: pid, docId: d.id });
        }
    }
    return out;
}

async function lightragDelete(apiKey: string, docIds: string[]): Promise<void> {
    if (docIds.length === 0) return;
    const r = await fetch(`${LIGHTRAG}/documents/delete_document`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
        body: JSON.stringify({ doc_ids: docIds, delete_llm_cache: true }),
        signal: AbortSignal.timeout(120_000),
    });
    if (!r.ok && r.status !== 404) {
        throw new Error(`lightrag delete ${JSON.stringify(docIds)} ${r.status}: ${await r.text()}`);
    }
}
