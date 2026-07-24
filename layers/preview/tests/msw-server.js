import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'

export const server = setupServer()

export function model(name, extra = {}) {
  return {
    name,
    uri: `/models/${encodeURIComponent(name)}`,
    byteSize: 5204732,
    modifiedAt: '2026-07-23T20:15:00+00:00',
    triangles: 138524,
    materials: 1,
    readable: true,
    ...extra,
  }
}

/** Answer GET /api/models with this list. */
export function serveModels(models, dir = '/home/hec/workspace/text-to-3D-skill/out') {
  server.use(
    http.get('/api/models', () =>
      HttpResponse.json({ contractVersion: '1.0', dir, models })),
  )
}

/** Answer GET /api/models with an error envelope. */
export function serveError(status, body) {
  server.use(http.get('/api/models', () => HttpResponse.json(body, { status })))
}

/** Answer GET /api/models by failing the connection outright. */
export function serveNetworkFailure() {
  server.use(http.get('/api/models', () => HttpResponse.error()))
}

export { http, HttpResponse }
