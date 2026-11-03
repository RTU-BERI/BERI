import {expect, test} from '@playwright/test';
import type {PickupPoint, ProductAvailability, ProductDetail} from '../src/features/product/types';
import {formatDecimal} from '../src/features/product/format';

// No route interception. Requires real Django + an isolated PostgreSQL with nonempty catalog/PVs.
test('real API: catalog to product, details and exact pickup-point counts', async ({page, request}) => {
  const listResponse = await request.get('/api/products/');
  expect(listResponse.status(), 'Запустите настоящий Django API').toBe(200);
  const products: {id: number; name: string}[] = await listResponse.json();
  expect(products.length, 'Добавьте учебные товары в собственной БД').toBeGreaterThan(0);
  const id = products[0].id;
  const detailResponse = await request.get(`/api/products/${id}/`);
  const pointsResponse = await request.get('/api/pickup-points/');
  const availabilityResponse = await request.get(`/api/products/${id}/availability/`);
  expect(detailResponse.status()).toBe(200);
  expect(pointsResponse.status()).toBe(200);
  expect(availabilityResponse.status()).toBe(200);
  const detail: ProductDetail = await detailResponse.json();
  const points: PickupPoint[] = await pointsResponse.json();
  const stock: ProductAvailability = await availabilityResponse.json();
  expect(points.length, 'Добавьте учебные ПВ для проверки выбора и наличия').toBeGreaterThan(0);
  await page.goto('/catalog');
  await page.locator(`article a[href="/products/${id}"]`).click();
  await expect(page).toHaveURL(new RegExp(`/products/${id}$`));
  await expect(page.getByRole('heading', {name: detail.name, exact: true})).toBeVisible();
  if (detail.description?.trim()) await expect(page.getByText(detail.description, {exact: true})).toBeVisible();
  if (detail.tariff) await expect(page.getByText(`${formatDecimal(detail.tariff.hourly_rate, false)} ₽/час`, {exact: true})).toBeVisible();
  else await expect(page.getByText('Стоимость аренды сейчас недоступна.', {exact: true})).toBeVisible();
  for (const point of points) {
    await page.getByLabel('Выберите пункт выдачи', {exact: true}).selectOption(String(point.id));
    const count = stock.pickup_points.find(value => value.pickup_point_id === point.id)?.available_count;
    await expect(page.getByText(count === undefined ? 'Данные о наличии в этом пункте недоступны.'
      : count === 0 ? 'Нет в наличии' : `Свободных экземпляров: ${count}`, {exact: true})).toBeVisible();
    await expect(page.getByText(point.address, {exact: true})).toBeVisible();
  }
  await page.screenshot({path: 'test-results/product-live-desktop.png', fullPage: true});
  await page.reload();
  await expect(page.getByRole('heading', {name: detail.name, exact: true})).toBeVisible();
});
