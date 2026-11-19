import {expect, test} from '@playwright/test';
import {mockReservations} from './reservations-fixtures';

test.beforeEach(async ({page}) => {await mockReservations(page, [], {anonymous: true}); await page.goto('/login');});

for (const variant of ['auth', 'detail', 'fields', 'html401', '204', '403', 'network']) test(`mocked: shared client regression ${variant}`, async ({page}) => {
  await page.route('**/api/client-regression/', route => variant === 'network' ? route.abort() : route.fulfill(
    variant === 'auth' ? {status: 400, json: {error: {code: 'validation_error', message: 'Проверьте данные', fields: {phone: ['invalid_phone']}}}}
      : variant === 'detail' ? {status: 409, json: {detail: 'UUID запроса уже использован.'}}
      : variant === 'fields' ? {status: 400, json: {request_id: ['Must be a valid UUID.']}}
      : variant === 'html401' ? {status: 401, contentType: 'text/html', body: '<h1>Unauthorized</h1>'}
      : variant === '204' ? {status: 204}
      : {status: 403, json: {detail: 'Permission denied.'}}));
  const result = await page.evaluate(async () => {
    const modulePath = '/src/shared/api/client.ts'; const {api} = await import(modulePath);
    let expired = 0; const listener = () => ++expired; window.addEventListener('beri:session-expired', listener);
    try {const data = await api('/api/client-regression/'); return {ok: data === undefined, expired, status: 0, code: '', message: '', fields: {}};}
    catch (error) {const e = error as {status: number; code: string; message: string; fields: Record<string, string[]>}; return {...e, message: e.message, ok: false, expired};}
    finally {window.removeEventListener('beri:session-expired', listener);}
  });
  if (variant === 'auth') {expect(result.code).toBe('validation_error'); expect(result.fields).toEqual({phone: ['invalid_phone']});}
  if (variant === 'detail') expect(result.message).toBe('UUID запроса уже использован.');
  if (variant === 'fields') expect(result.fields).toEqual({request_id: ['Must be a valid UUID.']});
  if (variant === 'html401') {expect(result.status).toBe(401); expect(result.expired).toBe(1);}
  if (variant === '204') expect(result.ok).toBeTruthy();
  if (variant === '403') {expect(result.status).toBe(403); expect(result.expired).toBe(0);}
  if (variant === 'network') expect(result.code).toBe('network_error');
});

test('mocked: login failure does not expire a session; non-API paths are refused', async ({page}) => {
  await page.route('**/api/auth/login/', route => route.fulfill({status: 401, json: {error: {code: 'invalid_credentials', message: 'Неверные данные'}}}));
  const result = await page.evaluate(async () => {
    const modulePath = '/src/shared/api/client.ts'; const {api} = await import(modulePath);
    let expired = 0; window.addEventListener('beri:session-expired', () => ++expired);
    let code = ''; let refused = false;
    try {await api('/api/auth/login/', 'POST', {});} catch (e) {code = (e as {code: string}).code;}
    try {await api('https://example.com/api/');} catch {refused = true;}
    return {expired, code, refused};
  });
  expect(result).toEqual({expired: 0, code: 'invalid_credentials', refused: true});
});
