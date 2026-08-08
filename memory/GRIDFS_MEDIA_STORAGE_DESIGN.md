# GridFS Media Storage — Future Design Doc

**Status:** PARKED (saved for future implementation, per founder direction on 2026-02).
**Priority:** P1 (execute when ORA image permanence becomes a user-blocking issue).
**Owner decision pending:** GridFS-first vs Emergent-managed storage first.

---

## 1. Why this doc exists

Currently AUREM has **no persistent object storage layer**:

- `routers/upload.py` — reads user files → base64 → vision LLM → **discards binary**. Files never persist.
- `routers/ora_chat.py` `/image` — OpenAI returns an **ephemeral URL** (expires ~1-2 hours). We save that URL in `ora_image_events` collection, but the underlying image dies. User's gallery breaks.
- No R2 / S3 / Supabase Storage / Cloudinary integration exists despite the aspirational comment in `upload.py` (`# arch: allow-http — R2/S3 object upload (iter 212m-225)`).

Founder asked: "Agar hum apna banayein toh?" — this doc captures the **GridFS-based own-storage plan** so a future agent can execute without re-deriving.

---

## 2. Options considered (2026-02)

| Option | Cost | Time | CDN | Scale ceiling | Verdict |
|---|---|---|---|---|---|
| **Emergent-managed storage** | Free tier → usage-based | 2-3 days | ✅ Built-in | 100k+ users | **Recommended for Phase 1** |
| **GridFS (this doc)** | $0 extra | 2 days | ❌ None | ~500-1000 users | **Fallback if Emergent path blocked** |
| Local disk + PVC | $0 extra | 1 day | ❌ None | Single-pod only | Rejected (multi-pod K8s broken) |
| MinIO self-host | Ops burden | 1-2 weeks | Separate CDN needed | 10k+ | Rejected (over-engineering for solo founder) |

Founder preference at time of writing: **build own → GridFS route documented here for later.**

---

## 3. GridFS — 30-second refresher

MongoDB's built-in file storage. Regular Mongo docs are capped at 16MB. GridFS:

1. Splits files into **255KB chunks** → `fs.chunks` collection.
2. Stores metadata (filename, size, contentType, uploadDate, custom fields) → `fs.files` collection.
3. Reassembles on read by streaming chunks in order.

Effectively turns MongoDB into "our own S3" — no new infra, uses same `MONGO_URL` we already have.

---

## 4. What to build

### 4.1 New backend service — `/app/backend/services/storage.py`

Async GridFS wrapper using `motor.motor_asyncio.AsyncIOMotorGridFSBucket`.

Interface:
```python
async def upload_file(
    data: bytes,
    filename: str,
    content_type: str,
    owner_user_id: str,
    metadata: dict | None = None,
) -> str:  # returns file_id (ObjectId as string)

async def get_file_stream(file_id: str):
    # returns async generator of chunks + content_type header
    # raises HTTPException(404) if not found

async def delete_file(file_id: str, requesting_user_id: str) -> bool:
    # owner check enforced here, not in router

async def get_metadata(file_id: str) -> dict:
    # returns {filename, size, content_type, uploaded_at, owner_user_id, custom_metadata}

async def list_user_files(user_id: str, limit: int = 50) -> list[dict]:
    # for future gallery endpoint
```

Store custom metadata:
```python
{
    "owner_user_id": "u_...",
    "source": "ora_image_gen" | "chat_upload" | "avatar",
    "prompt": "...",              # only for ora_image_gen
    "original_openai_url": "...", # audit trail
    "created_at": "2026-02-...",
}
```

### 4.2 New router — `/app/backend/routers/media.py`

```
POST   /api/aurem-dev/media/upload          # multipart, auth required
GET    /api/aurem-dev/media/{file_id}       # stream, auth required (owner only)
GET    /api/aurem-dev/media/public/{file_id}?exp=...&sig=...   # HMAC-signed public
DELETE /api/aurem-dev/media/{file_id}       # auth, owner only
GET    /api/aurem-dev/media/mine            # list caller's files
```

### 4.3 HMAC signed URL scheme (CDN substitute)

Since GridFS has no CDN, we use signed URLs to allow safe embedding in `<img src="">`:

```python
def sign_url(file_id: str, expiry_seconds: int = 604800) -> str:
    exp = int(time.time()) + expiry_seconds
    msg = f"{file_id}:{exp}".encode()
    sig = hmac.new(MEDIA_SIGNING_SECRET.encode(), msg, hashlib.sha256).hexdigest()[:32]
    return f"/api/aurem-dev/media/public/{file_id}?exp={exp}&sig={sig}"

def verify_signed_url(file_id: str, exp: int, sig: str) -> bool:
    if exp < int(time.time()):
        return False
    msg = f"{file_id}:{exp}".encode()
    expected = hmac.new(MEDIA_SIGNING_SECRET.encode(), msg, hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(sig, expected)
```

New env: `MEDIA_SIGNING_SECRET` — cryptographic random 32+ bytes.
Default expiry: **7 days** (refresh on chat reload).

### 4.4 HTTP caching headers

Compensate for no-CDN by making browser cache aggressively:
```
Cache-Control: public, max-age=604800, immutable
ETag: <file_id>
```
Files are immutable (file_id = ObjectId = unique content), so `immutable` is safe.

### 4.5 Integration hooks (call sites to modify)

**A. `routers/ora_chat.py` — image generation persistence**

After OpenAI returns image URL, immediately download bytes → `storage.upload_file()` → replace URL in response with signed URL:

```python
# after openai.images.generate returns openai_url:
raw = await http.get(openai_url).content
file_id = await storage.upload_file(
    raw, f"ora_{uuid4().hex}.png", "image/png",
    owner_user_id=user_id,
    metadata={"source": "ora_image_gen", "prompt": prompt,
              "original_openai_url": openai_url},
)
permanent_url = storage.sign_url(file_id)
# save permanent_url in ora_image_events (not openai_url)
```

**B. `routers/upload.py` — optional chat attachment persistence**

Add query param `persist=true`. When true, after vision OCR:
```python
if persist:
    file_id = await storage.upload_file(raw, filename, ctype, user_id,
                                         metadata={"source": "chat_upload"})
    response["file_id"] = file_id
    response["permanent_url"] = storage.sign_url(file_id)
```

**C. Frontend — `OraDirect.jsx`**

Just swap the `<img src={openai_url}>` for `<img src={permanent_url}>`. No other UI change needed.

### 4.6 Tests to write (`/app/backend/tests/`)

- `test_gridfs_storage.py` — upload / download / delete / owner-check / metadata roundtrip
- `test_gridfs_signed_urls.py` — sign, verify, expiry, tampering rejection, timing-safe compare
- `test_gridfs_ora_image_persistence.py` — E2E: `/image` command → GridFS row exists → signed URL renders
- `test_gridfs_media_router.py` — auth boundary tests, 401/403/404 paths

---

## 5. Feature flag & rollback

- Env: `ENABLE_GRIDFS_MEDIA` (default `false`).
- When `false`: current behavior unchanged, OpenAI ephemeral URLs used as-is.
- When `true`: new persistence path active.
- Rollback: flip flag off. Existing GridFS rows harmless (just orphan data).

---

## 6. Known trade-offs (documented for future reconsideration)

| Concern | Impact | Mitigation | When to worry |
|---|---|---|---|
| No CDN | 200-500ms latency per image | Browser cache headers | 500+ concurrent users |
| Backend bandwidth | Emergent quota burn | Signed URLs prevent hotlinking | Traffic spikes |
| Mongo bloat | Query IOPS shared | Separate `fs.chunks` collection isolates writes | DB > 30GB |
| Cold chunk assembly | ~500ms first-byte for 5MB files | HTTP streaming response | Rarely — images are small |
| Migration ceiling | 6-12 months → R2/S3 anyway | Keep storage.py interface S3-compatible for 1-day swap | 5k+ paying users |

---

## 7. Effort estimate (validated 2026-02)

| Task | Time |
|---|---|
| `services/storage.py` + tests | 3 hours |
| `routers/media.py` + tests | 4 hours |
| HMAC signing + tests | 2 hours |
| ORA chat integration | 2 hours |
| `upload.py` optional persist | 1 hour |
| Frontend URL swap in `OraDirect.jsx` | 1 hour |
| E2E via testing_agent | 2 hours |
| **Total** | **~15 hours (2 focused sessions)** |

---

## 8. Prerequisites before starting

- [ ] Confirm current MongoDB size via `db.stats()` — ensure headroom for 5-10GB media growth.
- [ ] Founder decision: **Emergent-managed storage vs GridFS** — GridFS only if Emergent path is unavailable or too expensive.
- [ ] Add `MEDIA_SIGNING_SECRET` to `backend/.env` (32+ random bytes).
- [ ] Confirm `motor` version supports `AsyncIOMotorGridFSBucket` (it does since 3.0; check `backend/requirements.txt`).

---

## 9. Not doing (out of scope)

- Image resizing / thumbnails (add later via Pillow if needed).
- Virus scanning (add ClamAV pass if UGC ever goes public).
- Multipart chunked upload for >100MB files (25MB cap in `upload.py` today).
- Public gallery UI (persistence first, UI later).

---

**Last updated:** 2026-02 by main agent, per founder instruction "save it for future ok".
**Next action trigger:** Founder says "start GridFS" OR ORA image expiry becomes user-reported issue.
