/* eslint-disable */
/**
 * Upload a PDF to paperless-ngx. Invoked by the "Scan to Paperless" pipeline
 * (config.local.js) — the scanservjs image ships no curl/wget, but node 20 has
 * global fetch/FormData/Blob, so the upload is done here.
 *
 * The API token is read from the mounted secret file (scanservjs-secret ->
 * /secrets/PAPERLESS_TOKEN); keeping it out of the pipeline command means it
 * never lands in scanservjs' info-level command logs. Empty token -> paperless
 * 401 -> non-zero exit -> the pipeline surfaces the error. Usage:
 *   node upload-to-paperless.js <pdf-path>
 */
const fs = require('fs');

const file = process.argv[2] || 'paperless.pdf';
const endpoint =
  'http://paperless.collab.svc.cluster.local:8000/api/documents/post_document/';

let token = '';
try {
  token = fs.readFileSync('/secrets/PAPERLESS_TOKEN', 'utf8').trim();
} catch (e) {
  /* file absent until the 1Password field exists */
}
if (!token) {
  console.error('no paperless token at /secrets/PAPERLESS_TOKEN');
  process.exit(1);
}

const buf = fs.readFileSync(file);
const form = new FormData();
form.append('document', new Blob([buf], { type: 'application/pdf' }), 'scan.pdf');

fetch(endpoint, {
  method: 'POST',
  headers: { Authorization: 'Token ' + token },
  body: form,
})
  .then(async (r) => {
    const body = await r.text();
    if (!r.ok) {
      console.error('paperless ' + r.status + ': ' + body);
      process.exit(1);
    }
    console.log('uploaded to paperless (task ' + body.trim() + ')');
  })
  .catch((e) => {
    console.error('upload failed: ' + e.message);
    process.exit(1);
  });
