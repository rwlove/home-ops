/* eslint-disable */
/**
 * Upload a PDF to paperless-ngx. Invoked by the "Scan to Paperless" pipeline
 * (config.local.js) — the scanservjs image ships no curl/wget, but node 20 has
 * global fetch/FormData/Blob, so the upload is done here.
 *
 * The API token is read from the mounted secret file (scanservjs-secret ->
 * /secrets/PAPERLESS_TOKEN); keeping it out of the pipeline command means it
 * never lands in scanservjs' info-level command logs. Usage:
 *   node upload-to-paperless.js <pdf-path>
 *
 * Failed scans are never dropped. scanservjs reuses fixed temp file names, so
 * a failed upload used to be overwritten by the next scan. Now a failed PDF is
 * kept as failed-<timestamp>.pdf in the output dir (a PVC, listed under
 * "Files" in the scanservjs UI), and every later run retries those first —
 * paperless's checksum duplicate check makes a retry of an already-filed scan
 * harmless. The script still exits non-zero on failure so the pipeline (and
 * the Home Assistant notification) reports the error.
 *
 * Successful scans are also moved into the output dir by scanservjs itself
 * (scan_*.pdf); those copies are pruned after RETAIN_DAYS.
 */
const fs = require('fs');
const path = require('path');

const file = process.argv[2] || 'paperless.pdf';
const endpoint =
  'http://paperless.collab.svc.cluster.local:8000/api/documents/post_document/';
const outputDir = '/var/lib/scanservjs/output';
const RETAIN_DAYS = 7;

let token = '';
try {
  token = fs.readFileSync('/secrets/PAPERLESS_TOKEN', 'utf8').trim();
} catch (e) {
  /* file absent until the 1Password field exists */
}

async function upload(pdf, name) {
  if (!token) throw new Error('no paperless token at /secrets/PAPERLESS_TOKEN');
  const form = new FormData();
  form.append('document', new Blob([fs.readFileSync(pdf)], { type: 'application/pdf' }), name);
  const r = await fetch(endpoint, {
    method: 'POST',
    headers: { Authorization: 'Token ' + token },
    body: form,
  });
  const body = await r.text();
  if (!r.ok) throw new Error('paperless ' + r.status + ': ' + body.slice(0, 200));
  return body.trim();
}

function listOutput(re) {
  try {
    return fs.readdirSync(outputDir).filter((f) => re.test(f)).sort();
  } catch (e) {
    return [];
  }
}

async function main() {
  // Retry earlier failures first.
  for (const f of listOutput(/^failed-.*\.pdf$/)) {
    const p = path.join(outputDir, f);
    try {
      const task = await upload(p, f.replace(/^failed-/, 'scan-'));
      fs.unlinkSync(p);
      console.log('retried ' + f + ' -> paperless (task ' + task + ')');
    } catch (e) {
      console.error('retry of ' + f + ' failed: ' + e.message);
    }
  }

  // Prune old successful-scan copies scanservjs left in the output dir.
  const cutoff = Date.now() - RETAIN_DAYS * 86400 * 1000;
  for (const f of listOutput(/^scan_.*\.pdf$/)) {
    const p = path.join(outputDir, f);
    try {
      if (fs.statSync(p).mtimeMs < cutoff) fs.unlinkSync(p);
    } catch (e) {
      /* best effort */
    }
  }

  try {
    const task = await upload(file, 'scan.pdf');
    console.log('uploaded to paperless (task ' + task + ')');
  } catch (e) {
    const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..*/, '');
    const kept = path.join(outputDir, 'failed-' + stamp + '.pdf');
    try {
      fs.copyFileSync(file, kept);
      console.error('upload failed (' + e.message + '); kept scan as ' + kept);
    } catch (e2) {
      console.error('upload failed (' + e.message + ') and could not keep scan: ' + e2.message);
    }
    process.exit(1);
  }
}

main();
