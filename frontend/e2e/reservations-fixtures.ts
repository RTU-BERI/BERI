import {expect} from '@playwright/test';
import type {Page} from '@playwright/test';
import {mockProductApi, product} from './product-fixtures';
import type {Reservation} from '../src/features/reservations/types';

export const customer = {id: 'd294c03e-d99e-4a29-a7bc-62c0b3bb0818', phone: '+12025550188', account_type: 'customer', phone_verified: true};
export const reservationId = '10ac1849-631e-468f-8f9e-74a5423aa001';
export function reservation(overrides: Partial<Reservation> = {}): Reservation {
  return {id: reservationId, product_id: 1, pickup_point_id: 11, assigned_product_item_id: 101,
    status: 'active', created_at: new Date(Date.now() - 30 * 60000).toISOString(),
    expires_at: new Date(Date.now() + 30 * 60000).toISOString(), cancelled_at: null, completed_at: null, ...overrides};
}
export async function mockReservations(page: Page, records: Reservation[] = [], options: {anonymous?: boolean; employee?: boolean} = {}) {
  await mockProductApi(page);
  await page.route('**/api/auth/me/', route => route.fulfill({json: options.anonymous ? {authenticated: false, user: null}
    : {authenticated: true, user: {...customer, account_type: options.employee ? 'employee' : 'customer'}}}));
  await page.route('**/api/auth/csrf/', async route => {
    await page.context().addCookies([{name: 'csrftoken', value: 'synthetic-csrf', url: 'http://localhost:5173'}]);
    await route.fulfill({json: {detail: 'csrf_ready'}});
  });
  await page.route('**/api/reservations/', route => route.request().method() === 'GET'
    ? route.fulfill({json: records, headers: {Date: new Date().toUTCString()}})
    : route.fulfill({status: 201, json: reservation({...route.request().postDataJSON(), id: route.request().postDataJSON().request_id}), headers: {Date: new Date().toUTCString()}}));
}
export async function openConfirmation(page: Page, point = '11') {
  await page.goto('/products/1');
  await expect(page.getByRole('heading', {name: product.name, exact: true})).toBeVisible();
  await page.getByLabel('Выберите пункт выдачи', {exact: true}).selectOption(point);
  await page.getByRole('button', {name: 'Забронировать', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Подтвердите бронирование'})).toBeVisible();
}
