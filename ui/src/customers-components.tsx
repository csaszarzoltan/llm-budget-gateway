/* Customer list and detail components for the LLM Budget Gateway cockpit.
   api() is passed as a prop — it lives in main.tsx's closure. */
import { useState, useEffect } from 'react';

type ApiFn = (p: string, o?: RequestInit) => Promise<any>;

const CUSTOMER_PALETTE = ['#5265df','#12815d','#c24753','#9b6500','#7d8dff','#5bd3a5','#ff8993','#f3bd63'];

function fmtCost(n: number): string {
  return `$${n.toFixed(2)}`;
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function fmtCalls(n: number): string {
  return n.toLocaleString();
}

function shortDate(iso: string): string {
  try {
    const d = new Date(iso);
    return `${d.getMonth() + 1}/${d.getDate()}`;
  } catch {
    return iso;
  }
}

/* ───────────────────────────────────────────
   Customers — list view
   ─────────────────────────────────────────── */

export function Customers({ onSelect, api }: { onSelect: (id: string) => void; api: ApiFn }) {
  const [list, setList] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showModal, setShowModal] = useState(false);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');

  async function load() {
    setLoading(true);
    setError('');
    try {
      const r = await api('/v1/product/customers');
      setList(r.customers || []);
    } catch (e: any) {
      setError(e?.message || 'Failed to load customers');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function addCustomer() {
    if (!newName.trim()) return;
    setCreating(true);
    setCreateError('');
    try {
      await api('/v1/product/customers', {
        method: 'POST',
        body: JSON.stringify({ name: newName.trim() }),
      });
      setNewName('');
      setShowModal(false);
      await load();
    } catch (e: any) {
      const msg = e?.message || '';
      if (msg.includes('409') || msg.toLowerCase().includes('already') || msg.toLowerCase().includes('duplicate')) {
        setCreateError('A customer with that name already exists.');
      } else {
        setCreateError(msg || 'Failed to create customer.');
      }
    } finally {
      setCreating(false);
    }
  }

  return (
    <>
      <section className="page-head">
        <div>
          <p className="kicker">CONTROL PLANE</p>
          <h1>Customers</h1>
          <p>Per-customer LLM spend, budgets and usage export.</p>
        </div>
        <button className="primary" onClick={() => { setShowModal(true); setCreateError(''); }}>+ Add customer</button>
      </section>

      {error && <p className="form-error" role="alert">{error} <button onClick={load}>Retry</button></p>}

      {loading ? (
        <p style={{ padding: 32, color: 'var(--muted)' }}>Loading customers…</p>
      ) : list.length === 0 ? (
        <div className="empty">
          <i>＋</i>
          <b>Connect a customer first</b>
          <span>Add a customer to track per-tenant LLM spend, set budgets, and export usage.</span>
        </div>
      ) : (
        <div className="panel" style={{ overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ textAlign: 'left', fontSize: 11, fontWeight: 800, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.06em' }}>
                  <th style={{ padding: '12px 16px' }}>Name</th>
                  <th style={{ padding: '12px 16px' }}>MTD Cost</th>
                  <th style={{ padding: '12px 16px' }}>MTD Calls</th>
                  <th style={{ padding: '12px 16px' }}>MTD Tokens</th>
                  <th style={{ padding: '12px 16px' }}>Budget</th>
                </tr>
              </thead>
              <tbody>
                {list.map((c: any) => (
                  <tr
                    key={c.id}
                    onClick={() => onSelect(c.id)}
                    style={{ borderTop: '1px solid var(--line)', cursor: 'pointer', transition: 'background .15s' }}
                    onMouseEnter={(e: any) => e.currentTarget.style.background = 'var(--panel2)'}
                    onMouseLeave={(e: any) => e.currentTarget.style.background = ''}
                  >
                    <td style={{ padding: '12px 16px', fontWeight: 600 }}>{c.name}</td>
                    <td style={{ padding: '12px 16px', fontVariantNumeric: 'tabular-nums' }}>{fmtCost(c.mtd?.cost_usd || 0)}</td>
                    <td style={{ padding: '12px 16px', fontVariantNumeric: 'tabular-nums' }}>{fmtCalls(c.mtd?.calls || 0)}</td>
                    <td style={{ padding: '12px 16px', fontVariantNumeric: 'tabular-nums' }}>{fmtTokens(c.mtd?.total_tokens || 0)}</td>
                    <td style={{ padding: '12px 16px' }}>
                      {c.budget ? (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 140 }}>
                          <div style={{ flex: 1, height: 6, borderRadius: 3, background: 'var(--panel2)', overflow: 'hidden' }}>
                            <div style={{
                              height: '100%',
                              width: `${Math.min(c.budget.percent_used, 100)}%`,
                              borderRadius: 3,
                              background: c.budget.percent_used >= 100 ? 'var(--bad)' : c.budget.percent_used >= 80 ? 'var(--warn)' : 'var(--good)',
                            }} />
                          </div>
                          <span style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' }}>{c.budget.percent_used}%</span>
                        </div>
                      ) : (
                        <span style={{ color: 'var(--muted)', fontSize: 12 }}>No budget</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {showModal && (
        <div className="modal" role="dialog" aria-modal="true" onClick={() => setShowModal(false)}>
          <div onClick={(e) => e.stopPropagation()}>
            <button className="close" onClick={() => setShowModal(false)}>×</button>
            <p className="kicker">NEW CUSTOMER</p>
            <h2>Add a customer</h2>
            <label>
              Customer name
              <input
                value={newName}
                onChange={(e: any) => setNewName(e.target.value)}
                placeholder="e.g. Acme Corp"
                autoFocus
                onKeyDown={(e: any) => { if (e.key === 'Enter') addCustomer(); }}
              />
            </label>
            {createError && <p className="form-error" role="alert">{createError}</p>}
            <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
              <button className="primary" onClick={addCustomer} disabled={creating || !newName.trim()}>
                {creating ? 'Creating…' : 'Create customer'}
              </button>
              <button onClick={() => setShowModal(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/* ───────────────────────────────────────────
   CustomerDetail — single customer view
   ─────────────────────────────────────────── */

export function CustomerDetail({ customerId, onBack, api }: { customerId: string; onBack: () => void; api: ApiFn }) {
  const [detail, setDetail] = useState<any>(null);
  const [daily, setDaily] = useState<any>(null);
  const [models, setModels] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [granularity, setGranularity] = useState('day');
  const [budgetEdit, setBudgetEdit] = useState(false);
  const [budgetVal, setBudgetVal] = useState('');
  const [budgetSaving, setBudgetSaving] = useState(false);
  const [budgetError, setBudgetError] = useState('');

  async function loadAll(gra?: string) {
    const g = gra || granularity;
    setLoading(true);
    setError('');
    try {
      const [d, ds, m] = await Promise.all([
        api(`/v1/product/customers/${customerId}`),
        api(`/v1/product/customers/${customerId}/daily-spend?days=31&granularity=${g}`),
        api(`/v1/product/customers/${customerId}/models`),
      ]);
      setDetail(d);
      setDaily(ds);
      setModels(m);
    } catch (e: any) {
      const msg = e?.message || '';
      if (msg.includes('404')) {
        setError('Customer data unavailable — backend endpoints may not be live yet');
      } else {
        setError(msg || 'Failed to load customer data');
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadAll(); }, [customerId]);

  async function changeGranularity(g: string) {
    setGranularity(g);
    setLoading(true);
    try {
      const ds = await api(`/v1/product/customers/${customerId}/daily-spend?days=31&granularity=${g}`);
      setDaily(ds);
    } catch {
      /* keep old data */
    } finally {
      setLoading(false);
    }
  }

  async function saveBudget() {
    const val = parseFloat(budgetVal);
    if (isNaN(val) || val < 0) {
      setBudgetError('Enter a valid budget amount.');
      return;
    }
    setBudgetSaving(true);
    setBudgetError('');
    try {
      const r = await api(`/v1/product/customers/${customerId}/budget`, {
        method: 'PUT',
        body: JSON.stringify({ monthly_limit_usd: val }),
      });
      setDetail((prev: any) => ({ ...prev, budget: r }));
      setBudgetEdit(false);
      setBudgetVal('');
    } catch (e: any) {
      setBudgetError(e?.message || 'Failed to save budget');
    } finally {
      setBudgetSaving(false);
    }
  }

  const summary = detail?.summary || {};
  const budget = detail?.budget;
  const points = daily?.points || [];
  const modelList = models?.models || [];

  /* --- SVG bar chart data --- */
  const maxCost = Math.max(1, ...points.map((p: any) => p.cost_usd || 0));
  const chartH = 200;
  const barW = Math.max(12, Math.min(32, Math.floor(600 / Math.max(points.length, 1))));
  const gap = 2;
  const svgW = points.length * (barW + gap) || 300;

  function budgetColor(pct: number): string {
    if (pct >= 100) return 'var(--bad)';
    if (pct >= 80) return 'var(--warn)';
    return 'var(--good)';
  }

  if (error) {
    return (
      <>
        <button onClick={onBack}>← Back to Customers</button>
        <div className="empty" style={{ marginTop: 32 }}>
          <i>!</i>
          <b>{error}</b>
          <span>The backend may not be ready yet. Ensure the LLM Budget Gateway server is running.</span>
          <button className="primary" onClick={() => loadAll()}>Retry</button>
        </div>
      </>
    );
  }

  return (
    <>
      {/* 1. Back link */}
      <button onClick={onBack}>← Back to Customers</button>

      {loading && !detail ? (
        <p style={{ padding: 32, color: 'var(--muted)' }}>Loading customer…</p>
      ) : detail && (
        <>
          {/* 2. Customer name */}
          <h1 style={{ margin: '12px 0 20px' }}>{detail.customer?.name || 'Customer'}</h1>

          {/* 3. Spend summary card */}
          <section className="panel" style={{ padding: 20, marginBottom: 20 }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, textAlign: 'center' }}>
              <div>
                <span style={{ fontSize: 11, fontWeight: 800, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.06em' }}>MTD Cost</span>
                <b style={{ display: 'block', fontSize: 28, marginTop: 4, fontVariantNumeric: 'tabular-nums' }}>{fmtCost(summary.mtd_cost_usd || 0)}</b>
              </div>
              <div>
                <span style={{ fontSize: 11, fontWeight: 800, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.06em' }}>Call Count</span>
                <b style={{ display: 'block', fontSize: 28, marginTop: 4, fontVariantNumeric: 'tabular-nums' }}>{fmtCalls(summary.mtd_calls || 0)}</b>
              </div>
              <div>
                <span style={{ fontSize: 11, fontWeight: 800, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.06em' }}>Token Volume</span>
                <b style={{ display: 'block', fontSize: 28, marginTop: 4, fontVariantNumeric: 'tabular-nums' }}>{fmtTokens(summary.mtd_total_tokens || 0)}</b>
              </div>
            </div>
            {(summary.mtd_prompt_tokens || summary.mtd_completion_tokens) ? (
              <div style={{ display: 'flex', justifyContent: 'center', gap: 24, marginTop: 12, fontSize: 12, color: 'var(--muted)' }}>
                <span>Prompt: {fmtTokens(summary.mtd_prompt_tokens || 0)}</span>
                <span>Completion: {fmtTokens(summary.mtd_completion_tokens || 0)}</span>
              </div>
            ) : null}
          </section>

          {/* 4. Daily spend chart */}
          <section className="panel" style={{ padding: 20, marginBottom: 20 }}>
            <div className="kicker" style={{ fontSize: 11, fontWeight: 800, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 8 }}>DAILY SPEND</div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <h3 style={{ margin: 0 }}>Cost over time</h3>
              <div style={{ display: 'flex', gap: 4 }}>
                {(['day', 'week', 'month'] as const).map((g) => (
                  <button
                    key={g}
                    onClick={() => changeGranularity(g)}
                    style={{
                      padding: '4px 10px',
                      borderRadius: 6,
                      border: '1px solid',
                      borderColor: granularity === g ? 'var(--accent)' : 'var(--line)',
                      background: granularity === g ? 'var(--accent)' : 'transparent',
                      color: granularity === g ? '#fff' : 'var(--muted)',
                      cursor: 'pointer',
                      fontSize: 11,
                      fontWeight: 600,
                      textTransform: 'capitalize',
                    }}
                  >
                    {g}
                  </button>
                ))}
              </div>
            </div>

            {points.length === 0 ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>
                <p>No spend data for this period.</p>
              </div>
            ) : (
              <div style={{ overflowX: 'auto' }} aria-label="Daily spend bar chart">
                <svg
                  width={Math.max(svgW, 300)}
                  height={chartH + 40}
                  viewBox={`0 0 ${Math.max(svgW, 300)} ${chartH + 40}`}
                  style={{ display: 'block' }}
                >
                  {/* horizontal gridlines */}
                  {[0.25, 0.5, 0.75, 1].map((frac) => (
                    <line
                      key={frac}
                      x1={0}
                      x2={Math.max(svgW, 300)}
                      y1={chartH - frac * chartH}
                      y2={chartH - frac * chartH}
                      stroke="var(--line)"
                      strokeWidth={0.5}
                      strokeDasharray="3,3"
                    />
                  ))}
                  {/* bars */}
                  {points.map((p: any, i: number) => {
                    const cost = p.cost_usd || 0;
                    const h = maxCost > 0 ? (cost / maxCost) * chartH : 0;
                    const x = i * (barW + gap) + gap / 2;
                    return (
                      <g key={p.date || i}>
                        <rect
                          x={x}
                          y={chartH - h}
                          width={barW}
                          height={h}
                          fill="var(--accent)"
                          rx={3}
                          ry={3}
                        >
                          <title>{`${shortDate(p.date)}: ${fmtCost(cost)} · ${fmtTokens(p.total_tokens || 0)} tokens · ${p.calls || 0} calls`}</title>
                        </rect>
                        {/* date label */}
                        {points.length <= 16 && (
                          <text
                            x={x + barW / 2}
                            y={chartH + 14}
                            textAnchor="middle"
                            fontSize={10}
                            fill="var(--muted)"
                          >
                            {shortDate(p.date)}
                          </text>
                        )}
                      </g>
                    );
                  })}
                  {/* y-axis scale labels */}
                  {[0.25, 0.5, 0.75, 1].map((frac) => (
                    <text
                      key={frac}
                      x={0}
                      y={chartH - frac * chartH - 4}
                      fontSize={9}
                      fill="var(--muted)"
                    >
                      {fmtCost(maxCost * frac)}
                    </text>
                  ))}
                </svg>
              </div>
            )}
          </section>

          {/* 5. Breakdown by model */}
          <section className="panel" style={{ padding: 20, marginBottom: 20 }}>
            <div className="kicker" style={{ fontSize: 11, fontWeight: 800, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 8 }}>BREAKDOWN BY MODEL</div>
            {modelList.length === 0 ? (
              <p style={{ color: 'var(--muted)', padding: 20, textAlign: 'center' }}>No model data available.</p>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ textAlign: 'left', fontSize: 11, fontWeight: 800, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.06em' }}>
                      <th style={{ padding: '10px 12px' }}>Model</th>
                      <th style={{ padding: '10px 12px', textAlign: 'right' }}>Cost</th>
                      <th style={{ padding: '10px 12px', textAlign: 'right' }}>Calls</th>
                      <th style={{ padding: '10px 12px', textAlign: 'right' }}>Tokens</th>
                    </tr>
                  </thead>
                  <tbody>
                    {modelList.map((m: any, i: number) => (
                      <tr key={m.model} style={{ borderTop: '1px solid var(--line)' }}>
                        <td style={{ padding: '10px 12px' }}>
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                            <span className="color-dot" style={{ background: CUSTOMER_PALETTE[i % CUSTOMER_PALETTE.length] }} />
                            <span style={{ fontWeight: 500 }}>{m.model}</span>
                          </span>
                        </td>
                        <td style={{ padding: '10px 12px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtCost(m.cost_usd || 0)}</td>
                        <td style={{ padding: '10px 12px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtCalls(m.calls || 0)}</td>
                        <td style={{ padding: '10px 12px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtTokens(m.total_tokens || 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* 6. Budget progress bar */}
          <section className="panel" style={{ padding: 20, marginBottom: 20 }}>
            <div className="kicker" style={{ fontSize: 11, fontWeight: 800, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 8 }}>MONTHLY BUDGET</div>

            {budget ? (
              <>
                <div style={{ marginBottom: 10 }}>
                  <span style={{ fontSize: 13, color: 'var(--text)' }}>
                    <b style={{ fontSize: 20 }}>{budget.percent_used}%</b> of {fmtCost(budget.monthly_limit_usd)} monthly budget used · {fmtCost(budget.remaining_usd)} remaining
                  </span>
                </div>
                <div style={{ height: 10, borderRadius: 5, background: 'var(--panel2)', overflow: 'hidden', marginBottom: 12 }}>
                  <div style={{
                    height: '100%',
                    width: `${Math.min(budget.percent_used, 100)}%`,
                    borderRadius: 5,
                    background: budgetColor(budget.percent_used),
                    transition: 'width .3s',
                  }} />
                </div>
                {budget.reset_day && (
                  <p style={{ fontSize: 12, color: 'var(--muted)', margin: '0 0 12px' }}>
                    Resets on day {budget.reset_day} of each month.
                  </p>
                )}
              </>
            ) : (
              <p style={{ color: 'var(--muted)', margin: '0 0 12px' }}>No budget set. Add a monthly spend cap to trigger alerts.</p>
            )}

            {budgetEdit ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 13 }}>$</span>
                <input
                  type="number"
                  min={0}
                  step={0.01}
                  value={budgetVal}
                  onChange={(e: any) => setBudgetVal(e.target.value)}
                  placeholder={budget ? String(budget.monthly_limit_usd) : '100.00'}
                  style={{ width: 120, padding: '6px 10px', border: '1px solid var(--line)', borderRadius: 6, background: 'var(--bg)', color: 'var(--text)' }}
                  autoFocus
                  onKeyDown={(e: any) => { if (e.key === 'Enter') saveBudget(); }}
                />
                <button className="primary" onClick={saveBudget} disabled={budgetSaving}>
                  {budgetSaving ? 'Saving…' : 'Save'}
                </button>
                <button onClick={() => { setBudgetEdit(false); setBudgetError(''); }}>Cancel</button>
              </div>
            ) : (
              <button onClick={() => { setBudgetEdit(true); setBudgetVal(budget ? String(budget.monthly_limit_usd) : ''); }}>
                {budget ? 'Change budget' : 'Set budget'}
              </button>
            )}
            {budgetError && <p className="form-error" role="alert" style={{ marginTop: 8 }}>{budgetError}</p>}
          </section>

          {/* 7. Export button */}
          <section style={{ marginBottom: 32 }}>
            <a
              href={`/v1/product/customers/${customerId}/export.csv`}
              download
              className="primary"
              aria-label="Export usage data as CSV"
              style={{ display: 'inline-block', padding: '10px 18px', borderRadius: 8, textDecoration: 'none' }}
            >
              Export usage CSV
            </a>
          </section>
        </>
      )}
    </>
  );
}
