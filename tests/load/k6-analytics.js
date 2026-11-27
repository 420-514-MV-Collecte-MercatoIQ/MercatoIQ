import http from 'k6/http';
import { check, group, sleep } from 'k6';

const BASE_URL = (__ENV.BASE_URL || 'http://localhost:3000/api/v1').replace(/\/+$/, '');
const SEASON = __ENV.SEASON || '2024';

export const options = {
  stages: [
    { duration: '20s', target: 15 },
    { duration: '45s', target: 40 },
    { duration: '20s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<500', 'p(99)<1200'],
    'http_req_duration{endpoint:analytics}': ['p(95)<350'],
    'http_req_duration{endpoint:silver}': ['p(95)<500'],
  },
};

function assertJsonOk(res, name) {
  check(res, {
    [`${name}: status 200`]: (r) => r.status === 200,
    [`${name}: valid json`]: (r) => {
      try {
        return Boolean(r.json());
      } catch {
        return false;
      }
    },
  });
}

export default function () {
  group('Health', () => {
    const res = http.get(`${BASE_URL}/health`, { tags: { endpoint: 'health' } });
    assertJsonOk(res, 'GET /health');
  });

  group('Gold Analytics Endpoints', () => {
    const analyticsRoutes = [
      ['kpis', `${BASE_URL}/analytics/kpis?season=${SEASON}`],
      ['club-roi', `${BASE_URL}/analytics/club-roi?season=${SEASON}&page=1&limit=20`],
      ['scouting-u23', `${BASE_URL}/analytics/scouting-u23?season=${SEASON}&page=1&limit=20`],
      [
        'ucl-vs-domestic',
        `${BASE_URL}/analytics/ucl-vs-domestic?season=${SEASON}&page=1&limit=20`,
      ],
      ['super-subs', `${BASE_URL}/analytics/super-subs?season=${SEASON}&page=1&limit=20`],
    ];

    for (const [label, url] of analyticsRoutes) {
      const res = http.get(url, { tags: { endpoint: 'analytics' } });
      assertJsonOk(res, `GET /analytics/${label}`);
    }
  });

  group('Silver Paginated Endpoints', () => {
    const playersRes = http.get(`${BASE_URL}/players?page=1&limit=20`, {
      tags: { endpoint: 'silver' },
    });
    assertJsonOk(playersRes, 'GET /players');

    const transfersRes = http.get(`${BASE_URL}/transfers?season=${SEASON}&page=1&limit=20`, {
      tags: { endpoint: 'silver' },
    });
    assertJsonOk(transfersRes, 'GET /transfers');
  });

  sleep(0.3);
}
