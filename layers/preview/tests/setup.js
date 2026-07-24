// MSW intercepts at the network layer, so the UI runs its real fetch calls.
import { afterAll, afterEach, beforeAll } from 'vitest'
import { server } from './msw-server.js'

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
