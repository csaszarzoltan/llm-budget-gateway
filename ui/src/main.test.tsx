import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const source = readFileSync(fileURLToPath(new URL('./main.tsx', import.meta.url)), 'utf8')
const styles = readFileSync(fileURLToPath(new URL('./styles.css', import.meta.url)), 'utf8')

describe('production cockpit source contracts', () => {
  it('contains the complete primary navigation and product flows', () => {
    for (const label of ['Home', 'Applications', 'Routes', 'Providers', 'Activity', 'Usage', 'Advanced']) expect(source).toContain(label)
    expect(source).toContain('/v1/product/provider-connections')
    expect(source).toContain('/v1/product/routes')
  })
  it('contains accessibility and responsive contracts', () => {
    expect(source).toContain('aria-label')
    expect(styles).toContain(':focus-visible')
    expect(styles).toMatch(/@media\s*\(max-width:/)
  })
  it('does not embed provider credentials or production secrets', () => {
    expect(source).not.toMatch(/sk-[A-Za-z0-9]{20,}/)
    expect(source).not.toContain('BEGIN PRIVATE KEY')
  })
})

it('research P0 cockpit exposes a complete safety operations flow', () => {
  const source = readFileSync(new URL('./main.tsx', import.meta.url), 'utf8')
  expect(source).toContain('Cost firewall')
  expect(source).toContain('Provider Compatibility Lab')
  expect(source).toContain('Explain selected request')
  expect(source).toContain('/v1/console/compatibility/${providerId}/run')
  expect(source).toContain('/v1/console/incidents/')
  expect(source).toContain('role="alert"')
})


it('safety workflows use selected live product evidence instead of hardcoded demos', () => {
  expect(source).toContain('/v1/console/compatibility/${providerId}/run')
  expect(source).toContain('/v1/console/incidents/from-request/${requestId}')
  expect(source).not.toContain("provider_id:'connected-provider'")
  expect(source).not.toContain('/v1/console/incidents/demo-incident')
  expect(source).toContain('/v1/console/runaway/evaluate')
})

it('ships the market-priority replay flow',()=>{expect(source).toContain('Production Replay Lab');expect(source).toContain('/v1/console/replay/compare');expect(source).toContain('Compare latest request');expect(source).toContain('role="status"')})

it('exposes the portable OpenTelemetry evidence plane',()=>{expect(source).toContain('OpenTelemetry evidence');expect(source).toContain('/v1/console/evidence/traces')})

it('exposes safe releases and outcome-aware autopilot',()=>{expect(source).toContain('Safe releases');expect(source).toContain('/v1/console/releases/plan');expect(source).toContain('Optimization autopilot');expect(source).toContain('/v1/console/autopilot/recommend')})

it('ships the complete Route Studio lifecycle and visual workflow',()=>{for(const text of ['Open designer','Search routes','Archived routes','Route Studio sections','Validate graph','Simulate','Save draft','Review & publish','Delete permanently','Type the route name to confirm','Required capabilities','Move up','Move down'])expect(source).toContain(text);expect(source).toContain('/dependencies');expect(source).toContain('/archive');expect(source).toContain('/restore');expect(source).toContain("'DELETE'")})

it('home gateway banner is driven by the reported status, not hard-coded', () => {
  expect(source).toContain('d.gateway.status')
  expect(source).toContain('Gateway unreachable')
  expect(source).not.toMatch(/<span className="healthy">● Gateway healthy<\/span>/)
})

it('usage page warns when traffic ran on models with no known price', () => {
  expect(source).toContain('d?.unpriced?.requests')
  expect(source).toContain('no known price')
})

it('usage page lists active provider plan limits with time until reset', () => {
  expect(source).toContain('/v1/product/quota')
  expect(source).toContain('Plan limits')
  expect(source).toContain('remaining_seconds')
})

it('usage page shows the share of input tokens served from the prompt cache', () => {
  expect(source).toContain('Prompt cache')
  expect(source).toContain('d?.cache?.cached_share')
})

it('usage toolbar wraps on narrow screens and the skip link is hidden until focused', () => {
  expect(styles).toMatch(/\.usage-toolbar\{[^}]*flex-wrap:wrap/)
  expect(styles).toMatch(/\.skip\{[^}]*left:-9999px/)
  expect(styles).toContain('.skip:focus')
  expect(styles).toMatch(/@media\s*\(max-width:640px\)\{[^@]*min-height:40px/)
  expect(styles).toMatch(/@media\s*\(max-width:640px\)\{[^@]*\.calls-panel\{overflow-x:auto\}/)
})
