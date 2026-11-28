# MercatoIQ Load Testing (`k6`)

Benchmarks Gold analytics endpoints (`/analytics/kpis`, `/analytics/club-roi`, `/analytics/scouting-u23`, `/analytics/ucl-vs-domestic`, `/analytics/super-subs`), Silver paginated reads (`/players`, `/transfers`), and `/health`.

## Prerequisites

1. **Docker services running** (MongoDB 7 + Redis 7):
   ```bash
   docker compose up -d
   ```
2. **Gold & Silver collections populated** via the Python pipeline (`pipeline/run_pipeline.py`).
3. **NestJS backend running** on `http://localhost:3000`:
   ```bash
   cd backend && npm run start:prod
   ```
4. **Install `k6`**: <https://grafana.com/docs/k6/latest/set-up/install-k6/>

## Run Load Tests

```bash
# Default target: http://localhost:3000/api/v1, season=2024
k6 run tests/load/k6-analytics.js

# Custom API base URL or season
BASE_URL=http://localhost:3000/api/v1 SEASON=2023 k6 run tests/load/k6-analytics.js

# Save JSON summary for before/after Redis cache comparison
k6 run --summary-export=tests/load/summary-redis.json tests/load/k6-analytics.js
```

## Target SLA Thresholds

| Metric | Target |
| :--- | :--- |
| Error rate (`http_req_failed`) | `< 1%` |
| Gold analytics `p95` latency | `< 350 ms` |
| Silver paginated `p95` latency | `< 500 ms` |
| Overall `p99` latency | `< 1200 ms` |

## Redis Cache Comparison Log *(to complete after `#43` merges)*

| Scenario | Throughput (req/s) | p50 Latency | p95 Latency | p99 Latency | Error Rate |
| :--- | :---: | :---: | :---: | :---: | :---: |
| MongoDB only (cache disabled) | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| MongoDB + Redis 7 (`BACK-05a`) | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
