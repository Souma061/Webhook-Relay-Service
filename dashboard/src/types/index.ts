export interface Endpoint {
  id: string;
  name: string;
  is_active: boolean;
  hmac_secret: string;
  created_at: string;
}

export interface RouteConfig {
  id: string;
  name: string;
  url: string;
  method: string;
  is_active: boolean;
  timeout_ms: number;
  max_retries: number;
  created_at: string;
}

export interface WebhookEvent {
  id: string;
  endpoint_id: string;
  idempotency_key: string | null;
  request_body: unknown;
  received_at: string;
  is_discarded: boolean;
}

export interface DeliveryAttempt {
  id: string;
  event_id: string;
  route_id: string;
  attempt_number: number;
  request_url: string;
  response_status: number | null;
  error: string | null;
  duration_ms: number | null;
  attempted_at: string;
}

export interface DlqEvent {
  event_id: string;
  endpoint_id: string;
  received_at: string;
  request_body: unknown;
  is_discarded: boolean;
  discarded_at: string | null;
  last_error: string | null;
  last_status: number | null;
  last_url: string | null;
  total_attempts: number;
}

export type ToastType = 'success' | 'error';

export interface Toast {
  id: number;
  message: string;
  type: ToastType;
}
