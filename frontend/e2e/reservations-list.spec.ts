import {expect, test} from '@playwright/test';
import {mockReservations, reservation, reservationId} from './reservations-fixtures';
import {product} from './product-fixtures';

test('mocked: own list displays four statuses, metadata and deduplicated reference requests', async ({page}) => {
  const statuses = ['active', 'cancelled', 'expired', 'fulfilled'] as const;
  const records = statuses.map((status, index) => reservation({id: reservationId.slice(0, -1) + (index + 1), status}));
  await mockReservations(page, records);
  const requests: string[] = [];
  page.on('request', request => {if (['/api/products/', '/api/pickup-points/'].includes(new URL(request.url()).pathname)) requests.push(new URL(request.url()).pathname);});
  await page.goto('/my-reservations');
  await expect(page.getByRole('article')).toHaveCount(4);
  for (const label of ['Активно', 'Отменено', 'Истекло', 'Выполнено']) await expect(page.getByText(`Статус: ${label}`, {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Отменить бронирование', exact: true})).toHaveCount(1);
  await expect(page.locator('.reservation-countdown')).toHaveCount(1);
  await expect(page.getByRole('article').first()).toContainText(product.name);
  await expect(page.getByRole('article').first()).toContainText('Учебный адрес №1');
  expect(requests.filter(path => path === '/api/products/')).toHaveLength(1);
  expect(requests.filter(path => path === '/api/pickup-points/')).toHaveLength(1);
  await expect(page.getByRole('navigation').getByRole('link', {name: 'Мои бронирования'})).toHaveClass('active');
  await expect(page.locator('.reservation-card').first()).toHaveCSS('border-radius', '18px');
  await expect(page.locator('.reservation-card').first()).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({path: 'test-results/my-reservations-desktop.png', fullPage: true});
});

test('mocked: empty history links to catalog', async ({page}) => {
  await mockReservations(page);
  await page.goto('/my-reservations');
  await expect(page.getByRole('heading', {name: 'У вас пока нет бронирований'})).toBeVisible();
  await page.getByRole('link', {name: 'Перейти в каталог'}).click();
  await expect(page).toHaveURL(/\/catalog$/);
});

test('mocked: loading, API error and retry; invalid payload is not an empty list', async ({page}) => {
  await mockReservations(page);
  let release!: () => void;
  const hold = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/api/reservations/', async route => {await hold; await route.fulfill({json: {results: []}});});
  await page.goto('/my-reservations');
  await expect(page.getByText('Загрузка бронирований…', {exact: true})).toBeVisible();
  release();
  await expect(page.getByRole('alert')).toContainText('неожиданный формат');
  await expect(page.getByText('У вас пока нет бронирований')).toHaveCount(0);
  await page.unroute('**/api/reservations/');
  await page.route('**/api/reservations/', route => route.fulfill({json: [reservation()]}));
  await page.getByRole('button', {name: 'Повторить загрузку бронирований'}).click();
  await expect(page.getByRole('article')).toHaveCount(1);
});

test('mocked: missing enrichment and image do not hide reservation', async ({page}) => {
  await mockReservations(page, [reservation()]);
  await page.route('**/api/products/', route => route.fulfill({status: 503, json: {detail: 'Unavailable'}}));
  await page.route('**/api/pickup-points/', route => route.abort());
  await page.goto('/my-reservations');
  const card = page.getByRole('article');
  await expect(card).toContainText('Товар № 1');
  await expect(card).toContainText('Пункт выдачи № 11');
  await expect(card).toContainText(reservationId);
  await expect(card.getByText('Нет изображения')).toBeVisible();
  await expect(card.getByRole('button', {name: 'Отменить бронирование'})).toBeEnabled();
});

for (const employee of [false, true]) test(`mocked: ${employee ? 'employee' : 'anonymous'} cannot load private list`, async ({page}) => {
  await mockReservations(page, [], {employee, anonymous: !employee});
  let requests = 0;
  page.on('request', request => {if (request.url().includes('/api/reservations/')) ++requests;});
  await page.goto('/my-reservations');
  if (employee) await expect(page.getByRole('alert')).toContainText('только клиентам');
  else await expect(page.getByRole('link', {name: 'войдите в аккаунт'})).toHaveAttribute('href', '/login');
  expect(requests).toBe(0);
});

test('mocked: successful 204 cancellation refreshes list, keeps history and has no optimistic success', async ({page}) => {
  await mockReservations(page);
  let records = [reservation()];
  await page.route('**/api/reservations/', route => route.fulfill({json: records}));
  let release!: () => void;
  const hold = new Promise<void>(resolve => {release = resolve;});
  let cancellations = 0;
  await page.route('**/api/reservations/*/cancel/', async route => {
    ++cancellations;
    expect(route.request().method()).toBe('POST');
    expect(route.request().headers()['x-csrftoken']).toBe('synthetic-csrf');
    await hold;
    records = [reservation({status: 'cancelled', cancelled_at: new Date().toISOString(), completed_at: new Date().toISOString()})];
    await route.fulfill({status: 204});
  });
  await page.goto('/my-reservations');
  await page.getByRole('button', {name: 'Отменить бронирование', exact: true}).click();
  await expect(page.getByText('Отменить бронирование?', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Оставить бронирование'}).click();
  expect(cancellations).toBe(0);
  await page.getByRole('button', {name: 'Отменить бронирование', exact: true}).click();
  await page.getByRole('button', {name: 'Отменить бронирование', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Отменяем…'})).toBeDisabled();
  await expect(page.getByText('Статус: Активно', {exact: true})).toBeVisible();
  await expect(page.getByText('Отмена бронирования принята сервером.')).toHaveCount(0);
  release();
  await expect(page.getByText('Статус: Отменено', {exact: true})).toBeVisible();
  await expect(page.getByRole('article')).toHaveCount(1);
  await expect(page.getByRole('button', {name: 'Отменить бронирование', exact: true})).toHaveCount(0);
  expect(cancellations).toBe(1);
});

for (const failure of ['network', 'conflict', 'missing']) test(`mocked: cancel ${failure} keeps server truth and explains failure`, async ({page}) => {
  await mockReservations(page);
  let records = [reservation()];
  let reads = 0;
  await page.route('**/api/reservations/', route => {++reads; return route.fulfill({json: records});});
  await page.route('**/api/reservations/*/cancel/', route => {
    if (failure === 'network') return route.abort();
    records = [reservation({status: 'expired'})];
    return route.fulfill({status: failure === 'missing' ? 404 : 409, json: {detail: 'Срок бронирования истёк.'}});
  });
  await page.goto('/my-reservations');
  await page.getByRole('button', {name: 'Отменить бронирование', exact: true}).click();
  await page.getByRole('button', {name: 'Отменить бронирование', exact: true}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByText('Отмена бронирования принята сервером.')).toHaveCount(0);
  if (failure === 'network') await expect(page.getByText('Статус: Активно')).toBeVisible();
  else {await expect(page.getByText('Статус: Истекло')).toBeVisible(); expect(reads).toBeGreaterThan(1);}
});

test('mocked: countdown ticks and reload uses same expiry, not a new 60 minutes', async ({page}) => {
  await page.clock.install();
  const record = reservation({expires_at: new Date(Date.now() + 5 * 60000).toISOString()});
  await mockReservations(page, [record]);
  await page.goto('/my-reservations');
  const countdown = page.locator('.reservation-countdown');
  await expect(countdown).toContainText(/Осталось: [45]:/);
  const before = await countdown.innerText();
  await page.clock.fastForward(3000);
  await expect(countdown).not.toHaveText(before);
  await page.reload();
  await expect(page.locator('time[datetime]').last()).toHaveAttribute('datetime', record.expires_at);
  await expect(countdown).toContainText(/Осталось: [45]:/);
});

for (const resolved of [false, true]) test(`mocked: zero triggers bounded recheck, server ${resolved ? 'confirms expired' : 'still active'}`, async ({page}) => {
  await page.clock.install();
  const record = reservation({expires_at: new Date(Date.now() - 10000).toISOString()});
  await mockReservations(page);
  let reads = 0;
  await page.route('**/api/reservations/', route => {++reads; return route.fulfill({json: [{...record, status: resolved && reads > 1 ? 'expired' : 'active'}], headers: {Date: new Date().toUTCString()}});});
  await page.goto('/my-reservations');
  if (resolved) await expect(page.getByText('Статус: Истекло')).toBeVisible();
  else {await expect(page.getByText('Статус: Активно')).toBeVisible(); await expect(page.getByText('Время истекло. Ожидаем подтверждения статуса сервером.')).toBeVisible();}
  await page.clock.fastForward(120000);
  expect(reads).toBe(2);
  await expect(page.getByRole('button', {name: 'Отменить бронирование', exact: true})).toHaveCount(0);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect.poll(() => reads).toBe(3);
});

test('mocked: clock skew uses server Date and fallback explicitly uses device time', async ({page}) => {
  await page.clock.install({time: Date.now() + 2 * 60 * 60000});
  await mockReservations(page, [reservation({expires_at: new Date(Date.now() + 15 * 60000).toISOString()})]);
  await page.goto('/my-reservations');
  await expect(page.locator('.reservation-countdown')).toContainText(/Осталось: 1[45]:/);
  await expect(page.locator('.reservation-countdown')).not.toContainText('по часам устройства');
  await page.route('**/api/reservations/', route => route.fulfill({json: [reservation({expires_at: new Date(Date.now() + 3 * 60 * 60000).toISOString()})], headers: {Date: 'invalid'}}));
  await page.getByRole('button', {name: 'Обновить бронирования'}).click();
  await expect(page.locator('.reservation-countdown')).toContainText('по часам устройства');
});

test('mocked: logout and late list response cannot restore private content', async ({page}) => {
  await mockReservations(page);
  let release!: () => void;
  const hold = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/api/reservations/', async route => {await hold; await route.fulfill({json: [reservation()]});});
  await page.goto('/my-reservations');
  await expect(page.getByText('Загрузка бронирований…')).toBeVisible();
  await page.route('**/api/auth/me/', route => route.fulfill({json: {authenticated: false, user: null}}));
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.getByRole('link', {name: 'войдите в аккаунт'})).toBeVisible();
  release();
  await expect(page.getByRole('article')).toHaveCount(0);
  await expect(page.getByText(reservationId, {exact: false})).toHaveCount(0);
});

test('mocked: list 403 after expired session offers login, permissions 403 stays a visible error', async ({page}) => {
  await mockReservations(page);
  await page.route('**/api/reservations/', route => route.fulfill({status: 403, json: {detail: 'Not authenticated.'}}));
  await page.goto('/my-reservations');
  await expect(page.getByRole('alert')).toContainText('Не удалось подтвердить право');
  await expect(page.getByRole('navigation').getByRole('link', {name: 'Мой аккаунт'})).toBeVisible();
  await page.route('**/api/auth/me/', route => route.fulfill({json: {authenticated: false, user: null}}));
  await page.getByRole('button', {name: 'Повторить загрузку бронирований'}).click();
  await expect(page.getByRole('link', {name: 'войдите в аккаунт'})).toBeVisible();
});

test('mocked: cancellation confirmation is keyboard accessible', async ({page}) => {
  await mockReservations(page, [reservation()]);
  await page.goto('/my-reservations');
  const cancel = page.getByRole('button', {name: 'Отменить бронирование', exact: true});
  await cancel.focus(); await page.keyboard.press('Enter');
  // Opening confirmation must place focus on the safe action.
  await expect(page.getByRole('button', {name: 'Оставить бронирование'})).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(cancel).toBeVisible();
});

test('mocked: unmounted countdown cannot recheck a reservation after leaving history', async ({page}) => {
  await page.clock.install();
  await mockReservations(page, [reservation({expires_at: new Date(Date.now() + 5000).toISOString()})]);
  let reads = 0;
  page.on('request', request => {if (new URL(request.url()).pathname === '/api/reservations/') ++reads;});
  await page.goto('/my-reservations');
  await expect(page.getByRole('article')).toHaveCount(1);
  await page.getByRole('navigation').getByRole('link', {name: 'Каталог', exact: true}).click();
  const before = reads;
  await page.clock.fastForward(120000);
  await expect(page.getByRole('heading', {name: 'Каталог товаров'})).toBeVisible();
  expect(reads).toBe(before);
});
