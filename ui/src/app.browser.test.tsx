// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'

const payloads: Record<string, unknown> = {
  '/v1/product/home?role=developer': {gateway:{endpoint:'http://local/v1'},counts:{applications:0,routes:0,providers:1},activation:{complete:5,steps:[]},metrics:{requests:1,success_rate:100,cost_usd:0.1,p95_latency_ms:10},attention:[],routes:[],activity:[]},
  '/v1/product/applications': {applications:[]}, '/v1/product/routes': {routes:[]},
  '/v1/product/provider-connections': {providers:[{id:'provider-1',name:'Local provider',provider_type:'openai_compatible'}]},
  '/v1/product/usage': {requests:1,cost_usd:0.1,success_rate:100,avg_latency_ms:10,by_route:[],by_model:[]},
  '/v1/product/activity': {activity:[{id:'req-1',route:'support',model:'model-a',success:false,latency_ms:20,cost_usd:0.2,reason:'429'}]},
  '/v1/product/provider-types': {provider_types:[]}, '/v1/product/discovered-models': {models:[]},
  '/v1/system/status': {ready:true,failures:[]},
}

afterEach(()=>{vi.restoreAllMocks();document.body.innerHTML=''})

it('renders and navigates the real Safety workspace with accessible controls', async()=>{
  document.body.innerHTML='<div id="root"></div>'
  vi.stubGlobal('fetch', vi.fn(async(input:RequestInfo|URL)=>{
    const path=String(input)
    return new Response(JSON.stringify(payloads[path]??{}),{status:200,headers:{'Content-Type':'application/json'}})
  }))
  await import('./main')
  await vi.waitFor(()=>expect(document.body.textContent).toContain('Your AI traffic, routed safely.'))
  const safety=[...document.querySelectorAll('button')].find(x=>x.textContent?.includes('Safety'))
  expect(safety).toBeTruthy(); safety!.click()
  await vi.waitFor(()=>expect(document.body.textContent).toContain('Provider Compatibility Lab'))
  expect(document.querySelector('select[aria-label="Provider connection"]')).toBeTruthy()
  expect(document.querySelector('select[aria-label="Request evidence"]')).toBeTruthy()
  expect(document.querySelector('[role="alert"]')).toBeNull()
})
