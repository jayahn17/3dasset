# assetpipe dashboard

Customer-facing view of processed scans. Deployed on Vercel; **assets and the
manifest live in blob storage**, so a new scan appears without a redeploy.

```
iOS CrateScanner → Drive → [GPU box] assetpipe → demo_out/ cad_out/ sim_out/
                                        ↓
                        python tools/publish_dashboard.py --uploader vercel
                          ├─ uploads deliverables → Vercel Blob
                          └─ uploads manifest.json → Blob (stable URL)
                                        ↓
                          this app fetches the manifest at request time
```

## Local

```bash
cd web && npm install
# publish into public/ so there is something to render
python ../tools/publish_dashboard.py --uploader local \
    --dest-dir public/assets --base-url /assets
npm run dev            # http://localhost:3000
```

## Deploy

```bash
npm i -g vercel
vercel link
vercel env add BLOB_READ_WRITE_TOKEN     # from Vercel ▸ Storage ▸ Blob
vercel --prod
```

Then publish from the GPU box and set the printed URL on the project:

```bash
export BLOB_READ_WRITE_TOKEN=vercel_blob_rw_...
python tools/publish_dashboard.py --uploader vercel
vercel env add NEXT_PUBLIC_MANIFEST_URL   # the URL it prints
```

Re-run the publish for every new scan. No redeploy — the page re-fetches.
