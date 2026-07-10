# MediaForge Queue API Reference

Base URL: `http://127.0.0.1:5000`

All endpoints return JSON. Errors return `"success": false` with a `"message"` string.

---

## GET /queue

Fetch the full queue state.

**Response** `200 OK`:
```json
{
    "success": true,
    "queue": {
        "active": { "id": "...", "status": "downloading", ... } | null,
        "queued": [ { "id": "...", "status": "queued", ... } ],
        "failed":  [ { "id": "...", "status": "failed", ... } ],
        "queued_count": 0,
        "failed_count": 0,
        "history": [ ... ]
    }
}
```

**Errors**: None.

---

## POST /queue/pause

Pause a running or queued job.

**Request**:
```json
{ "job_id": "abc123" }
```

**Response** `200 OK`:
```json
{ "success": true, "message": "Job paused" }
```

**Errors**:

| Status | Condition |
|---|---|
| `400` | `job_id` missing |
| `400` | Job not found |
| `400` | Job cannot be paused (wrong status) |

---

## POST /queue/resume

Resume a paused job.

**Request**:
```json
{ "job_id": "abc123" }
```

**Response** `200 OK`:
```json
{ "success": true, "message": "Job resumed" }
```

**Errors**:

| Status | Condition |
|---|---|
| `400` | `job_id` missing |
| `400` | Job not found |
| `400` | Job is not paused |

---

## POST /queue/retry

Re-queue a failed job.

**Request**:
```json
{ "job_id": "abc123" }
```

**Response** `200 OK`:
```json
{ "success": true, "job": { ... }, "job_id": "abc123" }
```

**Errors**:

| Status | Condition |
|---|---|
| `400` | `job_id` missing |
| `400` | Job not found |
| `400` | Job is not in failed status |

---

## POST /queue/remove

Delete a job from the queue entirely.

**Request**:
```json
{ "job_id": "abc123" }
```

**Response** `200 OK`:
```json
{ "success": true, "message": "Job removed" }
```

**Errors**:

| Status | Condition |
|---|---|
| `400` | `job_id` missing |
| `400` | Job not found |

---

## POST /queue/reorder

Move a job to a new position in the queue.

**Request**:
```json
{ "job_id": "abc123", "new_index": 0 }
```

**Response** `200 OK`:
```json
{ "success": true, "message": "Job reordered" }
```

**Errors**:

| Status | Condition |
|---|---|
| `400` | `job_id` or `new_index` missing |
| `400` | Job not found |

`new_index` is clamped to `[0, queue_length]`.

---

## POST /queue/priority

Change the priority of a job.

**Request**:
```json
{ "job_id": "abc123", "priority": "high" }
```

Valid priorities: `"high"`, `"normal"`, `"low"`.

**Response** `200 OK`:
```json
{ "success": true, "message": "Priority updated" }
```

**Errors**:

| Status | Condition |
|---|---|
| `400` | `job_id` missing |
| `400` | Invalid priority value |
| `400` | Job not found |

---

## POST /queue/cancel

Cancel a running, queued, paused, or retrying job. Marks the job as `"cancelled"` and appends it to download history.

**Request**:
```json
{ "job_id": "abc123" }
```

**Response** `200 OK`:
```json
{ "success": true, "job": { "status": "cancelled", ... }, "job_id": "abc123" }
```

**Errors**:

| Status | Condition |
|---|---|
| `400` | `job_id` missing |
| `400` | Job not found |
| `400` | Job cannot be cancelled (e.g. already completed or failed) |

---

## Common Job Fields

| Field | Type | Description |
|---|---|---|
| `id` | string | UUID hex |
| `url` | string | Source URL |
| `mode` | string | `mp3`, `1080p`, `4k`, `8k`, `playlist_mp3`, `playlist_video` |
| `label` | string | Human-readable label |
| `status` | string | `queued`, `downloading`, `running`, `paused`, `completed`, `failed`, `retrying`, `cancelled` |
| `progress` | float | 0.0–100.0 |
| `speed` | string | Human-readable speed or empty |
| `eta` | string | Timestamp or empty |
| `message` | string | Status description |
| `error` | string | Error detail (only on failure) |
| `attempts` | int | Current attempt number |
| `max_retries` | int | Maximum retry count |
| `queued_at` | string | ISO-8601 timestamp |
| `started_at` | string | ISO-8601 timestamp |
| `completed_at` | string | ISO-8601 timestamp |
