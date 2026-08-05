# scenes/

Local scene bundles — `scene.json`, splat LOD chunks, `collision.glb`,
`preview.jpg`. Written by `pipeline/bundle.py`, served to the viewers.

**Gitignored.** A single room is ~80 MB compressed and several GB uncompressed;
artifacts belong in the blob store keyed by content hash, not in git. See
[../docs/DATA_MODEL.md](../docs/DATA_MODEL.md).
