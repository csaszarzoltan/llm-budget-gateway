// Source-contract tests for the Customers cockpit flow (US-001 cost attribution UI).
// Mirrors the existing main.test.tsx pattern: read main.tsx as text, assert the
// integration seams exist and are wired to the real API contract — no mocks in the
// shipped path.
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const main = readFileSync(fileURLToPath(new URL('./main.tsx', import.meta.url)), 'utf8')
const components = readFileSync(fileURLToPath(new URL('./customers-components.tsx', import.meta.url)), 'utf8')

describe('customers flow (US-001)', () => {
  it('adds Customers to the primary navigation after Usage', () => {
    const usageIdx = main.indexOf("'usage','Usage','◔'")
    const customersIdx = main.indexOf("'customers','Customers','◈'")
    expect(usageIdx).toBeGreaterThan(-1)
    expect(customersIdx).toBeGreaterThan(usageIdx)
    expect(main).toMatch(/type View='[^']*'(\|'[^']*')*\|'customers'\|/)
  })

  it('renders the Customers list view wired to the live API', () => {
    expect(main).toContain("view==='customers'&&")
    expect(main).toContain('<Customers onSelect={setSelectedCustomer} api={api}/>')
    expect(main).toContain('selectedCustomer?<CustomerDetail customerId={selectedCustomer}')
    expect(main).toMatch(/\[selectedCustomer,setSelectedCustomer\]=useState/)
  })

  it('list view fetches the real customers endpoint and posts the add-customer modal', () => {
    expect(components).toContain("api('/v1/product/customers')")
    expect(components).toContain("method: 'POST'")
    expect(components).toContain("JSON.stringify({ name: newName.trim() })")
    expect(components).toContain('+ Add customer')
    expect(components).toContain('Connect a customer first')
  })

  it('detail page follows the exact GUI flow order (summary → chart → models → budget → export)', () => {
    const order = ['SPEND SUMMARY', 'MTD Cost', 'DAILY SPEND', 'BREAKDOWN BY MODEL', 'MONTHLY BUDGET', 'Export usage CSV'].map(
      (s) => components.indexOf(s)
    )
    for (let i = 1; i < order.length; i++) expect(order[i]).toBeGreaterThan(order[i - 1])
  })

  it('daily spend chart toggles day/week/month granularity against the live endpoint', () => {
    expect(components).toContain("granularity=${g}`")
    expect(components).toContain("(['day', 'week', 'month'] as const)")
    expect(components).toContain('days=31')
    expect(components).toContain('aria-label="Daily spend bar chart"')
  })

  it('model breakdown and budget progress use the real contract fields', () => {
    expect(components).toContain('/models')
    expect(components).toContain('m.cost_usd')
    expect(components).toContain('budget.percent_used')
    expect(components).toContain('budget.remaining_usd')
    expect(components).toContain('monthly_limit_usd')
  })

  it('export button downloads the CSV ledger from the live endpoint', () => {
    expect(components).toContain('href={`/v1/product/customers/${customerId}/export.csv`}')
    expect(components).toContain('download')
    expect(components).toContain('aria-label="Export usage data as CSV"')
  })
})
