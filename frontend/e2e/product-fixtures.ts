import type {Page} from '@playwright/test';
import type {PickupPoint, ProductAvailability, ProductDetail} from '../src/features/product/types';

// Synthetic responses are used ONLY by mocked UI tests, never by the application.
export const product: ProductDetail = {
  id: 1, name: 'Учебная дрель', category: {id: 1, name: 'Инструменты'}, status: 'fixture-status',
  description: 'Учебное описание товара.\nВторая строка описания.', image_url: null, brand: 'Учебный бренд', model: 'Д-1',
  attributes: [
    {name: 'Мощность', unit: 'Вт', value_text: null, value_number: '750.000000', value_boolean: null},
    {name: 'Вес', unit: 'кг', value_text: null, value_number: '0.000000', value_boolean: null},
    {name: 'Аккумулятор', unit: null, value_text: null, value_number: null, value_boolean: false},
    {name: 'Материал', unit: null, value_text: 'Металл', value_number: null, value_boolean: null},
  ],
  components: [{name: 'Кейс', quantity: 1}, {name: 'Насадки', quantity: 2}],
  tariff: {hourly_rate: '125.50', valid_from: '2026-10-01T00:00:00Z', valid_to: null},
};

export const otherProduct: ProductDetail = {
  ...product, id: 2, name: 'Учебный пылесос', category: {id: 2, name: 'Уборка'},
  brand: null, model: null, description: 'Описание другого товара.', attributes: [], components: [], tariff: null,
};

export const pickupPoints: PickupPoint[] = [
  {id: 11, name: 'Учебный ПВ №1', address: 'Учебный адрес №1', status: 'fixture-status'},
  {id: 22, name: 'Учебный ПВ №2', address: 'Учебный адрес №2', status: 'fixture-status'},
];

export const availability: ProductAvailability = {
  product_id: 1, pickup_points: [{pickup_point_id: 11, available_count: 3}, {pickup_point_id: 22, available_count: 0}],
};

export async function mockProductApi(page: Page) {
  await page.route('**/api/auth/me/', route => route.fulfill({json: {authenticated: false, user: null}}));
  await page.route('**/api/categories/', route => route.fulfill({json: [product.category, otherProduct.category]}));
  await page.route('**/api/products/', route => route.fulfill({json: [product, otherProduct].map(item => ({...item, category: item.category.id}))}));
  await page.route(/\/api\/products\/\d+\/$/, route => {
    const id = Number(new URL(route.request().url()).pathname.split('/')[3]);
    const item = [product, otherProduct].find(value => value.id === id);
    return route.fulfill(item ? {json: item} : {status: 404, json: {detail: 'Not found.'}});
  });
  await page.route('**/api/pickup-points/', route => route.fulfill({json: pickupPoints.map(point => ({...point,
    latitude: '55.000000', longitude: '37.000000', working_hours: [],
  }))}));
  await page.route('**/api/products/*/availability/', route => {
    const id = Number(new URL(route.request().url()).pathname.split('/')[3]);
    return route.fulfill({json: {...availability, product_id: id,
      pickup_points: id === 2 ? [{pickup_point_id: 11, available_count: 7}, {pickup_point_id: 22, available_count: 1}] : availability.pickup_points,
    }});
  });
}
