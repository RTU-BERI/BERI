export type FieldErrors = Record<string, string[]>;

export class ApiError extends Error {
  constructor(public code: string, message: string, public fields: FieldErrors = {}, public status = 0) {super(message);}
}

function csrfCookie(): string | undefined {
  const item = document.cookie.split('; ').find(value => value.startsWith('csrftoken='));
  return item ? decodeURIComponent(item.slice('csrftoken='.length)) : undefined;
}

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  if (!path.startsWith('/api/')) throw new Error('Only same-origin /api/ paths are supported');
  const unsafe = !['GET', 'HEAD', 'OPTIONS'].includes(method);
  if (unsafe && !csrfCookie()) await ensureCsrf();
  const headers: Record<string, string> = {'Accept': 'application/json'};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (unsafe) headers['X-CSRFToken'] = csrfCookie() ?? '';
  let response: Response;
  try {response = await fetch(path, {method, headers, credentials: 'same-origin', body: body === undefined ? undefined : JSON.stringify(body)});}
  catch {throw new ApiError('network_error', 'Не удалось связаться с сервером. Проверьте соединение и попробуйте снова.');}
  if (response.status === 204) return undefined as T;
  let data: unknown;
  try {data = await response.json();}
  catch {throw new ApiError('server_error', 'Сервер вернул неожиданный ответ. Попробуйте позже.', {}, response.status);}
  if (!response.ok) {
    const payload = data as {error?: {code?: string; message?: string; fields?: FieldErrors}};
    const error = payload.error;
    if (response.status === 401 && path !== '/api/auth/login/') window.dispatchEvent(new Event('beri:session-expired'));
    throw new ApiError(error?.code ?? 'server_error', error?.message ?? 'Запрос не выполнен. Попробуйте позже.', error?.fields, response.status);
  }
  return data as T;
}

export async function ensureCsrf(): Promise<void> {await api('/api/auth/csrf/');}
