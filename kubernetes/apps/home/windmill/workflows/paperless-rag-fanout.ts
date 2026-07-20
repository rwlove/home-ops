// Cron: every 15 min.
//
// Unified paperless → RAG ingest. Pulls each modified Paperless document
// ONCE and fans it out to BOTH retrieval backends:
//   - Qdrant `paperless` vector store (Open WebUI Knowledge Base) — chunk +
//     bge-m3 embed + upsert. Synchronous, cheap, robust.
//   - LightRAG graph RAG — fire-and-forget POST of the OCR text; LightRAG
//     runs its own chunk/embed/LLM entity+relationship extraction on the
//     Spark. LLM-heavy, so capped + paced by MAX_DOCS_PER_RUN.
//
// Replaces the two independent scripts (paperless-rag-ingest +
// lightrag-rag-ingest): one Paperless fetch, one watermark, one cron —
// instead of two of each over the same corpus.
//
// Watermark: STATELESS, derived from Qdrant `max(last_modified)`. Qdrant is
// the synchronous, reliable sink, so its high-water mark is authoritative
// for "processed up to here". No Windmill state ⇒ no schedule-vs-trigger
// state divergence (the failure mode the old lightrag-rag-ingest floor
// guarded against simply cannot occur here). Per-doc order is LightRAG
// FIRST, then Qdrant: a doc only enters the Qdrant watermark after its
// LightRAG submit, so a crash mid-run never advances past a doc LightRAG
// didn't see.
//
// `since` arg: manual override to reprocess from an earlier point (testing
// or a targeted backfill). The scheduled run passes nothing and uses the
// Qdrant-derived watermark.
//
// Identity in LightRAG: file_source "paperless:<id>" surfaces as file_path
// in doc status — the stable key for clean updates AND the tombstone sweeps
// (paperless-rag-tombstone + lightrag-rag-tombstone, both still separate).

import * as wmill from "npm:windmill-client@1.527.0";

const QDRANT = Deno.env.get("QDRANT_URL") ??
    "http://qdrant.databases.svc.cluster.local:6333";
const OLLAMA = Deno.env.get("OLLAMA_URL") ??
    "http://ollama-spark.ai.svc.cluster.local:11434";
const PAPERLESS = Deno.env.get("PAPERLESS_URL") ??
    "http://paperless.collab.svc.cluster.local:8000";
const LIGHTRAG = Deno.env.get("LIGHTRAG_URL") ??
    "http://lightrag.ai.svc.cluster.local:9621";
const EMBED_MODEL = Deno.env.get("EMBED_MODEL") ?? "bge-m3";
const COLLECTION = "paperless";
const EMBED_DIM = 1024;
const CHUNK_CHARS = 1500;
const CHUNK_OVERLAP = 200;
const PAGE_SIZE = 50;
const MAX_DOCS_PER_RUN = 20; // pace LightRAG's LLM-heavy extraction
const MIN_TEXT_LEN = 50;
const QD_MAX_DOC_CHARS = 200_000; // Qdrant: cap embedding cost
const LR_MAX_DOC_CHARS = 500_000; // LightRAG: chunks internally, higher cap
const SOURCE_PREFIX = "paperless:";
const EPOCH = "1970-01-01T00:00:00Z";

type PaperlessDoc = {
    id: number;
    title: string;
    content: string;
    modified: string;
    correspondent: { id: number; name: string } | null;
    tags: { id: number; name: string }[];
};
type PaperlessList = { count: number; next: string | null; results: PaperlessDoc[] };
type QdrantPoint = { id: number; vector: number[]; payload: Record<string, unknown> };
type DocStatus = { id: string; file_path: string };
type DocsStatusesResponse = { statuses: Record<string, DocStatus[]> };
type PipelineStatus = { busy?: boolean; request_pending?: boolean; destructive_busy?: boolean };

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function main(since?: string) {
    const token = Deno.env.get("PAPERLESS_TOKEN");
    if (!token) throw new Error("PAPERLESS_TOKEN env not set");
    const apiKey = Deno.env.get("LIGHTRAG_API_KEY");
    if (!apiKey) throw new Error("LIGHTRAG_API_KEY env not set");

    const started = new Date().toISOString();
    await ensureCollection();
    const watermark = since ?? await readWatermark();
    console.log(`[fanout] watermark=${watermark}${since ? " (since-override)" : ""}`);

    // Lazy fetch: pull the first candidate page BEFORE building the O(corpus)
    // LightRAG source map. Idle runs (nothing modified past the watermark)
    // return here — the common steady-state case.
    const firstPage = await paperlessList(token, watermark, 1);
    if (firstPage.results.length === 0) {
        console.log(`[fanout] no changes past watermark — nothing to do`);
        return {
            started, finished: new Date().toISOString(),
            watermark_in: watermark, watermark_out: watermark,
            lr_submitted: 0, lr_replaced: 0, lr_already: 0, lr_errors: 0,
            qd_docs: 0, qd_chunks: 0, skipped: 0, capped: false,
        };
    }

    const existing = await lightragSourceMap(apiKey); // "paperless:<id>" -> [docId]
    console.log(`[fanout] known lightrag sources=${existing.size}`);

    let lr_submitted = 0, lr_replaced = 0, lr_already = 0, lr_errors = 0;
    let qd_docs = 0, qd_chunks = 0, skipped = 0;
    let processed = 0;
    let maxModified = watermark;
    let page = 1;
    let capped = false;

    let list = firstPage;
    outer:
    while (true) {
        for (const doc of list.results) {
            if (processed >= MAX_DOCS_PER_RUN) { capped = true; break outer; }
            const raw = (doc.content ?? "").trim();
            const src = `${SOURCE_PREFIX}${doc.id}`;
            if (raw.length < MIN_TEXT_LEN) {
                skipped++;
                if (doc.modified > maxModified) maxModified = doc.modified;
                continue;
            }

            // --- LightRAG (graph) FIRST. A LightRAG hiccup must not block the
            // Qdrant path (the OWUI-facing sink) nor abort the whole run, so
            // errors are caught + counted; the doc still lands in Qdrant and
            // its next Paperless modification (or a `since` re-run) retries
            // LightRAG. ---
            try {
                const outcome = await submitToLightrag(apiKey, raw, src, existing.get(src));
                if (outcome === "submitted") lr_submitted++;
                else if (outcome === "replaced") lr_replaced++;
                else lr_already++;
            } catch (e) {
                lr_errors++;
                console.warn(`[fanout] lightrag ${src} error: ${e instanceof Error ? e.message : e}`);
            }

            // --- Qdrant (vector). Synchronous; defines the watermark. ---
            qd_chunks += await ingestQdrant(doc);
            qd_docs++;

            processed++;
            if (doc.modified > maxModified) maxModified = doc.modified;
        }
        if (!list.next) break;
        page += 1;
        list = await paperlessList(token, watermark, page);
        if (list.results.length === 0) break;
    }

    return {
        started, finished: new Date().toISOString(),
        watermark_in: watermark, watermark_out: maxModified,
        lr_submitted, lr_replaced, lr_already, lr_errors,
        qd_docs, qd_chunks, skipped, capped,
    };
}

// ---------------------------------------------------------------------------
// LightRAG (graph RAG)
// ---------------------------------------------------------------------------

// Refresh-or-insert one Paperless doc into LightRAG. LightRAG has no upsert:
// /documents/text 409s if the source exists, and delete is an ASYNC pipeline
// task refused while the pipeline is busy. So on a doc already present, bail
// if busy (leave the old copy; a future run refreshes it), else delete and
// confirm removal before re-inserting.
async function submitToLightrag(
    apiKey: string, raw: string, src: string, prior: string[] | undefined,
): Promise<"submitted" | "replaced" | "already"> {
    const text = raw.slice(0, LR_MAX_DOC_CHARS);
    let replaced = false;
    if (prior?.length) {
        if (await pipelineBusy(apiKey)) return "already";
        await lightragDelete(apiKey, prior);
        if (!(await waitSourceAbsent(apiKey, src, 30_000))) return "already";
        replaced = true;
    }
    const inserted = await lightragInsert(apiKey, text, src);
    if (!inserted) return "already"; // 409 backstop: already present
    return replaced ? "replaced" : "submitted";
}

async function lightragSourceMap(apiKey: string): Promise<Map<string, string[]>> {
    const r = await fetch(`${LIGHTRAG}/documents`, {
        headers: { "X-API-Key": apiKey },
        signal: AbortSignal.timeout(60_000),
    });
    if (!r.ok) throw new Error(`lightrag GET /documents ${r.status}: ${await r.text()}`);
    const body = (await r.json()) as DocsStatusesResponse;
    const map = new Map<string, string[]>();
    for (const docs of Object.values(body.statuses ?? {})) {
        for (const d of docs) {
            if (!d.file_path?.startsWith(SOURCE_PREFIX)) continue;
            const arr = map.get(d.file_path) ?? [];
            arr.push(d.id);
            map.set(d.file_path, arr);
        }
    }
    return map;
}

async function pipelineBusy(apiKey: string): Promise<boolean> {
    const r = await fetch(`${LIGHTRAG}/documents/pipeline_status`, {
        headers: { "X-API-Key": apiKey },
        signal: AbortSignal.timeout(30_000),
    });
    if (!r.ok) return true; // unknown → treat as busy; skip the refresh this run
    const s = (await r.json()) as PipelineStatus;
    return Boolean(s.busy || s.request_pending || s.destructive_busy);
}

async function waitSourceAbsent(apiKey: string, source: string, timeoutMs: number): Promise<boolean> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        const map = await lightragSourceMap(apiKey);
        if (!map.get(source)?.length) return true;
        await sleep(2_000);
    }
    return false;
}

async function lightragInsert(apiKey: string, text: string, source: string): Promise<boolean> {
    const r = await fetch(`${LIGHTRAG}/documents/text`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
        body: JSON.stringify({ text, file_source: source }),
        signal: AbortSignal.timeout(60_000),
    });
    if (r.status === 409) return false; // already present (no upsert)
    if (!r.ok) throw new Error(`lightrag insert ${source} ${r.status}: ${await r.text()}`);
    return true;
}

async function lightragDelete(apiKey: string, docIds: string[]): Promise<void> {
    const r = await fetch(`${LIGHTRAG}/documents/delete_document`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
        body: JSON.stringify({ doc_ids: docIds, delete_llm_cache: true }),
        signal: AbortSignal.timeout(120_000),
    });
    if (!r.ok && r.status !== 404) { // 404 = already gone; tolerate
        throw new Error(`lightrag delete ${JSON.stringify(docIds)} ${r.status}: ${await r.text()}`);
    }
}

// ---------------------------------------------------------------------------
// Qdrant (vector RAG)
// ---------------------------------------------------------------------------

async function ingestQdrant(doc: PaperlessDoc): Promise<number> {
    const text = (doc.content ?? "").slice(0, QD_MAX_DOC_CHARS).trim();
    if (text.length < MIN_TEXT_LEN) return 0;
    const chunks = chunk(text, CHUNK_CHARS, CHUNK_OVERLAP);
    await deleteDocPoints(doc.id);
    const points: QdrantPoint[] = [];
    for (let i = 0; i < chunks.length; i++) {
        const vector = await embed(chunks[i]);
        points.push({
            id: pointId(doc.id, i),
            vector,
            payload: {
                paperless_id: doc.id,
                title: doc.title,
                correspondent: doc.correspondent?.name ?? null,
                tags: (doc.tags ?? []).map((t) => t.name),
                last_modified: doc.modified,
                chunk_index: i,
                chunk_total: chunks.length,
                content: chunks[i],
            },
        });
    }
    await upsert(points);
    return points.length;
}

async function ensureCollection(): Promise<void> {
    const r = await fetch(`${QDRANT}/collections/${COLLECTION}`);
    if (r.status === 200) return;
    if (r.status !== 404) throw new Error(`qdrant probe ${r.status}: ${await r.text()}`);
    const create = await fetch(`${QDRANT}/collections/${COLLECTION}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vectors: { size: EMBED_DIM, distance: "Cosine" } }),
    });
    if (!create.ok) throw new Error(`qdrant create ${create.status}: ${await create.text()}`);
    const idx = await fetch(`${QDRANT}/collections/${COLLECTION}/index?wait=true`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ field_name: "paperless_id", field_schema: "integer" }),
    });
    if (!idx.ok) {
        console.warn(`qdrant payload-index paperless_id failed ${idx.status}: ${await idx.text()}`);
    }
    console.log(`[fanout] created collection ${COLLECTION} dim=${EMBED_DIM}`);
}

// Watermark: max last_modified across the live Qdrant collection. Stateless —
// re-derived each run. Small enough for a single scroll (target ~1k chunks
// for ~200 docs); switch to a payload-indexed range query past ~10k.
async function readWatermark(): Promise<string> {
    let max = EPOCH;
    let offset: unknown = undefined;
    let pages = 0;
    while (true) {
        const body: Record<string, unknown> = {
            limit: 10_000, with_payload: ["last_modified"], with_vector: false,
        };
        if (offset !== undefined && offset !== null) body.offset = offset;
        const r = await fetch(`${QDRANT}/collections/${COLLECTION}/points/scroll`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
            signal: AbortSignal.timeout(60_000),
        });
        if (!r.ok) return EPOCH;
        const j = await r.json() as {
            result: { points: { payload: { last_modified: string } }[]; next_page_offset: unknown };
        };
        for (const p of j.result.points) {
            const lm = p.payload?.last_modified ?? "";
            if (lm > max) max = lm;
        }
        offset = j.result.next_page_offset;
        pages++;
        if (offset === null || offset === undefined || pages > 100) break;
    }
    return max;
}

async function deleteDocPoints(paperless_id: number): Promise<void> {
    const r = await fetch(`${QDRANT}/collections/${COLLECTION}/points/delete?wait=true`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            filter: { must: [{ key: "paperless_id", match: { value: paperless_id } }] },
        }),
    });
    if (!r.ok && r.status !== 404) {
        throw new Error(`qdrant delete-by-filter ${r.status}: ${await r.text()}`);
    }
}

// Stable integer point IDs from paperless_id × chunk_index (Qdrant u64).
function pointId(paperless_id: number, chunk_index: number): number {
    return paperless_id * 1024 + chunk_index;
}

function chunk(text: string, size: number, overlap: number): string[] {
    if (text.length <= size) return [text];
    const out: string[] = [];
    let pos = 0;
    while (pos < text.length) {
        const end = Math.min(pos + size, text.length);
        out.push(text.slice(pos, end));
        if (end === text.length) break;
        pos = end - overlap;
    }
    return out;
}

async function embed(text: string): Promise<number[]> {
    const r = await fetch(`${OLLAMA}/api/embed`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: EMBED_MODEL, input: text }),
        signal: AbortSignal.timeout(120_000),
    });
    if (!r.ok) throw new Error(`ollama embed ${r.status}: ${await r.text()}`);
    const body = await r.json() as { embeddings: number[][] };
    return body.embeddings[0];
}

async function upsert(points: QdrantPoint[]): Promise<void> {
    if (!points.length) return;
    const r = await fetch(`${QDRANT}/collections/${COLLECTION}/points?wait=true`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ points }),
    });
    if (!r.ok) throw new Error(`qdrant upsert ${r.status}: ${await r.text()}`);
}

async function paperlessList(token: string, modified_gt: string, page: number): Promise<PaperlessList> {
    const url = new URL(`${PAPERLESS}/api/documents/`);
    url.searchParams.set("page", String(page));
    url.searchParams.set("page_size", String(PAGE_SIZE));
    url.searchParams.set("ordering", "modified");
    url.searchParams.set("modified__gt", modified_gt);
    const r = await fetch(url, {
        headers: { Authorization: `Token ${token}` },
        signal: AbortSignal.timeout(60_000),
    });
    if (!r.ok) throw new Error(`paperless list ${r.status}: ${await r.text()}`);
    return (await r.json()) as PaperlessList;
}
