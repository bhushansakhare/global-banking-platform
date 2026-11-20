import { request } from '@/services/http';

export const ROOT = '/integrations/finzola';
export const DOMAINS = ['organizations', 'users', 'vendors', 'expenses', 'approvals', 'finance', 'logs'] as const;
export type Domain = typeof DOMAINS[number];
export interface GatewayConfig {
  configured: boolean; domains: Domain[]; polling_enabled: boolean; interval_minutes: 5 | 15 | 60;
  next_due: string | null; last_fetch: string | null; authorization_valid?: boolean;
  state: Partial<Record<Domain, { count: number; last_success: string | null; error: string | null }>>;
}
export interface Mapping {
  id: string; external_org_id: string; account_id: string; routing_field: 'default' | 'vendor_id' | 'bank_account_id';
  routing_value: string; amount_field: 'net_amount' | 'amount'; amount_units: 'major' | 'minor';
  date_field: 'due_date' | 'expected_payment_date' | 'payment_date'; reference_field: 'transaction_reference' | 'invoice_number';
  direction: 'debit' | 'credit'; enabled: boolean; revision: number;
}
export type MappingDraft = Omit<Mapping, 'id' | 'revision' | 'amount_units'> & { amount_units: '' | Mapping['amount_units'] };
export interface Expected {
  amount: string; amount_minor: number; currency: string; reference: string; date: string; purpose: string; direction: string;
}
export interface BridgeItem {
  id: string; external_org_id: string; external_id: string; account_id: string | null; status: string; reason: string;
  expected: Partial<Expected>; session_id: string | null; decision: string | null; updated_at: string;
}
export interface Pipeline {
  mappings: Mapping[]; items: BridgeItem[]; total: number;
  jobs: { id: string; type: string; status: string; progress: string | null; error: string | null; result: Record<string, unknown> }[];
}
export interface Destination {
  id: string; external_org_id: string; has_key: boolean; revision: number; verified: boolean;
  verified_at: string | null; auto_delivery: boolean; last_error: string | null;
}
export interface Delivery {
  id: string; destination_id: string; source_kind: string; source_id: string; request_id: string;
  status: string; attempts: number; error: string | null; acknowledgement: Record<string, unknown> | null;
  payload: Record<string, unknown>; created_at: string; job_id: string | null;
}
export interface Outbound { destinations: Destination[]; deliveries: Delivery[]; total: number }
export interface Records { items: Record<string, unknown>[]; total: number; page: number; state: GatewayConfig['state'][Domain] }

export const finzolaApi = {
  config: () => request<GatewayConfig>(`${ROOT}/custom-api`),
  saveConfig: (body: object) => request<GatewayConfig>(`${ROOT}/custom-api`, { method: 'PUT', body }),
  fetch: () => request(`${ROOT}/custom-api/fetch`, { method: 'POST' }),
  records: (domain: Domain, page: number) => request<Records>(`${ROOT}/custom-api/data/${domain}`, { query: { page } }),
  pipeline: (page: number) => request<Pipeline>(`${ROOT}/custom-api/pipeline`, { query: { page } }),
  saveMapping: (body: MappingDraft) => request<{ comparison_queued: boolean }>(`${ROOT}/custom-api/pipeline/mapping`, { method: 'PUT', body }),
  process: () => request(`${ROOT}/custom-api/pipeline/process`, { method: 'POST' }),
  reviewPreview: (id: string) => request<{ existing: Partial<Expected>; current: Expected | null; account_id?: string; error?: string }>(`${ROOT}/custom-api/pipeline/items/${id}/review-preview`),
  review: (id: string, body: object) => request(`${ROOT}/custom-api/pipeline/items/${id}/review`, { method: 'POST', body }),
  reviewEvidence: (id: string, reason: string) => request<{ reviewed: boolean; comparison_queued: boolean }>(`${ROOT}/evidence/${id}/review`, { method: 'POST', body: { reason } }),
  outbound: (page: number) => request<Outbound>(`${ROOT}/outbound`, { query: { page } }),
  saveDestination: (body: object) => request(`${ROOT}/outbound`, { method: 'PUT', body }),
  testDestination: (org: string) => request(`${ROOT}/outbound/${encodeURIComponent(org)}/test`, { method: 'POST' }),
  removeDestination: (org: string) => request(`${ROOT}/outbound/${encodeURIComponent(org)}`, { method: 'DELETE' }),
  previewDebit: (body: object) => request<{ payload: Record<string, unknown> }>(`${ROOT}/outbound/transactions/preview`, { method: 'POST', body }),
  sendDebit: (body: object) => request<Delivery>(`${ROOT}/outbound/transactions/send`, { method: 'POST', body }),
  retry: (id: string) => request(`${ROOT}/outbound/deliveries/${id}/retry`, { method: 'POST' }),
};
