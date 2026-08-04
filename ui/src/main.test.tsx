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
  expect(source).toContain('Explain incident')
  expect(source).toContain('/v1/console/compatibility/evaluate')
  expect(source).toContain('/v1/console/incidents/')
  expect(source).toContain('role="alert"')
})
