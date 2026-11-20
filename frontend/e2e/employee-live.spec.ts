import {readFileSync} from 'node:fs';
import {expect, test} from '@playwright/test';
import type {BrowserContext, Page} from '@playwright/test';

// Реальные Django, cookies, CSRF и PostgreSQL. Фикстура хранится вне Git.
type Fixture = {password: string; users: Record<string, string>; bookings: Record<string, string>};
const fixturePath = process.env.BERI_EMPLOYEE_LIVE_FIXTURE;
test.skip(!fixturePath, 'Для live-проверки задайте BERI_EMPLOYEE_LIVE_FIXTURE с данными отдельной учебной БД.');
const data: Fixture | null = fixturePath ? JSON.parse(readFileSync(fixturePath, 'utf8')) : null;

async function login(context: BrowserContext, role = 'employee') {
  await context.request.get('/api/auth/csrf/');
  const csrf = (await context.cookies()).find(cookie => cookie.name === 'csrftoken')!.value;
  const response = await context.request.post('/api/auth/login/', {
    headers: {'X-CSRFToken': csrf, Origin: 'http://localhost:5173'},
    data: {phone: data!.users[role], password: data!.password},
  });
  expect(response.status()).toBe(200);
}

async function healthy(page: Page) {
  await page.getByLabel('Комплектность проверена и соответствует').check();
  await page.getByLabel('Состояние проверено, проблем нет').check();
  await page.getByRole('button', {name: 'Сохранить положительный осмотр'}).click();
  await expect(page.getByText('Положительный осмотр текущего экземпляра сохранён.')).toBeVisible();
}

async function replacement(page: Page) {
  await page.getByLabel('Обнаружена проблема состояния', {exact: true}).check();
  await page.getByLabel('Комплектность проверена и соответствует').check();
  await page.getByLabel('Описание проблемы').fill('Учебная трещина корпуса');
  await page.getByLabel('Причина замены (до 30 символов)').fill('Повреждение корпуса');
}

async function issue(page: Page) {
  await page.getByLabel('Подтверждаю фактическую передачу товара клиенту').check();
  const responsePromise = page.waitForResponse(response => response.url().endsWith('/issue/') && response.request().method() === 'POST');
  await page.getByRole('button', {name: 'Подтвердить выдачу'}).click();
  const response = await responsePromise;
  expect(response.status()).toBe(201);
  const rental = await response.json();
  expect(rental.status).toBe('active');
  expect(rental.id).toBeTruthy();
  await expect(page.getByRole('heading', {name: 'Аренда началась'})).toBeVisible();
  await expect(page.getByText('Загрузка осмотров…')).toHaveCount(0);
  await expect(page.getByText('Сохраняем результат…')).toHaveCount(0);
  return rental;
}

test('live employee: own point list → healthy inspection → issue → updated list', async ({page, context}) => {
  await login(context);
  await page.goto('/employee/reservations');
  const row = page.getByRole('row').filter({hasText: data!.bookings.healthy});
  await expect(row).toContainText('Действует');
  await expect(page.getByText(data!.bookings.foreign)).toHaveCount(0);
  await expect(page.getByText(data!.users.customer)).toHaveCount(0);
  await row.getByRole('link', {name: 'Открыть бронь'}).click();
  await healthy(page);
  await issue(page);
  await page.screenshot({path: 'test-results/employee-live-issued.png', fullPage: true});
  await page.getByRole('link', {name: 'Перейти к обновлённому списку'}).click();
  await expect(page.getByRole('row').filter({hasText: data!.bookings.healthy})).toContainText('Выдано');
});

test('live employee: automatic replacement → new assignment inspection → issue', async ({page, context}) => {
  await login(context);
  const id = data!.bookings.replace;
  const before = await (await context.request.get(`/api/employee/reservations/${id}/inspections/`)).json();
  await page.goto(`/employee/reservations/${id}`);
  await replacement(page);
  await page.getByRole('button', {name: 'Зафиксировать проблему и заменить'}).click();
  await expect(page.getByText('Проблема зафиксирована.', {exact: false})).toBeVisible();
  await expect(page.getByText('Учебная трещина корпуса', {exact: true})).toBeVisible();
  await healthy(page);
  const after = await (await context.request.get(`/api/employee/reservations/${id}/inspections/`)).json();
  expect(after.expected_assignment_id).not.toBe(before.expected_assignment_id);
  expect(after.assigned_product_item_id).not.toBe(before.assigned_product_item_id);
  expect(after.inspections).toHaveLength(2);
  const rental = await issue(page);
  expect(rental.product_item_id).toBe(after.assigned_product_item_id);
});

test('live employee: no replacement rolls back, explicit consent cancels', async ({page, context}) => {
  await login(context);
  const id = data!.bookings.no_replacement;
  await page.goto(`/employee/reservations/${id}`);
  await replacement(page);
  await page.getByRole('button', {name: 'Зафиксировать проблему и заменить'}).click();
  await expect(page.getByRole('alert')).toContainText('Сервер отклонил данные');
  const unchanged = await (await context.request.get(`/api/employee/reservations/${id}/inspections/`)).json();
  expect(unchanged.reservation_status).toBe('active');
  expect(unchanged.inspections).toHaveLength(0);
  await page.getByRole('button', {name: 'Обновить данные'}).click();
  await replacement(page);
  await page.getByLabel('Если свободной замены нет', {exact: false}).check();
  await page.getByRole('button', {name: 'Зафиксировать проблему и заменить'}).click();
  await expect(page.getByText('Бронь отменена. Дальнейшие операции закрыты.')).toBeVisible();
  const cancelled = await (await context.request.get(`/api/employee/reservations/${id}/inspections/`)).json();
  expect(cancelled.reservation_status).toBe('cancelled');
  expect(cancelled.expected_assignment_id).toBeNull();
  expect(cancelled.inspections).toHaveLength(1);
});

test('live employee: expired TTL, foreign point, unauthenticated and missing permissions', async ({page, context, browser}) => {
  await login(context);
  await page.goto(`/employee/reservations/${data!.bookings.expired}`);
  await page.getByLabel('Комплектность проверена и соответствует').check();
  await page.getByLabel('Состояние проверено, проблем нет').check();
  await page.getByRole('button', {name: 'Сохранить положительный осмотр'}).click();
  await expect(page.getByRole('alert')).toContainText('Состояние изменилось');
  await page.goto(`/employee/reservations/${data!.bookings.foreign}`);
  await expect(page.getByRole('alert')).toContainText('недоступно в вашем пункте выдачи');
  const guest = await browser.newContext({baseURL: 'http://localhost:5173'});
  try {
    const response = await guest.request.get('/api/employee/reservations/');
    expect([401, 403]).toContain(response.status());
    await login(guest, 'restricted');
    const forbidden = await guest.request.get('/api/employee/reservations/');
    expect(forbidden.status()).toBe(403);
    const restrictedPage = await guest.newPage();
    await restrictedPage.goto('/employee/reservations');
    await expect(restrictedPage.getByRole('alert')).toContainText('Доступ запрещён');
  } finally {await guest.close();}
});

test('live employee: colleague replaces assignment, stale POST gets 409, refresh recovers', async ({page, context, browser}) => {
  await login(context);
  const id = data!.bookings.stale;
  await page.goto(`/employee/reservations/${id}`);
  await expect(page.getByText('Осмотров пока нет.')).toBeVisible();
  const colleague = await browser.newContext({baseURL: 'http://localhost:5173'});
  try {
    await login(colleague, 'colleague');
    const before = await (await colleague.request.get(`/api/employee/reservations/${id}/inspections/`)).json();
    const csrf = (await colleague.cookies()).find(cookie => cookie.name === 'csrftoken')!.value;
    const replaced = await colleague.request.post(`/api/employee/reservations/${id}/replace/`, {
      headers: {'X-CSRFToken': csrf, Origin: 'http://localhost:5173'},
      data: {expected_assignment_id: before.expected_assignment_id, condition_ok: false, completeness_ok: true,
        comment: null, problems: [{problem_type: 'damage', description: 'Коллега обнаружил повреждение'}], reason: 'Повреждение корпуса'},
    });
    expect(replaced.status()).toBe(200);
    await page.getByLabel('Комплектность проверена и соответствует').check();
    await page.getByLabel('Состояние проверено, проблем нет').check();
    await page.getByRole('button', {name: 'Сохранить положительный осмотр'}).click();
    await expect(page.getByRole('alert')).toContainText('Состояние изменилось');
    await page.getByRole('button', {name: 'Обновить данные'}).click();
    await healthy(page);
    await issue(page);
  } finally {await colleague.close();}
});
