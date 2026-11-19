export type FieldErrors = Record<string, string[]>;

export class ApiError extends Error {
  constructor(public code: string, message: string, public fields: FieldErrors = {}, public status = 0) {super(message);}
}

function csrfCookie(): string | undefined {
  const item = document.cookie.split('; ').find(value => value.startsWith('csrftoken='));
  return item ? decodeURIComponent(item.slice('csrftoken='.length)) : undefined;
}

export type ApiMetadata = {serverDate: number | null; receivedAt: number; roundTripMs: number};

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function fieldErrors(value: unknown): FieldErrors {
  if (!record(value)) return {};
  return Object.fromEntries(Object.entries(value).filter((entry): entry is [string, string[]] =>
    Array.isArray(entry[1]) && entry[1].every(item => typeof item === 'string')));
}

// The optional observer exposes HTTP time without changing existing callers' payloads.
export async function api<T>(path: string, method = 'GET', body?: unknown,
  onResponse?: (metadata: ApiMetadata) => void): Promise<T> {
  if (!path.startsWith('/api/')) throw new Error('Only same-origin /api/ paths are supported');
  const unsafe = !['GET', 'HEAD', 'OPTIONS'].includes(method);
  if (unsafe && !csrfCookie()) await ensureCsrf();
  const headers: Record<string, string> = {'Accept': 'application/json'};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (unsafe) headers['X-CSRFToken'] = csrfCookie() ?? '';
  let response: Response;
  const startedAt = performance.now();
  try {response = await fetch(path, {method, headers, credentials: 'same-origin', body: body === undefined ? undefined : JSON.stringify(body)});}
  catch {throw new ApiError('network_error', 'Не удалось связаться с сервером. Проверьте соединение и попробуйте снова.');}
  const receivedAt = performance.now();
  const date = Date.parse(response.headers.get('Date') ?? '');
  onResponse?.({serverDate: Number.isFinite(date) ? date : null, receivedAt, roundTripMs: receivedAt - startedAt});
  if (response.status === 401 && path !== '/api/auth/login/') window.dispatchEvent(new Event('beri:session-expired'));
  if (response.status === 204) return undefined as T;
  let data: unknown;
  try {data = await response.json();}
  catch {throw new ApiError('server_error', 'Сервер вернул неожиданный ответ. Попробуйте позже.', {}, response.status);}
  if (!response.ok) {
    const error = record(data) && record(data.error) ? data.error : undefined;
    const detail = record(data) && typeof data.detail === 'string' ? data.detail : undefined;
    const fields = fieldErrors(error?.fields ?? data);
    throw new ApiError(typeof error?.code === 'string' ? error.code : 'server_error',
      typeof error?.message === 'string' ? error.message : detail
        ?? (Object.keys(fields).length ? 'Проверьте введённые данные.' : 'Запрос не выполнен. Попробуйте позже.'), fields, response.status);
  }
  return data as T;
}

export async function ensureCsrf(): Promise<void> {await api('/api/auth/csrf/');}
