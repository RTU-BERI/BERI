import {expect, test} from '@playwright/test';
import type {CreateReservationInput, Reservation} from '../src/features/reservations/types';
import type {ProductDetail, ProductAvailability} from '../src/features/product/types';

// Explicit opt-in only AFTER the agreed backend is merged, on an isolated test DB.
// No route interception, backend source copying, migrations or implicit test seeding.
test('live reservations: session, CSRF, create, same-UUID retry, own history and cancellation', async ({page, context}) => {
  test.skip(process.env.BERI_RESERVATIONS_LIVE !== '1', 'Backend #15/#16 integration and an isolated live environment are required.');
  expect(process.env.BERI_LIVE_ISOLATED, 'Explicitly confirm your own isolated PostgreSQL environment').toBe('1');
  const phone = process.env.BERI_RESERVATIONS_PHONE;
  const password = process.env.BERI_RESERVATIONS_PASSWORD;
  expect(phone, 'Provide a synthetic customer in your isolated DB').toBeTruthy();
  expect(password, 'Provide the test password via environment, never in Git').toBeTruthy();
  const listResponse = await context.request.get('/api/products/');
  expect(listResponse.status()).toBe(200);
  const products: {id: number}[] = await listResponse.json();
  let target: {product: ProductDetail; pointId: number} | undefined;
  for (const item of products) {
    const detailResponse = await context.request.get(`/api/products/${item.id}/`);
    const stockResponse = await context.request.get(`/api/products/${item.id}/availability/`);
    expect(detailResponse.ok()).toBeTruthy(); expect(stockResponse.ok()).toBeTruthy();
    const product: ProductDetail = await detailResponse.json();
    const stock: ProductAvailability = await stockResponse.json();
    const point = stock.pickup_points.find(value => value.available_count > 0);
    if (product.tariff && point) {target = {product, pointId: point.pickup_point_id}; break;}
  }
  expect(target, 'Prepare a product with tariff and a free item in your isolated DB').toBeTruthy();
  await page.goto('/login');
  await page.getByLabel('Телефон', {exact: true}).fill(phone!);
  await page.getByLabel('Пароль', {exact: true}).fill(password!);
  await page.getByRole('button', {name: 'Войти', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Вы вошли'})).toBeVisible();
  expect((await context.cookies()).find(cookie => cookie.name === 'sessionid')?.httpOnly).toBeTruthy();
  await page.goto(`/products/${target!.product.id}?pickup_point=${target!.pointId}`);
  await page.getByRole('button', {name: 'Забронировать', exact: true}).click();
  const createdResponse = page.waitForResponse(response => response.url().endsWith('/api/reservations/') && response.request().method() === 'POST');
  await page.getByRole('button', {name: 'Подтвердить бронирование'}).click();
  const response = await createdResponse;
  expect(response.status()).toBe(201);
  const original: Reservation = await response.json();
  const input: CreateReservationInput = response.request().postDataJSON();
  await expect(page.getByRole('heading', {name: 'Товар забронирован'})).toBeVisible();
  const csrf = (await context.cookies()).find(cookie => cookie.name === 'csrftoken')!.value;
  const repeated = await context.request.post('/api/reservations/', {data: input, headers: {'X-CSRFToken': csrf}});
  expect(repeated.status()).toBe(201);
  const retried: Reservation = await repeated.json();
  expect(retried.id).toBe(original.id); expect(retried.expires_at).toBe(original.expires_at);
  await page.getByRole('navigation').getByRole('link', {name: 'Мои бронирования'}).click();
  const card = page.getByRole('article', {name: `Бронирование ${original.id}`, exact: true});
  await expect(card).toHaveCount(1);
  await card.getByRole('button', {name: 'Отменить бронирование', exact: true}).click();
  await card.getByRole('button', {name: 'Отменить бронирование', exact: true}).click();
  await expect(card.getByText('Статус: Отменено', {exact: true})).toBeVisible();
});
