import {expect, test} from '@playwright/test';
import type {Page} from '@playwright/test';

const bookingId = '89258494-44bb-4ead-a4d1-2fec836b0411';
const employee = {id: '59e3321d-8f5d-4170-8f98-086bf4441061', account_type: 'employee', phone: 'private-phone', phone_verified: true};
const listUrl = '/employee/reservations';
const detailUrl = `${listUrl}/${bookingId}`;

async function fixture(page: Page) {
  const state = {
    reservation_status: 'active', assigned_product_item_id: 11 as number | null,
    expected_assignment_id: 71 as number | null,
    inspections: [] as {id: number; product_item_id: number; condition_ok: boolean; completeness_ok: boolean; comment: string | null; inspected_at: string; problems: {problem_type: string; description: string}[]}[],
    posts: [] as {path: string; data: Record<string, unknown>}[],
    noReplacement: false,
  };
  await page.route('**/api/auth/me/', route => route.fulfill({json: {authenticated: true, user: employee}}));
  await page.route('**/api/auth/csrf/', route => route.fulfill({json: {detail: 'csrf_ready'}}));
  await page.route('**/api/employee/reservations/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'GET') {
      if (path === '/api/employee/reservations/') {
        return route.fulfill({json: [{id: bookingId, product_id: 5, pickup_point_id: 3,
          assigned_product_item_id: state.assigned_product_item_id, status: state.reservation_status,
          expires_at: '2026-12-01T12:00:00Z', customer_phone: 'DO-NOT-DISPLAY-CUSTOMER'}]});
      }
      return route.fulfill({json: {reservation_id: bookingId, expires_at: '2026-12-01T12:00:00Z', ...state}});
    }
    const data = request.postDataJSON();
    state.posts.push({path, data});
    if (path.endsWith('/replace/') && state.noReplacement && !data.completion_reason) {
      return route.fulfill({status: 400, json: {completion_reason: ['Для отмены необходима явная причина.']}});
    }
    if (path.endsWith('/issue/')) {
      state.reservation_status = 'fulfilled';
      state.expected_assignment_id = null;
      return route.fulfill({status: 201, json: {id: '84a864e4-0229-4090-a55a-a6e13aa04bb4', status: 'active',
        started_at: '2026-11-20T12:00:00Z', payment_method_id: 'DO-NOT-DISPLAY-PAYMENT'}});
    }
    state.inspections.push({id: state.inspections.length + 1, product_item_id: state.assigned_product_item_id!,
      condition_ok: data.condition_ok, completeness_ok: data.completeness_ok,
      comment: data.comment, problems: data.problems, inspected_at: '2026-11-20T11:59:00Z'});
    if (path.endsWith('/replace/')) {
      state.assigned_product_item_id = state.noReplacement ? null : 12;
      state.expected_assignment_id = state.noReplacement ? null : 72;
      if (state.noReplacement) state.reservation_status = 'cancelled';
    }
    return route.fulfill({json: {reservation_status: state.reservation_status}});
  });
  return state;
}

async function inspect(page: Page) {
  await page.getByLabel('Комплектность проверена и соответствует').check();
  await page.getByLabel('Состояние проверено, проблем нет').check();
  await page.getByRole('button', {name: 'Сохранить положительный осмотр'}).click();
  await expect(page.getByText('Положительный осмотр текущего экземпляра сохранён.')).toBeVisible();
}

async function problem(page: Page) {
  await page.getByLabel('Обнаружена проблема состояния', {exact: true}).check();
  await page.getByLabel('Комплектность проверена и соответствует').check();
  await page.getByLabel('Описание проблемы').fill('Трещина корпуса');
  await page.getByLabel('Причина замены (до 30 символов)').fill('Повреждение корпуса');
}

test('mocked employee: loading, list, status, no customer data and history', async ({page}) => {
  await fixture(page);
  let release!: () => void;
  const pending = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/api/employee/reservations/', async route => {await pending; await route.fallback();});
  await page.goto(listUrl);
  await expect(page.getByText('Загрузка бронирований…')).toBeVisible();
  release();
  await expect(page.getByRole('cell', {name: 'Действует', exact: true})).toBeVisible();
  await expect(page.getByText('DO-NOT-DISPLAY-CUSTOMER')).toHaveCount(0);
  await page.getByRole('link', {name: 'Открыть бронь'}).click();
  await expect(page.getByText('Осмотров пока нет.')).toBeVisible();
  await expect(page.getByRole('button', {name: 'Подтвердить выдачу'})).toHaveCount(0);
});

test('mocked employee: empty and failed list can be refreshed', async ({page}) => {
  await fixture(page);
  await page.route('**/api/employee/reservations/', route => route.fulfill({json: []}));
  await page.goto(listUrl);
  await expect(page.getByText('Бронирований пока нет.')).toBeVisible();
  await page.route('**/api/employee/reservations/', route => route.fulfill({status: 503, json: {}}));
  await page.getByRole('button', {name: 'Обновить список'}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByText('Бронирований пока нет.')).toHaveCount(0);
  await page.unroute('**/api/employee/reservations/');
  await page.getByRole('button', {name: 'Обновить список'}).click();
  await expect(page.getByRole('link', {name: 'Открыть бронь'})).toBeVisible();
});

test('mocked employee: positive inspection, handover, updated list and double-click guard', async ({page}) => {
  const state = await fixture(page);
  await page.goto(detailUrl);
  await inspect(page);
  expect(state.posts[0].data).toEqual({expected_assignment_id: 71, condition_ok: true, completeness_ok: true, comment: null, problems: []});
  const issueButton = page.getByRole('button', {name: 'Подтвердить выдачу'});
  await expect(issueButton).toBeDisabled();
  await page.getByLabel('Подтверждаю фактическую передачу товара клиенту').check();
  let release!: () => void;
  const pending = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/issue/', async route => {await pending; await route.fallback();});
  await issueButton.evaluate(button => {(button as HTMLButtonElement).click(); (button as HTMLButtonElement).click();});
  await expect(page.getByText('Сохраняем результат…')).toBeVisible();
  await expect(issueButton).toBeDisabled();
  release();
  await expect(page.getByRole('heading', {name: 'Аренда началась'})).toBeVisible();
  expect(state.posts.filter(row => row.path.endsWith('/issue/'))).toHaveLength(1);
  expect(state.posts[1].data).toEqual({expected_assignment_id: 71});
  await expect(page.getByText('DO-NOT-DISPLAY-PAYMENT')).toHaveCount(0);
  await page.getByRole('link', {name: 'Перейти к обновлённому списку'}).click();
  await expect(page.getByRole('cell', {name: 'Выдано', exact: true})).toBeVisible();
});

test('mocked employee: automatic replacement, new inspection and new assignment for issue', async ({page}) => {
  const state = await fixture(page);
  await page.goto(detailUrl);
  await problem(page);
  await page.getByRole('button', {name: 'Зафиксировать проблему и заменить'}).click();
  await expect(page.getByRole('definition').filter({hasText: '№12'})).toBeVisible();
  expect(state.posts[0].data).toEqual({expected_assignment_id: 71, condition_ok: false, completeness_ok: true,
    comment: null, problems: [{problem_type: 'damage', description: 'Трещина корпуса'}], reason: 'Повреждение корпуса'});
  await expect(page.getByRole('button', {name: 'Подтвердить выдачу'})).toHaveCount(0);
  await inspect(page);
  await page.getByLabel('Подтверждаю фактическую передачу товара клиенту').check();
  await page.getByRole('button', {name: 'Подтвердить выдачу'}).click();
  await expect(page.getByRole('heading', {name: 'Аренда началась'})).toBeVisible();
  expect(state.posts[1].data.expected_assignment_id).toBe(72);
  expect(state.posts[2].data).toEqual({expected_assignment_id: 72});
  await expect(page.getByText('Трещина корпуса', {exact: true})).toBeVisible();
});

test('mocked employee: no replacement needs explicit cancellation consent', async ({page}) => {
  const state = await fixture(page);
  state.noReplacement = true;
  await page.goto(detailUrl);
  await problem(page);
  await page.getByRole('button', {name: 'Зафиксировать проблему и заменить'}).click();
  await expect(page.getByRole('alert')).toContainText('Сервер отклонил данные');
  await expect(page.getByRole('button', {name: 'Зафиксировать проблему и заменить'})).toBeDisabled();
  expect(state.reservation_status).toBe('active');
  expect(state.inspections).toHaveLength(0);
  await page.getByRole('button', {name: 'Обновить данные'}).click();
  await problem(page);
  await page.getByLabel('Если свободной замены нет', {exact: false}).check();
  await page.getByRole('button', {name: 'Зафиксировать проблему и заменить'}).click();
  await expect(page.getByText('Бронь отменена. Дальнейшие операции закрыты.')).toBeVisible();
  expect(state.posts[1].data.completion_reason).toBe('Нет свободной замены');
});

test('mocked employee: stale assignment 409 requires GET before another write', async ({page}) => {
  const state = await fixture(page);
  await page.goto(detailUrl);
  await page.route('**/inspections/', async route => {
    if (route.request().method() !== 'POST') return route.fallback();
    state.expected_assignment_id = 72;
    state.assigned_product_item_id = 12;
    return route.fulfill({status: 409, json: {detail: 'Stale assignment'}});
  });
  await page.getByLabel('Комплектность проверена и соответствует').check();
  await page.getByLabel('Состояние проверено, проблем нет').check();
  await page.getByRole('button', {name: 'Сохранить положительный осмотр'}).click();
  await expect(page.getByRole('alert')).toContainText('Состояние изменилось');
  await expect(page.getByRole('button', {name: 'Сохранить положительный осмотр'})).toBeDisabled();
  await page.unroute('**/inspections/');
  await page.getByRole('button', {name: 'Обновить данные'}).click();
  await inspect(page);
  expect(state.posts[0].data.expected_assignment_id).toBe(72);
});

for (const status of [400, 401, 403, 404, 409]) {
  test(`mocked employee: HTTP ${status} is explained`, async ({page}) => {
    await fixture(page);
    await page.route('**/inspections/', route => route.fulfill({status, json: {detail: 'server refusal'}}));
    await page.goto(detailUrl);
    await expect(page.getByRole('alert')).toContainText(status === 404 ? 'не найдено' : status === 409 ? 'Состояние изменилось' : status === 400 ? 'Сервер отклонил данные' : 'Доступ запрещён');
    await expect(page.getByRole('button', {name: 'Сохранить положительный осмотр'})).toHaveCount(0);
  });
}

test('mocked employee: lost POST response is reconciled by GET with no automatic POST retry', async ({page}) => {
  const state = await fixture(page);
  let attempts = 0;
  await page.route('**/inspections/', async route => {
    if (route.request().method() !== 'POST') return route.fallback();
    attempts++;
    state.inspections.push({id: 1, product_item_id: 11, condition_ok: true, completeness_ok: true,
      comment: null, inspected_at: '2026-11-20T12:00:00Z', problems: []});
    return route.abort();
  });
  await page.goto(detailUrl);
  await page.getByLabel('Комплектность проверена и соответствует').check();
  await page.getByLabel('Состояние проверено, проблем нет').check();
  await page.getByRole('button', {name: 'Сохранить положительный осмотр'}).click();
  await expect(page.getByRole('alert')).toContainText('мог сохраниться');
  await expect(page.getByRole('button', {name: 'Сохранить положительный осмотр'})).toBeDisabled();
  await page.getByRole('button', {name: 'Обновить данные'}).click();
  await expect(page.getByText('Положительный осмотр текущего экземпляра сохранён.')).toBeVisible();
  expect(attempts).toBe(1);
  await page.route('**/issue/', route => {
    state.reservation_status = 'fulfilled';
    state.expected_assignment_id = null;
    return route.abort();
  });
  await page.getByLabel('Подтверждаю фактическую передачу товара клиенту').check();
  await page.getByRole('button', {name: 'Подтвердить выдачу'}).click();
  await expect(page.getByRole('alert')).toContainText('мог сохраниться');
  await page.getByRole('button', {name: 'Обновить данные'}).click();
  await expect(page.getByText('Выдача уже оформлена.')).toBeVisible();
  await expect(page.getByRole('button', {name: 'Подтвердить выдачу'})).toHaveCount(0);
});

test('mocked employee: terminal history has no write actions', async ({page}) => {
  const state = await fixture(page);
  state.reservation_status = 'expired';
  state.expected_assignment_id = null;
  await page.goto(detailUrl);
  await expect(page.getByText('Срок бронирования истёк.')).toBeVisible();
  await expect(page.getByRole('button', {name: 'Сохранить положительный осмотр'})).toHaveCount(0);
  expect(state.posts).toHaveLength(0);
});

test('mocked employee: anonymous and customer accounts cannot mount the work screen', async ({page}) => {
  const state = await fixture(page);
  await page.route('**/api/auth/me/', route => route.fulfill({json: {authenticated: false, user: null}}));
  await page.goto(listUrl);
  await expect(page.getByRole('heading', {name: 'Войдите как сотрудник'})).toBeVisible();
  await page.route('**/api/auth/me/', route => route.fulfill({json: {authenticated: true, user: {...employee, account_type: 'customer'}}}));
  await page.reload();
  await expect(page.getByRole('heading', {name: 'Доступ только сотрудникам'})).toBeVisible();
  expect(state.posts).toHaveLength(0);
});
