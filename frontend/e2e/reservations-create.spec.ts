import {expect, test} from '@playwright/test';
import {customer, mockReservations, openConfirmation, reservation} from './reservations-fixtures';
import {pickupPoints, product} from './product-fixtures';

test('mocked: create uses shared pickup, session/CSRF, server deadline and refreshes real availability', async ({page}) => {
  await mockReservations(page);
  const expires = new Date(Date.now() + 12 * 60000).toISOString();
  let stockRequests = 0;
  page.on('request', request => {if (request.url().endsWith('/availability/')) ++stockRequests;});
  let payload: Record<string, unknown> = {};
  await page.route('**/api/reservations/', async route => {
    payload = route.request().postDataJSON();
    expect(route.request().headers()['x-csrftoken']).toBe('synthetic-csrf');
    await route.fulfill({status: 201, headers: {Date: new Date().toUTCString()}, json: reservation({id: String(payload.request_id), expires_at: expires})});
  });
  await openConfirmation(page);
  const before = stockRequests;
  await expect(page.locator('select')).toHaveCount(1);
  await expect(page.locator('.reservation-confirmation')).toContainText('125,50 ₽/час');
  await expect(page.locator('.reservation-confirmation')).toContainText('не продолжительность аренды');
  await page.getByRole('button', {name: 'Подтвердить бронирование'}).click();
  await expect(page.getByRole('heading', {name: 'Товар забронирован'})).toBeVisible();
  expect(payload).toMatchObject({product_id: 1, pickup_point_id: 11});
  expect(Object.keys(payload).sort()).toEqual(['pickup_point_id', 'product_id', 'request_id']);
  expect(payload.request_id).toMatch(/^[\da-f-]{36}$/);
  await expect(page.locator('.reservation-result time[datetime]')).toHaveAttribute('datetime', expires);
  await expect(page.locator('.reservation-countdown')).toContainText(/Осталось: 1[12]:/);
  expect(stockRequests).toBeGreaterThan(before);
  await expect(page.locator('.reservation-result').getByRole('link', {name: 'Мои бронирования'})).toHaveAttribute('href', '/my-reservations');
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  await page.screenshot({path: 'test-results/reservation-success-desktop.png', fullPage: true});
});

test('mocked: anonymous login restores product and pickup; normal auth uses existing session', async ({page}) => {
  await mockReservations(page, [], {anonymous: true});
  await page.route('**/api/auth/login/', route => route.fulfill({json: {authenticated: true, user: customer}}));
  await page.goto('/products/1');
  await page.getByLabel('Выберите пункт выдачи', {exact: true}).selectOption('22');
  await page.getByRole('link', {name: 'войдите в аккаунт'}).click();
  await page.getByLabel('Телефон', {exact: true}).fill(customer.phone);
  await page.getByLabel('Пароль', {exact: true}).fill('Synthetic-password9!');
  await page.getByRole('button', {name: 'Войти', exact: true}).click();
  await expect(page).toHaveURL(/\/products\/1\?pickup_point=22$/);
  await expect(page.getByLabel('Выберите пункт выдачи', {exact: true})).toHaveValue('22');
  await expect(page.getByRole('button', {name: 'Забронировать', exact: true})).toBeEnabled();
});

test('mocked: missing pickup and tariff cannot be confirmed; employee has no customer action', async ({page}) => {
  await mockReservations(page);
  await page.goto('/products/1');
  await expect(page.getByRole('button', {name: 'Забронировать', exact: true})).toBeDisabled();
  await page.route('**/api/products/1/', route => route.fulfill({json: {...product, tariff: null}}));
  await page.reload();
  await page.getByLabel('Выберите пункт выдачи', {exact: true}).selectOption('11');
  await expect(page.getByRole('button', {name: 'Забронировать', exact: true})).toBeDisabled();
  await expect(page.getByText('Бронирование недоступно: действующий тариф не указан.')).toBeVisible();
  await page.route('**/api/auth/me/', route => route.fulfill({json: {authenticated: true, user: {...customer, account_type: 'employee'}}}));
  await page.reload();
  await expect(page.getByRole('alert')).toContainText('только клиентам');
  await expect(page.getByRole('button', {name: 'Забронировать', exact: true})).toHaveCount(0);
});

for (const [detail, expected] of [
  ['В этом пункте выдачи нет свободных экземпляров.', 'К сожалению, товар уже забронирован другим пользователем'],
  ['Экземпляр уже закреплён за другой бронью.', 'К сожалению, товар уже забронирован другим пользователем'],
  ['UUID запроса уже использован.', 'UUID запроса уже использован.'],
  ['Срок бронирования истёк.', 'Срок бронирования истёк.'],
]) test(`mocked: 409 preserves actual conflict: ${detail}`, async ({page}) => {
  await mockReservations(page);
  await page.route('**/api/reservations/', route => route.fulfill({status: 409, json: {detail}}));
  await openConfirmation(page);
  await page.getByRole('button', {name: 'Подтвердить бронирование'}).click();
  await expect(page.getByRole('alert')).toContainText(expected);
  if (!detail.includes('экземпляр') && !detail.includes('Экземпляр')) await expect(page.getByRole('alert')).not.toContainText('другим пользователем');
  await expect(page.getByRole('heading', {name: 'Товар забронирован'})).toHaveCount(0);
});

for (const failure of ['network', 'server', 'malformed']) test(`mocked: ${failure} retry retains UUID, blocks duplicates and new operation gets new UUID`, async ({page}) => {
  await mockReservations(page);
  const ids: string[] = [];
  let release!: () => void;
  const hold = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/api/reservations/', async route => {
    const body = route.request().postDataJSON(); ids.push(body.request_id);
    if (ids.length === 1) return failure === 'network' ? route.abort()
      : route.fulfill(failure === 'server' ? {status: 503, json: {detail: 'Unavailable'}} : {status: 201, json: {id: 'broken'}});
    if (ids.length === 2) await hold;
    await route.fulfill({status: 201, json: reservation({id: body.request_id}), headers: {Date: new Date().toUTCString()}});
  });
  await openConfirmation(page);
  await page.getByRole('button', {name: 'Подтвердить бронирование'}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await page.getByRole('button', {name: 'Повторить эту операцию'}).click();
  await expect(page.getByRole('button', {name: 'Бронируем…'})).toBeDisabled();
  await expect(page.getByLabel('Выберите пункт выдачи', {exact: true})).toBeDisabled();
  expect(ids).toHaveLength(2); expect(ids[0]).toBe(ids[1]);
  release();
  await expect(page.getByRole('heading', {name: 'Товар забронирован'})).toBeVisible();
  await page.getByRole('button', {name: 'Создать ещё одно бронирование'}).click();
  await page.getByRole('button', {name: 'Подтвердить бронирование'}).click();
  await expect(page.getByRole('heading', {name: 'Товар забронирован'})).toBeVisible();
  expect(ids).toHaveLength(3); expect(ids[2]).not.toBe(ids[0]);
});

test('mocked: change pickup before send; uncertain operation survives route changes and retains its target', async ({page}) => {
  await mockReservations(page);
  const bodies: {request_id: string; pickup_point_id: number}[] = [];
  await page.route('**/api/reservations/', route => {bodies.push(route.request().postDataJSON()); return route.abort();});
  await openConfirmation(page);
  await page.getByLabel('Выберите пункт выдачи', {exact: true}).selectOption('22');
  await expect(page.locator('.reservation-confirmation')).toContainText(pickupPoints[1].name);
  await page.getByRole('button', {name: 'Подтвердить бронирование'}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  expect(bodies[0].pickup_point_id).toBe(22);
  await page.getByRole('link', {name: '← В каталог'}).click();
  await page.getByRole('link', {name: product.name, exact: true}).click();
  await expect(page.getByText(/Результат предыдущей операции ещё не подтверждён/)).toBeVisible();
  await page.getByRole('link', {name: 'Вернуться к предыдущей операции'}).click();
  await page.getByRole('button', {name: 'Повторить эту операцию'}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  expect(bodies).toHaveLength(2); expect(bodies[1]).toEqual(bodies[0]);
});

test('mocked: idempotent response may already be cancelled', async ({page}) => {
  await mockReservations(page);
  await page.route('**/api/reservations/', route => route.fulfill({status: 201, json: reservation({
    id: route.request().postDataJSON().request_id, status: 'cancelled', cancelled_at: new Date().toISOString(), completed_at: new Date().toISOString(),
  })}));
  await openConfirmation(page);
  await page.getByRole('button', {name: 'Подтвердить бронирование'}).click();
  await expect(page.getByRole('heading', {name: 'Бронирование: Отменено'})).toBeVisible();
  await expect(page.getByRole('heading', {name: 'Товар забронирован'})).toHaveCount(0);
  await expect(page.locator('.reservation-countdown')).toHaveCount(0);
});

test('mocked: session 403 refreshes me, but genuine permission/CSRF 403 does not log out a customer', async ({page}) => {
  await mockReservations(page);
  await page.route('**/api/reservations/', route => route.fulfill({status: 403, json: {detail: 'CSRF Failed: test'}}));
  await openConfirmation(page);
  await page.getByRole('button', {name: 'Подтвердить бронирование'}).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось подтвердить право');
  await expect(page.getByRole('navigation').getByRole('link', {name: 'Мой аккаунт'})).toBeVisible();
  await page.route('**/api/auth/me/', route => route.fulfill({json: {authenticated: false, user: null}}));
  await page.getByRole('button', {name: 'Подтвердить бронирование'}).click();
  await expect(page.getByRole('link', {name: 'войдите в аккаунт'})).toBeVisible();
});

test('mocked: keyboard confirmation and existing button/select styles', async ({page}) => {
  await mockReservations(page);
  await page.route('**/api/pickup-points/', route => route.fulfill({json: pickupPoints.map((point, index) => ({...point, name: `Point ${index + 1}`}))}));
  await page.goto('/products/1');
  const select = page.getByLabel('Выберите пункт выдачи', {exact: true});
  await expect(select).toBeEnabled();
  await select.focus(); await page.keyboard.press('p');
  await expect(select).toHaveValue('11');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', {name: 'Забронировать', exact: true})).toBeFocused();
  await page.keyboard.press('Enter');
  const confirm = page.getByRole('button', {name: 'Подтвердить бронирование'});
  await expect(confirm).toBeFocused();
  await expect(confirm).toHaveCSS('background-color', 'rgb(35, 76, 63)');
  await expect(confirm).toHaveCSS('border-radius', '9px');
  await expect(select).toHaveCSS('background-color', 'rgb(250, 251, 248)');
  await expect(page.locator('.product-page__summary > .product-page__panel')).toHaveCSS('border-radius', '18px');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({path: 'test-results/reservation-confirm-desktop.png', fullPage: true});
});

test('mocked: login ignores external return URL and keeps existing account page', async ({page}) => {
  await mockReservations(page, [], {anonymous: true});
  await page.route('**/api/auth/login/', route => route.fulfill({json: {authenticated: true, user: customer}}));
  await page.addInitScript(() => {if (location.pathname === '/login') history.replaceState({usr: {returnTo: 'https://example.com/'}, key: 'test', idx: 0}, '', location.href);});
  await page.goto('/login');
  await page.getByLabel('Телефон', {exact: true}).fill(customer.phone);
  await page.getByLabel('Пароль', {exact: true}).fill('Synthetic-password9!');
  await page.getByRole('button', {name: 'Войти', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Вы вошли'})).toBeVisible();
  await expect(page).toHaveURL('http://localhost:5173/login');
});
