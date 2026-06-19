// Cron: every 15 min.
//
// Streams modified Paperless documents into Qdrant `paperless` so the
// Open WebUI Knowledge Base can retrieve against them. Idempotent —
// safe to re-run with the same modified__gt cutoff.
//
// Sequencing per doc:
//   1. fetch full content from Paperless
//   2. chunk to ~1500 chars / ~200-char overlap (rough 350-tok target,
//      fits bge-m3's 8192-token ceiling with a wide margin)
//   3. delete any pre-existing chunks for this paperless_id (so
//      updates replace cleanly — point IDs derive from paperless_id +
//      chunk_index, but we still wipe by filter to handle docs whose
//      chunk count shrinks)
//   4. embed each chunk via Spark Ollama bge-m3 (1024-dim)
//   5. upsert all chunks at once with full payload
//
// Watermark: read max(modified) across the live collection. If the
// collection is empty, start from epoch (initial backfill). Save the
// max(modified) observed in this run for the next iteration; we don't
// persist it externally — re-deriving from Qdrant on each run keeps
// the function stateless.
//
// Collection schema (lazy-created on first run):
//   vectors:  size=1024 distance=Cosine
//   payload:  paperless_id, title, correspondent, tags, last_modified,
//             chunk_index, chunk_total, content

type PaperlessDoc = {
    id: number;
    title: string;
    content: string;
    modified: string;
    correspondent: { id: number; name: string } | null;
    tags: { id: number; name: string }[];
};

type PaperlessList = {
    count: number;
    next: string | null;
    results: PaperlessDoc[];
};

type QdrantPoint = {
    id: number;
    vector: number[];
    payload: Record<string, unknown>;
};

const QDRANT = Deno.env.get("QDRANT_URL") ??
    "http://qdrant.databases.svc.cluster.local:6333";
const OLLAMA = Deno.env.get("OLLAMA_URL") ??
    "http://ollama-spark.ai.svc.cluster.local:11434";
const PAPERLESS = Deno.env.get("PAPERLESS_URL") ??
    "http://paperless.collab.svc.cluster.local:8000";
const EMBED_MODEL = Deno.env.get("EMBED_MODEL") ?? "bge-m3";
const COLLECTION = "paperless";
const EMBED_DIM = 1024;
const CHUNK_CHARS = 1500;
const CHUNK_OVERLAP = 200;
const PAGE_SIZE = 50;
const MAX_DOC_CHARS = 200_000; // safety: refuse runaway PDFs

export async function main() {
    const token = Deno.env.get("PAPERLESS_TOKEN");
    if (!token) throw new Error("PAPERLESS_TOKEN env not set");

    const started = new Date().toISOString();
    await ensureCollection();
    const watermark = await readWatermark();
    console.log(`[ingest] watermark=${watermark} collection=${COLLECTION}`);

    let touched = 0;
    let chunks_written = 0;
    let max_modified = watermark;
    let page = 1;

    while (true) {
        const list = await paperlessList(token, watermark, page);
        if (list.results.length === 0) break;
        for (const doc of list.results) {
            if (doc.modified > max_modified) max_modified = doc.modified;
            const written = await ingestDoc(doc);
            chunks_written += written;
            touched += 1;
        }
        if (!list.next) break;
        page += 1;
    }

    return {
        started,
        finished: new Date().toISOString(),
        watermark_in: watermark,
        watermark_out: max_modified,
        docs_touched: touched,
        chunks_written,
    };
}

async function ensureCollection(): Promise<void> {
    const r = await fetch(`${QDRANT}/collections/${COLLECTION}`);
    if (r.status === 200) return;
    if (r.status !== 404) {
        throw new Error(`qdrant probe ${r.status}: ${await r.text()}`);
    }
    const create = await fetch(`${QDRANT}/collections/${COLLECTION}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            vectors: { size: EMBED_DIM, distance: "Cosine" },
        }),
    });
    if (!create.ok) {
        throw new Error(`qdrant create ${create.status}: ${await create.text()}`);
    }
    // Payload index for tombstone filter + per-doc deletes.
    const idx = await fetch(
        `${QDRANT}/collections/${COLLECTION}/index?wait=true`,
        {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                field_name: "paperless_id",
                field_schema: "integer",
            }),
        },
    );
    if (!idx.ok) {
        console.warn(
            `qdrant payload-index paperless_id failed ${idx.status}: ${await idx.text()}`,
        );
    }
    console.log(`[ingest] created collection ${COLLECTION} dim=${EMBED_DIM}`);
}

async function readWatermark(): Promise<string> {
    // Scroll the collection for the max last_modified payload. The
    // collection is small enough (target ~1k chunks for 200 docs) that
    // a single scroll is fine; switch to a payload-indexed range query
    // later if it grows past ~10k.
    const r = await fetch(
        `${QDRANT}/collections/${COLLECTION}/points/scroll`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                limit: 10_000,
                with_payload: ["last_modified"],
                with_vector: false,
            }),
        },
    );
    if (!r.ok) return "1970-01-01T00:00:00Z";
    const body = await r.json() as { result: { points: { payload: { last_modified: string } }[] } };
    if (!body.result?.points?.length) return "1970-01-01T00:00:00Z";
    let max = "1970-01-01T00:00:00Z";
    for (const p of body.result.points) {
        const lm = p.payload?.last_modified ?? "";
        if (lm > max) max = lm;
    }
    return max;
}

async function paperlessList(
    token: string,
    modified_gt: string,
    page: number,
): Promise<PaperlessList> {
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
    return await r.json() as PaperlessList;
}

async function ingestDoc(doc: PaperlessDoc): Promise<number> {
    const text = (doc.content ?? "").slice(0, MAX_DOC_CHARS).trim();
    if (text.length < 50) {
        console.log(`[ingest] skip doc ${doc.id} (${JSON.stringify(doc.title)}) — too short`);
        return 0;
    }
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
    console.log(
        `[ingest] doc ${doc.id} (${JSON.stringify(doc.title)}) → ${points.length} chunks`,
    );
    return points.length;
}

// Stable integer point IDs derived from paperless_id × chunk_index.
// Qdrant accepts arbitrary u64; we encode (paperless_id * 1024 +
// chunk_index) so reads/deletes are deterministic without a hash.
function pointId(paperless_id: number, chunk_index: number): number {
    return paperless_id * 1024 + chunk_index;
}

async function deleteDocPoints(paperless_id: number): Promise<void> {
    const r = await fetch(
        `${QDRANT}/collections/${COLLECTION}/points/delete?wait=true`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                filter: {
                    must: [{
                        key: "paperless_id",
                        match: { value: paperless_id },
                    }],
                },
            }),
        },
    );
    if (!r.ok) {
        const t = await r.text();
        // 404 means no points matched — fine.
        if (r.status !== 404) {
            throw new Error(`qdrant delete-by-filter ${r.status}: ${t}`);
        }
    }
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
    const r = await fetch(
        `${QDRANT}/collections/${COLLECTION}/points?wait=true`,
        {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ points }),
        },
    );
    if (!r.ok) throw new Error(`qdrant upsert ${r.status}: ${await r.text()}`);
}
